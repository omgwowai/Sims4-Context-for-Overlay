"""Asynchronous, bounded in-process event views over immutable durable prefixes.

Public methods only enqueue work or decode a bounded cached page. One worker
owns source replay and derivation; it never receives a Recorder or game object.
"""

import hashlib
import queue
import threading
import time
from collections import Counter

from context_overlay import SCHEMA_VERSION
from context_overlay.history import deep_size, MIB
from context_overlay.model import new_id
from context_overlay.view_source import ViewError, decode, packed, read_prefix

VIEWS = ("records", "events", "organized", "recap")
FACETS = ("lineage", "policy", "labels", "revisions", "events", "units")
PROFILE = "recap_v1"
SCHEMA = "event_views_v1"
BOUNDARIES = {"session_start", "session_end", "zone_entry", "zone_exit"}


class CheckedList(list):
    def __init__(self, values, checkpoint):
        super().__init__(values)
        self.checkpoint = checkpoint

    def __iter__(self):
        for i, item in enumerate(super().__iter__()):
            if i % 32 == 0:
                self.checkpoint()
            yield item


class ViewStore:
    def __init__(self, path, session_id, game_version=None, max_queries=8,
                 memory_bytes=512 * MIB, source_bytes=128 * MIB, ttl=300,
                 build_seconds=120, page_bytes=512 * 1024):
        self.path, self.session_id, self.game_version = str(path), session_id, game_version
        self.max_queries, self.memory_limit, self.source_limit = max_queries, memory_bytes, source_bytes
        self.ttl, self.build_seconds, self.page_bytes = ttl, build_seconds, page_bytes
        self._lock, self._queue, self._closed = threading.RLock(), queue.Queue(), threading.Event()
        self._jobs, self._sources = {}, {}
        self._thread = threading.Thread(target=self._run, name="ContextOverlayViews", daemon=True)
        self._thread.start()

    @staticmethod
    def _page_size(value):
        if type(value) is not int or not 1 <= value <= 100:
            raise ViewError("invalid_request", "page_size must be an integer from 1 to 100")

    def query(self, view, entity_key, head, source_snapshot_id=None, profile=PROFILE, page_size=20):
        self._page_size(page_size)
        if view not in VIEWS or profile != PROFILE:
            raise ViewError("invalid_request", "Unsupported view or profile")
        if view == "recap" and not entity_key.startswith("sim:"):
            raise ViewError("invalid_request", "The recap profile currently requires a Sim")
        with self._lock:
            self._admit()
            if source_snapshot_id is not None:
                source = self._sources.get(source_snapshot_id)
                if source is None or not any(j["source"] == source_snapshot_id and self._live(j) for j in self._jobs.values()):
                    raise ViewError("source_expired", "Keep a source query open while requesting another layer")
            else:
                sequence, offset = head.get("durable_sequence"), head.get("durable_byte_offset")
                if type(offset) is not int or offset < 1 or type(sequence) is not int or sequence < 1:
                    raise ViewError("source_unavailable", "Wait for the first durable journal record")
                if offset > self.source_limit:
                    raise ViewError("view_budget", "Durable source exceeds the configured source byte budget")
                source = next((s for s in self._sources.values() if s["sequence"] == sequence and s["offset"] == offset), None)
                if source is None:
                    source = {"id": new_id(), "sequence": sequence, "offset": offset,
                              "data": None, "projections": {}, "memory": 0, "head": dict(head)}
                    self._sources[source["id"]] = source
            job = self._new_job(source["id"], view, entity_key, profile, page_size)
        return self.status(job["id"])

    def _live(self, job):
        return not job["cancel"].is_set() and time.monotonic() - job["touched"] < self.ttl

    def _admit(self):
        if self._closed.is_set():
            raise ViewError("session_closed", "Event view provider has closed")
        if len(self._jobs) >= self.max_queries:
            raise ViewError("view_limit", "Close a view request before opening another")

    def _new_job(self, source_id, view, entity, profile, page_size, explanation=None):
        job = {"id": new_id(), "source": source_id, "view": view, "entity": entity,
               "profile": profile, "page_size": page_size, "state": "building",
               "cancel": threading.Event(), "touched": time.monotonic(), "memory": 0,
               "explanation": explanation}
        self._jobs[job["id"]] = job
        self._queue.put(job)
        return job

    def _get(self, request_id):
        job = self._jobs.get(request_id) if isinstance(request_id, str) else None
        if job is None or not self._live(job):
            raise ViewError("view_expired", "Request was closed, cancelled or expired")
        job["touched"] = time.monotonic()
        return job

    def status(self, request_id):
        with self._lock:
            job = self._get(request_id)
            state = job["state"]
            result = {"schema_version": SCHEMA_VERSION, "view_schema_version": SCHEMA, "session_id": self.session_id,
                      "request_id": job["id"], "source_snapshot_id": job["source"],
                      "view": job["view"], "state": state}
            if state == "failed":
                result["error"] = dict(job["error"])
            elif state == "ready":
                result["snapshot_id"] = job["snapshot"]
                result["total_matches"] = len(job["rows"])
                result["cursor"] = job["id"] + ":0"
                result["build_ms"] = job["build_ms"]
        return result

    def page(self, cursor):
        if not isinstance(cursor, str) or len(cursor) > 100:
            raise ViewError("invalid_cursor", "Expected an event-view page cursor")
        try:
            request_id, position = cursor.split(":")
            offset = int(position)
            if str(offset) != position or offset < 0:
                raise ValueError()
        except ValueError:
            raise ViewError("invalid_cursor", "Malformed event-view page cursor") from None
        with self._lock:
            job = self._get(request_id)
            if job["state"] != "ready":
                raise ViewError("view_not_ready", "Wait for the request to become ready")
            if offset not in job["page_offsets"]:
                raise ViewError("invalid_cursor", "Cursor is not a page boundary")
            end = job["page_offsets"][offset]
            selected = job["rows"][offset:end]
            metadata = job["metadata"]
            snapshot, source_id, total = job["snapshot"], job["source"], len(job["rows"])
        # Work outside the publication lock; no disk reads, full-session copy,
        # or derivation runs here. A page's encoded bytes have a hard upper bound.
        return dict(decode(metadata), state="ready", request_id=request_id, snapshot_id=snapshot,
                    source_snapshot_id=source_id, session_id=self.session_id, offset=offset,
                    total_matches=total, cursor=cursor,
                    next_cursor=request_id + ":" + str(end) if end < total else None,
                    items=[decode(row) for row in selected])

    def explain(self, snapshot_id, item_id, facet="lineage", page_size=20):
        self._page_size(page_size)
        if facet not in FACETS or not isinstance(item_id, str):
            raise ViewError("invalid_request", "Unsupported explanation facet or item identity")
        with self._lock:
            self._admit()
            parent = next((j for j in self._jobs.values() if j.get("snapshot") == snapshot_id and self._live(j)), None)
            if parent is None:
                raise ViewError("snapshot_mismatch", "The view snapshot is unavailable in this session")
            if item_id not in parent["members"]:
                raise ViewError("item_not_in_view", "Item does not belong to the supplied view snapshot")
            job = self._new_job(parent["source"], "explanation", parent["entity"], parent["profile"], page_size,
                                {"facet": facet, "item_id": item_id, "parent_snapshot_id": snapshot_id,
                                 "members": parent["members"][item_id]})
        return self.status(job["id"])

    def close(self, request_id):
        if not isinstance(request_id, str) or len(request_id) != 32:
            raise ViewError("invalid_request", "Expected an event-view request_id")
        with self._lock:
            job = self._jobs.get(request_id)
            released = bool(job and not job["cancel"].is_set())
            if job:
                job["cancel"].set()
        # The worker reclaims source/cache objects, including on cancellation.
        return {"released": released}

    def shutdown(self, wait=False):
        self._closed.set()
        with self._lock:
            for job in self._jobs.values():
                job["cancel"].set()
        if wait:
            self._thread.join(timeout=5)

    def metrics(self):
        with self._lock:
            return {"requests": sum(self._live(j) for j in self._jobs.values()), "sources": len(self._sources),
                    "estimated_bytes": self._memory(), "memory_budget_bytes": self.memory_limit,
                    "source_budget_bytes": self.source_limit, "ttl_seconds": self.ttl,
                    "max_queries": self.max_queries, "page_budget_bytes": self.page_bytes}

    def _memory(self):
        with self._lock:
            return sum(s["memory"] for s in self._sources.values()) + sum(j["memory"] for j in self._jobs.values())

    def _prune(self):
        garbage = []
        with self._lock:
            for key, job in list(self._jobs.items()):
                if not self._live(job):
                    job["cancel"].set()
                    garbage.append(self._jobs.pop(key))
            wanted = {j["source"] for j in self._jobs.values()}
            for key in list(self._sources):
                if key not in wanted:
                    garbage.append(self._sources.pop(key))
        # Destruction of large snapshots happens on the worker without the lock.
        garbage.clear()

    def _run(self):
        while not self._closed.is_set():
            self._prune()
            try:
                job = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue
            started, yielded = time.monotonic(), [time.monotonic()]

            def checkpoint():
                now = time.monotonic()
                if job["cancel"].is_set() or self._closed.is_set():
                    raise ViewError("view_cancelled", "View construction cancelled")
                if now - started > self.build_seconds:
                    raise ViewError("view_budget", "View construction time budget exceeded")
                if now - yielded[0] >= 0.003:
                    time.sleep(0.001)
                    yielded[0] = time.monotonic()

            try:
                checkpoint()
                with self._lock:
                    source = self._sources[job["source"]]
                    available = self.memory_limit - self._memory()
                if source["data"] is None:
                    data = read_prefix(self.path, self.session_id, source["sequence"], source["offset"], checkpoint, available)
                    checkpoint()
                    with self._lock:
                        source.update(data=data, memory=data["memory_bytes"])
                rows, members, metadata = self._prepare(job, source, checkpoint)
                meta = packed(metadata)
                item_budget = self.page_bytes - len(meta)
                if item_budget <= 0:
                    raise ViewError("view_budget", "View metadata exceeds the page byte budget")
                encoded, charged, boundaries, position, used = [], deep_size(members) + len(meta), {}, 0, 0
                for row in rows:
                    checkpoint()
                    raw = packed(row)
                    if len(raw) > item_budget:
                        raise ViewError("view_budget", "One item exceeds the page byte budget; narrow fields are not silently substituted")
                    if len(encoded) - position >= job["page_size"] or used + len(raw) > item_budget:
                        boundaries[position] = len(encoded)
                        position, used = len(encoded), 0
                    encoded.append(raw)
                    used += len(raw)
                    charged += len(raw) + 80
                    if self._memory() + charged > self.memory_limit:
                        raise ViewError("view_budget", "View cache memory budget exceeded")
                boundaries[position] = len(encoded)
                charged += deep_size(boundaries)
                if self._memory() + charged > self.memory_limit:
                    raise ViewError("view_budget", "View cache memory budget exceeded")
                identity = {"source": source["data"]["sha256"], "sequence": source["sequence"],
                            "entity": job["entity"], "view": job["view"], "profile": job["profile"] if job["view"] in ("organized", "recap", "explanation") else None,
                            "rules": metadata.get("rules"), "explanation": job["explanation"], "schema": SCHEMA}
                snapshot = hashlib.sha256(packed(identity)).hexdigest()
                checkpoint()
                with self._lock:
                    job.update(state="ready", rows=tuple(encoded), members=members, metadata=meta, page_offsets=boundaries,
                               snapshot=snapshot, memory=charged, build_ms=round((time.monotonic() - started) * 1000, 3))
            except Exception as exc:
                with self._lock:
                    job.update(state="failed", error={"code": exc.code if isinstance(exc, ViewError) else "view_build_failed", "message": str(exc)})
            finally:
                self._queue.task_done()
                # Do not keep the last completed/failed job alive during idle time.
                job = source = data = rows = members = encoded = None
        self._prune()

    def _projection(self, source, entity, checkpoint):
        cached = source["projections"].get(entity)
        if cached is not None:
            return decode(cached)
        from context_overlay.experience.experience_recap import build_recap
        data = source["data"]
        # Reserve for temporary organization/copies before entering the core.
        if self._memory() + 2 * data["latest_event_bytes"] > self.memory_limit:
            raise ViewError("view_budget", "Insufficient memory for experience construction")
        loaded = {"complete": True, "event_scope": "all", "session_id": self.session_id,
                  "sha256": data["sha256"], "events": CheckedList(data["events"].values(), checkpoint)}
        bundle = build_recap(loaded, entity, self.game_version)
        checkpoint()
        cached = packed(bundle)
        size = len(cached) + 128
        if self._memory() + size > self.memory_limit:
            raise ViewError("view_budget", "Experience projection exceeds memory budget")
        with self._lock:
            source["projections"][entity] = cached
            source["memory"] += size
        return bundle

    def _prepare(self, job, source, checkpoint):
        data, entity = source["data"], job["entity"]
        events, records = data["events"], data["records"]
        selected = events.selected(entity)
        if not selected and not (job["view"] in ("records", "explanation") and
                                 any(entity in keys for _, _, keys in data["observations"])):
            raise ViewError("entity_not_recorded", "Entity has no recorded events at this durable source cutoff")
        metadata = {"schema_version": SCHEMA_VERSION, "view_schema_version": SCHEMA, "view": job["view"],
                    "scope": {"source": "durable_session", "entity_key": entity, "membership": "entity_index_not_participation_or_knowledge",
                              "as_of_sequence": source["sequence"], "source_sha256": data["sha256"],
                              "source_byte_offset": source["offset"], "include_internal": True,
                              "time_window": "whole_durable_session", "selected_latest_events": len(selected)},
                    "coverage": {"source_integrity": "validated_contiguous_prefix", "recording": source["head"],
                                 "source_scope": "recorded_callbacks_not_all_game_activity", "fifo_applied": False,
                                 "source_latest_events": len(events), "source_records": len(records)}}
        members = {}
        if job["view"] == "records":
            sequences = {seq for key in selected for seq in data["revisions"][key]}
            auxiliary = [seq for seq, category, keys in data["observations"] if category in BOUNDARIES or entity in keys]
            sequences.update(auxiliary)
            metadata["scope"].update(record_membership="full_revision_chains_plus_related_observations_and_shared_boundaries",
                                      event_revision_records=sum(len(data["revisions"][key]) for key in selected), auxiliary_records=len(auxiliary))
            for seq in sorted(sequences):
                checkpoint()
                record = decode(records[seq])
                members["record:" + str(seq)] = {"events": [record["event"]["event_id"]] if record["kind"] == "event_revision" else [], "units": [], "records": [seq]}
            rows = ({"item_id": "record:" + str(seq), "record": decode(records[seq])} for seq in sorted(sequences))
            return rows, members, metadata
        if job["view"] == "events":
            members = {key: {"events": [key], "units": []} for key in selected}
            return ({"item_id": key, "event": events[key]} for key in selected), members, metadata
        explanation = job["explanation"]
        if explanation and not explanation["members"]["units"] and (
                not explanation["members"]["events"] or explanation["facet"] in ("events", "revisions", "lineage")):
            related = explanation["members"]
            keys = set(related["events"])
            if explanation["facet"] == "events":
                rows = (events[key] for key in events if key in keys)
            elif explanation["facet"] == "revisions":
                sequences = set(related.get("records", [])) | {seq for key in keys for seq in data["revisions"][key]}
                rows = (decode(records[seq]) for seq in sorted(sequences))
            else:
                rows = ({"event_id": key, "revision": events[key]["revision"], "record_sequences": data["revisions"][key]} for key in events if key in keys)
            metadata["explanation"] = {k: v for k, v in explanation.items() if k != "members"}
            return rows, {}, metadata
        bundle = self._projection(source, entity, checkpoint)
        audit, units = bundle["audit"]["evidence"], bundle["units"]
        metadata["rules"] = bundle["manifest"]["implementation_sha256"]
        metadata["coverage"].update(supporting_events=sum(r["outside_entity_index"] for r in audit.values()),
                                    source_events_without_units=sum(not r["units"] for r in audit.values()))

        def membership(ids):
            refs = {ref for uid in ids for ref in units[uid]["evidence"]}
            return {"units": list(ids), "events": [r["event_id"] for ref, r in audit.items() if ref in refs]}

        if job["view"] == "organized":
            rows = []
            for uid, unit in units.items():
                checkpoint()
                members[uid] = membership([uid])
                rows.append({"item_id": uid, "kind": "unit", "lane": bundle["ledger"][uid]["lane"], "unit": unit})
            for ref, row in audit.items():
                if not row["units"]:
                    identifier = "standalone:" + row["event_id"]
                    members[identifier] = {"units": [], "events": [row["event_id"]]}
                    rows.append({"item_id": identifier, "kind": "standalone", "event_id": row["event_id"],
                                 "revision": row["revision"], "category": row["semantic_role"],
                                 "recap_disposition": row["disposition"], "evidence_ref": ref})
            metadata["scope"]["all_input_events_have_membership_or_standalone"] = True
            accounted = {key for value in members.values() for key in value["events"]}
            if not set(selected) <= accounted or accounted != {row["event_id"] for row in audit.values()}:
                raise ViewError("view_build_failed", "Organization has an unaccounted source event")
            return rows, members, metadata
        if job["view"] == "recap":
            rows = []
            sections = ("activities", "results", "relationship_observations", "states", "review_actions")
            for section in sections:
                for row in bundle["recap"][section]:
                    refs = [row["ref"]] if "ref" in row else row.get("refs", [r["ref"] for r in row.get("intervals", [])])
                    ids = [uid for ref in refs for uid in bundle["routes"][ref]]
                    identifier = "recap:" + section + ":" + hashlib.sha256(packed(ids)).hexdigest()[:24]
                    members[identifier] = membership(ids)
                    # Short rN references are aliases inside this snapshot only.
                    for ref in refs:
                        members[ref] = membership(bundle["routes"][ref])
                    rows.append({"item_id": identifier, "section": section, "value": row})
            metadata["recap"] = {k: v for k, v in bundle["recap"].items() if k not in sections}
            metadata["scope"]["profile"] = PROFILE
            return rows, members, metadata
        explanation = job["explanation"]
        related = explanation["members"]
        ids, event_ids = set(related["units"]), set(related["events"])
        if not ids:
            ids.update(uid for row in audit.values() if row["event_id"] in event_ids for uid in row["units"])
        # Explaining an activity also includes explicitly attached results,
        # state transitions and decision evidence, never unrelated dependencies.
        roots = set(ids)
        ids.update(uid for uid, unit in units.items() if unit.get("activity") in roots or any(x["activity"] in roots for x in unit.get("activity_links", [])))
        ids.update(key for uid in list(ids) for key in units[uid].get("decisions", []))
        event_ids.update(membership(ids)["events"])
        rows, facet = [], explanation["facet"]
        evidence = {ref: row for ref, row in audit.items() if row["event_id"] in event_ids}
        if facet == "lineage":
            rows = [dict(row, evidence_ref=ref, record_sequences=data["revisions"][row["event_id"]]) for ref, row in evidence.items()]
        elif facet == "policy":
            rows = [dict(bundle["ledger"][uid], unit_id=uid) for uid in sorted(ids)]
            rows += [dict(row, evidence_ref=ref) for ref, row in evidence.items() if not row["units"]]
        elif facet == "labels":
            rows = [row for row in bundle["audit"]["labels"] if row["unit"] in ids]
        elif facet == "units":
            rows = [units[uid] for uid in sorted(ids)]
        elif facet == "events":
            rows = [events[key] for key in events if key in event_ids]
        elif facet == "revisions":
            sequences = set(related.get("records", [])) | {seq for key in event_ids for seq in data["revisions"][key]}
            rows = (decode(records[seq]) for seq in sorted(sequences))
        metadata["explanation"] = {k: v for k, v in explanation.items() if k != "members"}
        return rows, {}, metadata
