"""Entity indexes and bounded, immutable-version query snapshots.

Only the simulation thread publishes and queries. Published event dictionaries
are private; callers receive detached copies, including for cursor pages.
"""

import bisect
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from collections import OrderedDict

from context_overlay.model import copy_data, new_id, utc_now


MIB = 1024 * 1024
DEFAULT_EVENT_CAPACITY = 200000
DEFAULT_MEMORY_BYTES = 1536 * MIB


def copy_events(events, durable_sequence):
    records = copy_data(events)
    for record in records:
        for item in [record] + record.get("effects", []):
            item["persistence"] = "written" if item["accepted_sequence"] <= durable_sequence else "accepted"
    return records


class HistoryError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(code + ": " + message)


def deep_size(value):
    """Conservative per-event Python allocation estimate (not process RSS)."""
    seen, pending, total = set(), [value], 0
    while pending:
        item = pending.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            pending.extend(item)
    return total


def ticks(value):
    if value is None:
        return None
    raw = value.get("ticks") if isinstance(value, dict) else value
    if isinstance(raw, bool) or not isinstance(raw, (int, str)):
        raise HistoryError("invalid_query", "Game time must be integer ticks")
    try:
        return int(raw)
    except ValueError:
        raise HistoryError("invalid_query", "Game time must be integer ticks")


def validate_entity(key):
    if not isinstance(key, str):
        raise HistoryError("invalid_query", "Expected sim:<id> or object:<id>")
    parts = key.split(":")
    if len(parts) != 2 or parts[0] not in ("sim", "object") or not parts[1].isdigit() or int(parts[1]) <= 0:
        raise HistoryError("invalid_query", "Expected sim:<id> or object:<id>")


class EntityIndex:
    __slots__ = ("by_id", "by_time")

    def __init__(self):
        self.by_id = OrderedDict()
        self.by_time = []

    def add(self, event_id, sort_key):
        if event_id not in self.by_id:
            if not self.by_time or sort_key > self.by_time[-1]:
                self.by_time.append(sort_key)
            else:
                bisect.insort_right(self.by_time, sort_key)
            self.by_id[event_id] = sort_key
        self.by_id.move_to_end(event_id)

    def remove(self, event_id):
        sort_key = self.by_id.pop(event_id)
        position = bisect.bisect_left(self.by_time, sort_key)
        if position < len(self.by_time) and self.by_time[position] == sort_key:
            self.by_time.pop(position)


class HistoryIndex:
    def __init__(self, session_id, capacity=DEFAULT_EVENT_CAPACITY,
                 memory_bytes=DEFAULT_MEMORY_BYTES, snapshot_limit=8,
                 snapshot_refs=100000, snapshot_bytes=256 * MIB,
                 snapshot_ttl=120, clock=None):
        self.session_id = session_id
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("Event capacity must be a positive integer")
        self.capacity = capacity
        self.memory_limit = memory_bytes
        self.events = OrderedDict()
        self.fifo = OrderedDict()
        self.evicted = 0
        self.last_evicted_time = None
        self.entities = {}
        self.global_index = EntityIndex()
        self.producers = {}
        self.last_evicted_sequence = 0
        self._checkpoint_secret = os.urandom(32)
        self._keys = {}
        self._charges = {}
        self.memory_bytes = 0
        self.reference_count = 0
        self.snapshot_limit = snapshot_limit
        self.snapshot_ref_limit = snapshot_refs
        self.snapshot_byte_limit = snapshot_bytes
        self.snapshot_ttl = snapshot_ttl
        self._clock = clock or time.monotonic
        self._snapshots = {}
        self._snapshot_refs = 0
        self._snapshot_bytes = 0
        self.closed = False

    def prepare(self, event):
        if self.closed:
            raise HistoryError("session_closed", "Recording run is closed")
        previous = self.events.get(event["event_id"])
        if previous and event["revision"] <= previous["revision"]:
            raise HistoryError("invalid_revision", "Event revisions must increase")
        event = copy_data(event)
        event.setdefault("origin", "external" if event["event_type"] == "external_event" else "game")
        event.setdefault("producer", None)
        event.pop("accepted_sequence", None)
        if previous:
            event["first_observed_time"] = previous["first_observed_time"]
            event["entities"] = list(dict.fromkeys(previous["entities"] + event["entities"]))
        else:
            event["first_observed_time"] = event.get("first_observed_time", event.get("last_observed_time"))
            event["entities"] = list(dict.fromkeys(event["entities"]))
        if ticks(event["first_observed_time"]) is None:
            raise HistoryError("invalid_event", "An event needs an observed time")
        for key in event["entities"]:
            validate_entity(key)
        # Includes a conservative allowance for map entries, sort tuples and
        # accepted_sequence; repeated shared strings are charged independently.
        charge = deep_size(event) + 1280 + 512 * len(event["entities"])
        projected = self.memory_bytes - self._charges.get(event["event_id"], 0) + charge
        if previous is None and len(self.events) >= self.capacity:
            projected -= self._charges[next(iter(self.fifo))]
        if projected > self.memory_limit:
            raise HistoryError("history_memory", "Estimated history memory budget reached; recording paused")
        return event, charge

    def eviction_for(self, event_id):
        if event_id not in self.events and len(self.events) >= self.capacity:
            return next(iter(self.fifo))
        return None

    def remove(self, event_id):
        event = self.events.pop(event_id)
        self.global_index.remove(event_id)
        if event.get("producer") in self.producers:
            producer_index = self.producers[event["producer"]]
            producer_index.remove(event_id)
            if not producer_index.by_id:
                del self.producers[event["producer"]]
        self.fifo.pop(event_id)
        self._keys.pop(event_id)
        self.memory_bytes -= self._charges.pop(event_id)
        for key in event["entities"]:
            index = self.entities[key]
            index.remove(event_id)
            self.reference_count -= 1
            if not index.by_id:
                del self.entities[key]
        self.evicted += 1
        self.last_evicted_time = event["first_observed_time"]
        self.last_evicted_sequence = max(self.last_evicted_sequence, event["accepted_sequence"])
        return event

    def publish(self, event, sequence, charge):
        """Publish only after the journal has accepted the revision."""
        event_id = event["event_id"]
        discarded_id = self.eviction_for(event_id)
        discarded = self.remove(discarded_id) if discarded_id else None
        self.fifo.setdefault(event_id, None)
        event["accepted_sequence"] = sequence
        self.memory_bytes += charge - self._charges.get(event_id, 0)
        self._charges[event_id] = charge
        if event_id not in self._keys:
            self._keys[event_id] = (ticks(event["first_observed_time"]), sequence, event_id)
        self.global_index.add(event_id, self._keys[event_id])
        if event.get("producer"):
            self.producers.setdefault(event["producer"], EntityIndex()).add(event_id, self._keys[event_id])
        for key in event["entities"]:
            index = self.entities.get(key)
            if index is None:
                index = self.entities[key] = EntityIndex()
            if event_id not in index.by_id:
                self.reference_count += 1
            index.add(event_id, self._keys[event_id])
        self.events[event_id] = event
        self.events.move_to_end(event_id)
        return discarded

    @staticmethod
    def parent_id(event):
        return (event.get("cause") or {}).get("event_id")

    @classmethod
    def grouped(cls, events):
        by_id = {event["event_id"]: event for event in events}
        children = {}
        for event in events:
            parent = cls.parent_id(event)
            if parent in by_id and by_id[parent]["event_type"] == "interaction":
                children.setdefault(parent, []).append(event)
        attached = {event["event_id"] for values in children.values() for event in values}
        result = []
        for event in events:
            if event["event_id"] in attached:
                continue
            if event["event_id"] in children:
                event = dict(event, effects=children[event["event_id"]])
            result.append(event)
        return result

    def recent(self, entity_key, limit, include_internal, group_effects=False, origins=None, producers=None):
        origins, producers = self.source_filters(origins, producers)
        index = self.entities.get(entity_key)
        if index is None:
            return [], False
        # Group at most 500 candidates; flat queries need one extra to detect truncation.
        candidate_limit = 500 if group_effects else limit + 1
        selected = []
        for event_id in reversed(index.by_id):
            event = self.events[event_id]
            if (include_internal or event["tier"] == "main") and self.source_matches(event, origins, producers):
                selected.append(event)
                if len(selected) == candidate_limit:
                    break
        if group_effects:
            grouped = self.grouped(selected)
            return grouped[:limit], len(grouped) > limit or len(index.by_id) > len(selected)
        return selected[:limit], len(selected) > limit

    def prune(self):
        now = self._clock()
        for query_id, snapshot in tuple(self._snapshots.items()):
            if now >= snapshot["expires"]:
                self._drop(query_id)

    def _drop(self, query_id):
        snapshot = self._snapshots.pop(query_id)
        self._snapshot_refs -= snapshot["refs"]
        self._snapshot_bytes -= snapshot["charge"]

    def close(self):
        self.closed = True
        self._snapshots.clear()
        self._snapshot_refs = self._snapshot_bytes = 0

    def status(self):
        self.prune()
        return {"entities": len(self.entities), "event_references": self.reference_count,
                "estimated_memory_bytes": self.memory_bytes, "memory_budget_bytes": self.memory_limit,
                "event_capacity": self.capacity, "snapshots": len(self._snapshots),
                "retention_policy": "fifo_first_accepted", "evicted_events": self.evicted,
                "last_evicted_time": self.last_evicted_time,
                "last_evicted_sequence": self.last_evicted_sequence,
                "snapshot_references": self._snapshot_refs, "snapshot_charged_bytes": self._snapshot_bytes,
                "snapshot_memory_budget_bytes": self.snapshot_byte_limit}

    @staticmethod
    def _values(value, name, allowed=None):
        if value is None:
            return None
        if not isinstance(value, (list, tuple)) or not value or len(value) > 64 or any(not isinstance(v, str) or not v for v in value):
            raise HistoryError("invalid_query", name + " must be a nonempty list of at most 64 strings")
        values = set(value)
        if len(values) != len(value):
            raise HistoryError("invalid_query", name + " must contain distinct values")
        if allowed is not None and not values <= allowed:
            raise HistoryError("invalid_query", "Unsupported " + name)
        return values

    @classmethod
    def source_filters(cls, origins, producers):
        origins = cls._values(origins, "origins", {"game", "external"})
        producers = cls._values(producers, "producers")
        if producers and any(len(v) > 128 or any(ord(c) < 33 or ord(c) > 126 for c in v) for v in producers):
            raise HistoryError("invalid_query", "producer names must be 1-128 printable ASCII characters without spaces")
        return origins, producers

    @staticmethod
    def source_matches(event, origins, producers):
        return ((origins is None or event.get("origin", "game") in origins)
                and (producers is None or event.get("producer") in producers))

    def _indexes(self, entity_key, producers):
        if entity_key is not None:
            index = self.entities.get(entity_key)
            return [index] if index is not None else []
        if producers is not None:
            return [self.producers[p] for p in sorted(producers) if p in self.producers]
        return [self.global_index]

    def _validate_page(self, entity_key, page_size, include_internal):
        if self.closed:
            raise HistoryError("session_closed", "Recording run is closed")
        if entity_key is not None:
            validate_entity(entity_key)
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 500:
            raise HistoryError("invalid_query", "Page size must be between 1 and 500")
        if not isinstance(include_internal, bool):
            raise HistoryError("invalid_query", "include_internal must be a boolean")
        self.prune()
        if len(self._snapshots) >= self.snapshot_limit:
            raise HistoryError("query_limit", "Close a query or wait for expiry")

    def _check_budget(self, count, charged):
        if count + self._snapshot_refs > self.snapshot_ref_limit or charged + self._snapshot_bytes > self.snapshot_byte_limit:
            raise HistoryError("query_budget", "Narrow the filters or close other queries")

    def _freeze(self, rows, metadata, entity_key, filters, page_size, examined, charged,
                group_effects=False, checkpoint=None):
        refs = len(rows)
        versions = tuple(self.grouped(rows) if group_effects else rows)
        charged += deep_size(filters) + (512 * len(versions) if group_effects else 0)
        if checkpoint:
            charged += sys.getsizeof(checkpoint) + 256
        self._check_budget(refs, charged)
        query_id = new_id()
        self._snapshots[query_id] = {"events": versions, "refs": refs, "charge": charged,
            "expires": self._clock() + self.snapshot_ttl, "page_size": page_size, "entity_key": entity_key,
            "metadata": copy_data(metadata), "created_at": utc_now(), "examined": examined,
            "filters": filters, "checkpoint": checkpoint}
        self._snapshot_refs += refs
        self._snapshot_bytes += charged
        return self._page(query_id, 0)

    def query(self, entity_key, metadata, page_size=50, include_internal=False,
              time_field="first_observed", from_ticks=None, to_ticks=None,
              event_types=None, fields=None, outcomes=None, tuning_ids=None, order="desc", group_effects=False,
              origins=None, producers=None):
        self._validate_page(entity_key, page_size, include_internal)
        if not isinstance(group_effects, bool):
            raise HistoryError("invalid_query", "group_effects must be a boolean")
        if time_field not in ("first_observed", "started", "ended") or order not in ("asc", "desc"):
            raise HistoryError("invalid_query", "Unsupported time field or order")
        lower, upper = ticks(from_ticks), ticks(to_ticks)
        if lower is not None and upper is not None and lower >= upper:
            raise HistoryError("invalid_query", "Time range is [from, to), with from < to")
        types = self._values(event_types, "event_types", {"interaction", "state_change", "game_event", "external_event"})
        origins, producers = self.source_filters(origins, producers)
        names = self._values(fields, "fields")
        results = self._values(outcomes, "outcomes", {"completed", "cancelled", "failed", "unknown"})
        tunings = self._values(tuning_ids, "tuning_ids")
        self.prune()
        if len(self._snapshots) >= self.snapshot_limit:
            raise HistoryError("query_limit", "Close a query or wait for expiry")
        rows, charged, examined = [], deep_size(metadata) + 1024, 0
        if charged + self._snapshot_bytes > self.snapshot_byte_limit:
            raise HistoryError("query_budget", "Query metadata exceeds snapshot budget")
        for index in self._indexes(entity_key, producers):
            if time_field == "first_observed":
                left = 0 if lower is None else bisect.bisect_left(index.by_time, (lower,))
                right = len(index.by_time) if upper is None else bisect.bisect_left(index.by_time, (upper,))
                candidates = (index.by_time[i] for i in range(left, right))
            else:
                candidates = iter(index.by_time)
            for key in candidates:
                examined += 1
                event = self.events[key[2]]
                observed = key[0] if time_field == "first_observed" else ticks(event.get(time_field + "_time"))
                if observed is None or (lower is not None and observed < lower) or (upper is not None and observed >= upper):
                    continue
                if not include_internal and event["tier"] != "main":
                    continue
                if not self.source_matches(event, origins, producers):
                    continue
                if types and event["event_type"] not in types:
                    continue
                if names and event.get("field") not in names:
                    continue
                if results and event.get("outcome") not in results:
                    continue
                if tunings and event.get("facts", {}).get("tuning_id") not in tunings:
                    continue
                charged += self._charges[event["event_id"]] + 64
                if len(rows) + 1 + self._snapshot_refs > self.snapshot_ref_limit or charged + self._snapshot_bytes > self.snapshot_byte_limit:
                    raise HistoryError("query_budget", "Narrow the time/type filters or close other queries")
                rows.append(event)
        rows.sort(key=lambda event: self._keys[event["event_id"]] if time_field == "first_observed" else (
            ticks(event[time_field + "_time"]), self._keys[event["event_id"]][1], event["event_id"]))
        if order == "desc":
            rows.reverse()
        filters = {"time_field": time_field, "from_ticks": str(lower) if lower is not None else None,
                                "to_ticks": str(upper) if upper is not None else None, "order": order,
                                "include_internal": include_internal, "group_effects": group_effects, "event_types": sorted(types) if types else None,
                                "fields": sorted(names) if names else None, "outcomes": sorted(results) if results else None,
                                "tuning_ids": sorted(tunings) if tunings else None,
                                "origins": sorted(origins) if origins else None,
                                "producers": sorted(producers) if producers else None}
        return self._freeze(rows, metadata, entity_key, filters, page_size, examined, charged, group_effects)

    def _checkpoint(self, sequence, filters):
        data = json.dumps([self.session_id, sequence, filters], separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(data).decode("ascii") + "." + hmac.new(
            self._checkpoint_secret, data, hashlib.sha256).hexdigest()

    def checkpoint_data(self, checkpoint):
        try:
            if not isinstance(checkpoint, str) or len(checkpoint) > 24576:
                raise ValueError()
            encoded, signature = checkpoint.split(".")
            data = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
            session, sequence, filters = json.loads(data.decode("utf-8"))
            if session != self.session_id:
                raise HistoryError("session_changed", "Checkpoint belongs to another run")
            if not hmac.compare_digest(signature, hmac.new(self._checkpoint_secret, data, hashlib.sha256).hexdigest()):
                raise ValueError()
            return sequence, filters
        except HistoryError:
            raise
        except (ValueError, TypeError, UnicodeError, RecursionError):
            raise HistoryError("invalid_checkpoint", "Use an unchanged checkpoint returned by the provider") from None

    def changes(self, metadata, checkpoint=None, start=None, entity_key=None, origins=None,
                producers=None, include_internal=None, page_size=50):
        upper = metadata["coverage"]["persistence"]["accepted_sequence"]
        if checkpoint is not None:
            if any(v is not None for v in (start, entity_key, origins, producers, include_internal)):
                raise HistoryError("invalid_query", "Checkpoint already binds initialization and filters")
            lower, filters = self.checkpoint_data(checkpoint)
            entity_key, origins, producers, include_internal = (filters[k] for k in (
                "entity_key", "origins", "producers", "include_internal"))
            if self.last_evicted_sequence > lower:
                raise HistoryError("history_gap", "Events after checkpoint were evicted; reinitialize with retained or now (conservative across all sources)")
        else:
            start = "now" if start is None else start
            if start not in ("now", "retained"):
                raise HistoryError("invalid_query", "start must be now or retained")
            lower = upper if start == "now" else 0
            include_internal = False if include_internal is None else include_internal
            origins, producers = self.source_filters(origins, producers)
            filters = {"entity_key": entity_key, "origins": sorted(origins) if origins else None,
                       "producers": sorted(producers) if producers else None, "include_internal": include_internal}
        self._validate_page(entity_key, page_size, include_internal)
        rows, charged, examined = [], deep_size(metadata) + 1024, 0
        for index in self._indexes(entity_key, producers):
            for event_id in reversed(index.by_id):
                event = self.events[event_id]
                examined += 1
                if event["accepted_sequence"] <= lower:
                    break
                if (not include_internal and event["tier"] != "main") or not self.source_matches(event, origins, producers):
                    continue
                charged += self._charges[event_id] + 64
                self._check_budget(len(rows) + 1, charged)
                rows.append(event)
        rows.sort(key=lambda event: event["accepted_sequence"])
        metadata = dict(metadata, change_range={"after_sequence": lower, "through_sequence": upper,
            "revision_policy": "latest_per_event", "initialization": start if checkpoint is None else None,
            "gap_detection": "conservative_all_sources"})
        metadata["target"] = {"key": entity_key} if entity_key else None
        return self._freeze(rows, metadata, entity_key, filters, page_size, examined, charged + 512,
                            checkpoint=self._checkpoint(upper, filters))

    def _cursor(self, query_id, offset):
        return "{}:{}:{}".format(self.session_id, query_id, offset)

    def _decode(self, cursor):
        if not isinstance(cursor, str) or len(cursor) > 200:
            raise HistoryError("invalid_cursor", "Invalid history cursor")
        parts = cursor.split(":")
        if len(parts) != 3 or len(parts[1]) != 32 or any(c not in "0123456789abcdef" for c in parts[1]) or not parts[2].isdigit():
            raise HistoryError("invalid_cursor", "Invalid history cursor")
        if parts[0] != self.session_id:
            raise HistoryError("session_changed", "Cursor belongs to another recording run")
        if self.closed:
            raise HistoryError("session_closed", "Recording run is closed")
        self.prune()
        if parts[1] not in self._snapshots:
            raise HistoryError("cursor_expired", "Query expired or was explicitly closed")
        return parts[1], int(parts[2])

    def next_page(self, cursor):
        query_id, offset = self._decode(cursor)
        return self._page(query_id, offset)

    def release(self, cursor):
        query_id, _ = self._decode(cursor)
        self._drop(query_id)

    def _page(self, query_id, offset):
        snapshot = self._snapshots[query_id]
        versions, size = snapshot["events"], snapshot["page_size"]
        if offset % size or (offset >= len(versions) and offset != 0):
            raise HistoryError("invalid_cursor", "Cursor offset is outside this query")
        end = min(offset + size, len(versions))
        result = copy_data(snapshot["metadata"])
        records = copy_events(versions[offset:end], result["coverage"]["persistence"]["durable_sequence"])
        result.update({"events": records, "scope": "current_session_query_snapshot",
                       "entity_key": snapshot["entity_key"], "filters": copy_data(snapshot["filters"]),
                       "query_id": query_id, "cursor": self._cursor(query_id, offset),
                       "next_cursor": self._cursor(query_id, end) if end < len(versions) else None,
                       "has_more": end < len(versions), "total_matches": len(versions),
                       "page_size": size, "offset": offset, "created_at": snapshot["created_at"],
                       "expires_in_seconds": max(0, snapshot["expires"] - self._clock()),
                       "as_of_sequence": result["coverage"]["persistence"].get("accepted_sequence", 0),
                       "candidates_examined": snapshot["examined"],
                       "persistence_as_of": "query_creation"})
        if snapshot.get("checkpoint"):
            result["scope"] = "current_session_change_snapshot"
            result["checkpoint"] = snapshot["checkpoint"] if not result["has_more"] else None
        return result
