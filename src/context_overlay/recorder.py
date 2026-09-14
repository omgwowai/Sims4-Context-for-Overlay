"""Canonical event revisions and bounded recent history."""

from context_overlay.history import DEFAULT_EVENT_CAPACITY, DEFAULT_MEMORY_BYTES, HistoryError, HistoryIndex
from context_overlay.model import copy_data, envelope, new_id, outcome, utc_now
from context_overlay.storage import StorageError


class Recorder:
    def __init__(self, journal, session_id=None, capacity=DEFAULT_EVENT_CAPACITY, enabled=True,
                 memory_bytes=DEFAULT_MEMORY_BYTES, **query_options):
        self.journal = journal
        self.session_id = session_id or new_id()
        self.capacity = capacity
        self.enabled = enabled
        self.paused = False
        self.error = None
        self.index = HistoryIndex(self.session_id, capacity, memory_bytes, **query_options)
        self.events = self.index.events
        self.evicted = 0
        self.started_at = utc_now()
        self._samples = {}
        self._scopes = {}
        self._relationship_bits = {}

    def fail(self, message):
        self.paused = True
        self.error = str(message)

    def status(self):
        persistence = self.journal.status()
        if persistence.get("error"):
            self.fail(persistence["error"])
        return {"state": "disabled" if not self.enabled else ("failed" if self.paused else "recording"),
                "error": self.error, "session_id": self.session_id,
                "started_at": self.started_at, "retained_events": len(self.events),
                "evicted_events": self.evicted, "persistence": persistence,
                "history_index": self.index.status()}

    def _write(self, record):
        if not self.enabled or self.paused:
            return None
        try:
            return self.journal.append(record)
        except StorageError as exc:
            self.fail(exc)
            return None

    def note(self, category, data, game_time=None):
        record = envelope("observation", self.session_id)
        record.update({"observation_id": new_id(), "category": category,
                       "game_time": game_time, "data": copy_data(data)})
        return self._write(record)

    def _save(self, event):
        if not self.enabled or self.paused:
            return None
        try:
            event, charge = self.index.prepare(event)
        except HistoryError as exc:
            self.note("recording_error", {"error": str(exc)})
            self.fail(exc)
            return None
        record = envelope("event_revision", self.session_id)
        record["event"] = event
        sequence = self._write(record)
        if sequence is None:
            return None
        self.index.publish(event, sequence, charge)
        return event

    def interaction(self, phase, facts, game_time, source):
        if not self.enabled or self.paused:
            return None
        actor = facts["actor"]
        interaction_id = str(facts["interaction_id"])
        event_id = "{}:interaction:{}:{}".format(self.session_id, actor["id"], interaction_id)
        previous = self.events.get(event_id)
        if previous and any(item["phase"] == phase and item["source"] == source for item in previous["observations"]):
            return previous
        if previous is None:
            event = {"event_id": event_id, "revision": 0, "event_type": "interaction",
                     "first_observed_time": game_time, "started_time": None,
                     "ended_time": None, "stage": "unknown", "outcome": "unknown",
                     "observations": [], "entities": [actor["key"]]}
        else:
            event = copy_data(previous)
            event.pop("accepted_sequence", None)
        event["facts"] = copy_data(facts)
        if previous is not None and previous["stage"] == "ended" and phase != "exited":
            # A delayed non-terminal observation cannot erase terminal evidence.
            event["facts"]["finishing_type"] = previous["facts"].get("finishing_type")
        target = facts.get("target")
        if target and target["key"] not in event["entities"]:
            event["entities"].append(target["key"])
        event["tier"] = facts.get("tier", "internal")
        event["revision"] += 1
        event["last_observed_time"] = game_time
        if phase == "queued" and event["stage"] == "unknown":
            event["stage"] = "queued"
        elif phase == "started":
            if event["stage"] != "ended":
                event["stage"] = "triggered" if facts.get("immediate") else "running"
                if event["started_time"] is None:
                    event["started_time"] = game_time
        elif phase == "observed_running" and event["stage"] not in ("running", "ended"):
            event["stage"] = "running"
        elif phase == "exited":
            event["stage"] = "ended"
            event["ended_time"] = game_time
            event["outcome"] = outcome(facts.get("finishing_type"), True)
        event["observations"].append({"id": new_id(), "phase": phase, "source": source,
                                      "game_time": game_time, "recorded_at": utc_now()})
        return self._save(event)

    def change(self, entities, field_name, before, after, game_time, source, interval=None, metadata=None):
        if before == after:
            return None
        event = {"event_id": self.session_id + ":change:" + new_id(), "revision": 1,
                 "event_type": "state_change", "tier": "main",
                 "entities": [item["key"] for item in entities],
                 "participants": copy_data(entities), "field": field_name,
                 "before": copy_data(before), "after": copy_data(after),
                 "last_observed_time": game_time, "source": source,
                 "interval": copy_data(interval),
                 "evidence_type": "sample_difference" if interval else "notification"}
        if metadata:
            event["metadata"] = copy_data(metadata)
        return self._save(event)

    def relationship_bit(self, actor, other, bit, added, game_time, source, bidirectional):
        keys = (actor["key"], other["key"])
        pair = tuple(sorted(keys)) if bidirectional else keys
        identity = pair + (bit["id"], bidirectional)
        previous = self._relationship_bits.get(identity)
        if previous is not None and previous[0] == added:
            self.note("relationship_bit_duplicate", {"event_id": previous[1], "reporting_actor": actor,
                                                      "source": source}, game_time)
            return self.events.get(previous[1])
        if identity not in self._relationship_bits and len(self._relationship_bits) >= self.capacity:
            self.fail("Relationship identity capacity reached; recording paused")
            return None
        event = self.change([actor, other], "relationship.bits", None if added else bit, bit if added else None,
                            game_time, source, metadata={"bidirectional": bidirectional, "reporting_actor": actor})
        if event is not None:
            self._relationship_bits[identity] = (added, event["event_id"])
        return event

    def sample(self, target, values, game_time, related=None):
        key = target["key"]
        current = copy_data(values)
        previous = self._samples.get(key)
        if self.note("sample_baseline" if previous is None else "state_sample",
                     {"target": target, "values": current, "related": related or {}}, game_time) is None:
            return
        if previous is None:
            self._samples[key] = (game_time, current)
            return
        previous_time, previous_values = previous
        for field_name, value in current.items():
            if field_name in previous_values:
                participants = [target] + (related or {}).get(field_name, [])
                label = "relationships." + field_name.rsplit(".", 1)[-1] if field_name.startswith("relationship.") else field_name
                self.change(participants, label, previous_values[field_name], value,
                            game_time, "continuous_sample",
                            {"from": previous_time, "to": game_time})
        if not self.paused:
            self._samples[key] = (game_time, current)

    def _scope_change(self, target, game_time, entering):
        if not self.enabled or self.paused:
            return
        key = target["key"]
        if key not in self._scopes and len(self._scopes) >= self.capacity:
            self.note("recording_error", {"error": "Scope identity capacity reached; recording paused"}, game_time)
            self.fail("Scope identity capacity reached; recording paused")
            return
        previous = self._scopes.get(key, {"first_entry": None, "last_entry": None,
                                          "last_exit": None, "entry_count": 0})
        current = copy_data(previous)
        current["currently_observed"] = entering
        if entering:
            current["entry_count"] += 1
            current["last_entry"] = game_time
            if current["first_entry"] is None:
                current["first_entry"] = game_time
        else:
            current["last_exit"] = game_time
        if self.note("scope_entry" if entering else "scope_exit", {"target": target}, game_time) is not None:
            self._scopes[key] = current

    def enter(self, target, game_time):
        self._scope_change(target, game_time, True)

    def leave(self, target, game_time):
        self._samples.pop(target["key"], None)
        if target["kind"] == "sim":
            for identity in tuple(self._relationship_bits):
                if target["key"] in identity[:2]:
                    del self._relationship_bits[identity]
            # A relationship sampled on the other participant must also lose
            # its baseline across an unobserved interval.
            prefix = "relationship." + target["id"] + "."
            for sample_time, values in self._samples.values():
                for name in tuple(values):
                    if name.startswith(prefix):
                        del values[name]
        self._scope_change(target, game_time, False)

    def history(self, entity_key, limit=50, include_internal=False):
        if not 1 <= limit <= 500:
            raise ValueError("History limit must be between 1 and 500")
        state = self.status()
        selected, truncated = self.index.recent(entity_key, limit, include_internal)
        durable = state["persistence"]["durable_sequence"]
        records = copy_data(selected)
        for record in records:
            record["persistence"] = "written" if record["accepted_sequence"] <= durable else "accepted"
        return {"status": state["state"], "events": records, "coverage": state,
                "target_observation": copy_data(self._scopes.get(entity_key, {"currently_observed": False, "status": "not_observed"})),
                "limit": limit, "truncated": truncated or self.evicted > 0,
                "scope": "current_session_recent_cache", "include_internal": include_internal}

    def query_history(self, entity_key, target=None, **filters):
        state = self.status()
        metadata = {"status": state["state"], "coverage": state,
                    "target": copy_data(target or {"key": entity_key}),
                    "target_observation": copy_data(self._scopes.get(entity_key, {
                        "currently_observed": False, "status": "not_observed"}))}
        return self.index.query(entity_key, metadata, **filters)

    def history_page(self, cursor):
        return self.index.next_page(cursor)

    def close_query(self, cursor):
        self.index.release(cursor)

    def close_queries(self):
        self.index.close()
