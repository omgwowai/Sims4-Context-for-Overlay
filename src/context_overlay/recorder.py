"""Canonical event revisions and bounded recent history."""

from collections import OrderedDict

from context_overlay.history import DEFAULT_EVENT_CAPACITY, DEFAULT_MEMORY_BYTES, HistoryError, HistoryIndex, copy_events
from context_overlay.model import copy_data, envelope, new_id, outcome, utc_now
from context_overlay.storage import StorageError, StorageBusy


class Recorder:
    def __init__(self, journal, session_id=None, capacity=DEFAULT_EVENT_CAPACITY, enabled=True,
                 memory_bytes=DEFAULT_MEMORY_BYTES, **query_options):
        external_rate = query_options.pop("external_rate", 20)
        external_burst = query_options.pop("external_burst", 40)
        self.journal = journal
        self.session_id = session_id or new_id()
        self.capacity = capacity
        self.enabled = enabled
        self.paused = False
        self.error = None
        self.index = HistoryIndex(self.session_id, capacity, memory_bytes, **query_options)
        self.events = self.index.events
        self.started_at = utc_now()
        self.observation_scope = None
        self.zone_visit = 0
        self.interaction_namespace = self.session_id
        self._scopes = {}
        self._relationship_bits = {}
        self._relationship_event_keys = {}
        self._retired_interactions = OrderedDict()
        self.references = {}
        from context_overlay.external import ExternalWriter
        self.external = ExternalWriter(self, external_rate, external_burst)

    def fail(self, message):
        if not self.paused:
            self.error = str(message)
        self.paused = True

    def status(self):
        persistence = self.journal.status()
        if persistence.get("error"):
            self.fail(persistence["error"])
        return {"state": "disabled" if not self.enabled else ("failed" if self.paused else "recording"),
                "error": self.error, "session_id": self.session_id,
                "started_at": self.started_at, "retained_events": len(self.events),
                "zone_visit": self.zone_visit, "observation_scope": copy_data(self.observation_scope),
                "evicted_events": self.index.evicted, "persistence": persistence,
                "history_index": self.index.status(), "external": self.external.status()}

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

    def _save(self, event, external=False):
        if not self.enabled or self.paused:
            return None
        if self.observation_scope is not None:
            event.setdefault("observation_scope", copy_data(self.observation_scope))
            event.setdefault("zone_visit", self.zone_visit)
        actor = (event.get("cause") or {}).get("actor")
        if actor:
            if actor["key"] not in event["entities"]:
                event["entities"].append(actor["key"])
                event.setdefault("participants", []).append(actor)
            if not any(role["entity_key"] == actor["key"] for role in event.get("roles", [])):
                event.setdefault("roles", []).append({"entity_key": actor["key"], "role": "initiator",
                                                      "basis": event["cause"].get("basis", "recorded_cause_actor")})
        try:
            event, charge = self.index.prepare(event)
        except HistoryError as exc:
            if external:
                raise
            self.note("recording_error", {"error": str(exc)})
            self.fail(exc)
            return None
        record = envelope("event_revision", self.session_id)
        record["event"] = event
        eviction = self.index.eviction_for(event["event_id"])
        if eviction:
            record["evicted_event_ids"] = [eviction]
        if external:
            from context_overlay.external import ExternalError
            try:
                sequence = self.journal.append(record, recoverable=True)
            except StorageBusy as exc:
                raise ExternalError("write_busy", str(exc), {"retry_after_seconds": 0.25}) from None
            except StorageError as exc:
                self.fail(exc)
                raise ExternalError("recorder_failed", str(exc)) from None
        else:
            sequence = self._write(record)
        if sequence is None:
            return None
        discarded = self.index.publish(event, sequence, charge)
        if discarded:
            if discarded["event_type"] == "interaction":
                self._retired_interactions[discarded["event_id"]] = None
                if len(self._retired_interactions) > self.capacity:
                    self._retired_interactions.popitem(last=False)
            identity = self._relationship_event_keys.pop(discarded["event_id"], None)
            if identity is not None:
                self._relationship_bits.pop(identity, None)
            for key in discarded["entities"]:
                if key not in self.index.entities and not self._scopes.get(key, {}).get("currently_observed"):
                    self._scopes.pop(key, None)
                    self.references.pop(key, None)
        refs = list(event.get("participants", []))
        facts = event.get("facts", {})
        refs.extend(item for item in (facts.get("actor"), facts.get("target")) if item)
        refs.extend(facts.get("participants", []))
        if not external:
            self.references.update(copy_data({reference["key"]: reference for reference in refs}))
        return event

    def fact(self, category, participants, payload, game_time, source, roles=None,
             cause=None, tier="main", event_id=None, evidence="notification"):
        event_id = event_id or self.session_id + ":fact:" + new_id()
        previous = self.events.get(event_id)
        # Own the lists extended by _save; prepare detaches all nested input.
        event = {"event_id": event_id, "revision": previous["revision"] + 1 if previous else 1,
                 "event_type": "game_event", "category": category, "field": category,
                 "tier": tier, "entities": [ref["key"] for ref in participants],
                 "participants": list(participants), "roles": list(roles or []),
                 "payload": payload, "last_observed_time": game_time,
                 "source": source, "evidence_type": evidence, "cause": cause}
        if previous:
            event["first_observed_time"] = previous["first_observed_time"]
        return self._save(event)

    def link_decision(self, interaction_event_id, decision_event_id):
        previous = self.events.get(interaction_event_id)
        if previous is None or previous["event_type"] != "interaction":
            return None
        if previous["facts"].get("decision_event_id") == decision_event_id:
            return previous
        event = copy_data(previous)
        event.pop("accepted_sequence", None)
        event["facts"]["decision_event_id"] = decision_event_id
        event["facts"]["decision_coverage"] = "exact_selected_instance"
        event["revision"] += 1
        return self._save(event)

    def interaction_event_id(self, actor_id, interaction_id):
        return "{}:interaction:{}:{}".format(self.interaction_namespace, actor_id, interaction_id)

    def begin_zone(self, scope):
        self.zone_visit += 1
        self.observation_scope = copy_data(scope)
        # EA instance IDs can be reused after a zone is unloaded. Preserve the
        # recording session, but never revise an earlier visit's interaction.
        self.interaction_namespace = self.session_id + ":visit:" + str(self.zone_visit)
        self._retired_interactions.clear()
        self._relationship_bits.clear()
        self._relationship_event_keys.clear()

    def end_zone(self, game_time):
        for key, scope in list(self._scopes.items()):
            if scope.get("currently_observed"):
                self.leave(self.references[key], game_time)
                # Observation has stopped even if writing the boundary fails.
                scope = self._scopes[key]
                scope["currently_observed"] = False
                scope["last_exit"] = copy_data(game_time)

    def interaction(self, phase, facts, game_time, source):
        if not self.enabled or self.paused:
            return None
        actor = facts["actor"]
        interaction_id = str(facts["interaction_id"])
        event_id = self.interaction_event_id(actor["id"], interaction_id)
        if event_id in self._retired_interactions:
            return None
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
        if previous is not None and previous["facts"].get("decision_event_id"):
            event["facts"]["decision_event_id"] = previous["facts"]["decision_event_id"]
            event["facts"]["decision_coverage"] = "exact_selected_instance"
        if previous is not None and previous["stage"] == "ended" and phase != "exited":
            # A delayed non-terminal observation cannot erase terminal evidence.
            event["facts"]["finishing_type"] = previous["facts"].get("finishing_type")
        target = facts.get("target")
        if target and target["key"] not in event["entities"]:
            event["entities"].append(target["key"])
        for participant in facts.get("participants", []):
            if participant["key"] not in event["entities"]:
                event["entities"].append(participant["key"])
        event["participants"] = copy_data(facts.get("participants", [actor] + ([target] if target else [])))
        event["roles"] = copy_data(facts.get("roles", []))
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

    def change(self, entities, field_name, before, after, game_time, source, metadata=None,
               cause=None, tier="main", roles=None):
        if before == after:
            return None
        event = {"event_id": self.session_id + ":change:" + new_id(), "revision": 1,
                 "event_type": "state_change", "tier": tier,
                 "entities": [item["key"] for item in entities],
                 "participants": list(entities), "field": field_name,
                 "before": before, "after": after,
                 "last_observed_time": game_time, "source": source,
                 "evidence_type": "notification"}
        event["roles"] = list(roles) if roles is not None else [
            {"entity_key": ref["key"], "role": "subject" if i == 0 else "target", "basis": "state_change_owner"}
            for i, ref in enumerate(entities)]
        if cause:
            event["cause"] = cause
        if metadata:
            event["metadata"] = metadata
        return self._save(event)

    def relationship_bit(self, actor, other, bit, added, game_time, source, bidirectional, cause=None):
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
                            game_time, source, metadata={"bidirectional": bidirectional, "reporting_actor": actor}, cause=cause)
        if event is not None:
            if previous:
                self._relationship_event_keys.pop(previous[1], None)
            self._relationship_bits[identity] = (added, event["event_id"])
            self._relationship_event_keys[event["event_id"]] = identity
        return event

    def _scope_change(self, target, game_time, entering):
        if not self.enabled or self.paused:
            return
        key = target["key"]
        if key not in self._scopes and len(self._scopes) >= max(4096, self.capacity):
            retired = next((item for item, scope in self._scopes.items()
                            if not scope.get("currently_observed") and item not in self.index.entities), None)
            if retired is None:
                self.note("recording_error", {"error": "Scope identity capacity reached; recording paused"}, game_time)
                self.fail("Scope identity capacity reached; recording paused")
                return
            self._scopes.pop(retired)
            self.references.pop(retired, None)
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
            self.references[key] = copy_data(target)

    def enter(self, target, game_time):
        self._scope_change(target, game_time, True)

    def leave(self, target, game_time):
        if target["kind"] == "sim":
            for identity in tuple(self._relationship_bits):
                if target["key"] in identity[:2]:
                    self._relationship_event_keys.pop(self._relationship_bits[identity][1], None)
                    del self._relationship_bits[identity]
        self._scope_change(target, game_time, False)

    def history(self, entity_key, limit=50, include_internal=False, group_effects=False, origins=None, producers=None):
        if not 1 <= limit <= 500:
            raise ValueError("History limit must be between 1 and 500")
        state = self.status()
        selected, truncated = self.index.recent(entity_key, limit, include_internal, group_effects, origins, producers)
        records = copy_events(selected, state["persistence"]["durable_sequence"])
        return {"status": state["state"], "events": records, "coverage": state,
                "target_observation": copy_data(self._scopes.get(entity_key, {"currently_observed": False, "status": "not_observed"})),
                "limit": limit, "truncated": truncated or self.index.evicted > 0,
                "scope": "current_session_recent_cache", "include_internal": include_internal,
                "origins": origins, "producers": producers}

    def query_history(self, entity_key, target=None, **filters):
        state = self.status()
        metadata = {"status": state["state"], "coverage": state,
                    "target": target or ({"key": entity_key} if entity_key else None),
                    "target_observation": self._scopes.get(entity_key, {"currently_observed": False, "status": "not_observed"})}
        return self.index.query(entity_key, metadata, **filters)

    def read_changes(self, **options):
        state = self.status()
        if state["state"] != "recording":
            raise HistoryError("recorder_disabled" if not self.enabled else "recorder_failed", "Cannot advance changes while recorder is unavailable")
        return self.index.changes({"status": state["state"], "coverage": state}, **options)

    def history_page(self, cursor):
        return self.index.next_page(cursor)

    def close_query(self, cursor):
        self.index.release(cursor)

    def close_queries(self):
        self.index.close()
