"""Entity indexes and bounded, immutable-version query snapshots.

Only the simulation thread publishes and queries. Published event dictionaries
are private; callers receive detached copies, including for cursor pages.
"""

import bisect
import sys
import time
from collections import OrderedDict

from context_overlay.model import copy_data, new_id, utc_now


MIB = 1024 * 1024
DEFAULT_EVENT_CAPACITY = 200000
DEFAULT_MEMORY_BYTES = 1536 * MIB


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
        charge = deep_size(event) + 512 + 512 * len(event["entities"])
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

    def recent(self, entity_key, limit, include_internal, group_effects=False):
        index = self.entities.get(entity_key)
        if index is None:
            return [], False
        if group_effects:
            # Group a bounded query instead of scanning/copying the full cache.
            candidates = []
            for event_id in reversed(index.by_id):
                event = self.events[event_id]
                if include_internal or event["tier"] == "main":
                    candidates.append(event)
                if len(candidates) >= 500:
                    break
            grouped = self.grouped(candidates)
            return grouped[:limit], len(grouped) > limit or len(index.by_id) > len(candidates)
        selected = []
        for event_id in reversed(index.by_id):
            event = self.events[event_id]
            if include_internal or event["tier"] == "main":
                if len(selected) == limit:
                    return selected, True
                selected.append(event)
        return selected, False

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
                "snapshot_references": self._snapshot_refs, "snapshot_charged_bytes": self._snapshot_bytes,
                "snapshot_memory_budget_bytes": self.snapshot_byte_limit}

    @staticmethod
    def _values(value, name, allowed=None):
        if value is None:
            return None
        if not isinstance(value, (list, tuple)) or not value or len(value) > 64 or any(not isinstance(v, str) or not v for v in value):
            raise HistoryError("invalid_query", name + " must be a nonempty list of at most 64 strings")
        values = set(value)
        if allowed is not None and not values <= allowed:
            raise HistoryError("invalid_query", "Unsupported " + name)
        return values

    def query(self, entity_key, metadata, page_size=50, include_internal=False,
              time_field="first_observed", from_ticks=None, to_ticks=None,
              event_types=None, fields=None, outcomes=None, tuning_ids=None, order="desc", group_effects=False):
        if self.closed:
            raise HistoryError("session_closed", "Recording run is closed")
        validate_entity(entity_key)
        if isinstance(page_size, bool) or not isinstance(page_size, int) or not 1 <= page_size <= 500:
            raise HistoryError("invalid_query", "Page size must be between 1 and 500")
        if not isinstance(include_internal, bool):
            raise HistoryError("invalid_query", "include_internal must be a boolean")
        if not isinstance(group_effects, bool):
            raise HistoryError("invalid_query", "group_effects must be a boolean")
        if time_field not in ("first_observed", "started", "ended") or order not in ("asc", "desc"):
            raise HistoryError("invalid_query", "Unsupported time field or order")
        lower, upper = ticks(from_ticks), ticks(to_ticks)
        if lower is not None and upper is not None and lower >= upper:
            raise HistoryError("invalid_query", "Time range is [from, to), with from < to")
        types = self._values(event_types, "event_types", {"interaction", "state_change", "game_event"})
        names = self._values(fields, "fields")
        results = self._values(outcomes, "outcomes", {"completed", "cancelled", "failed", "unknown"})
        tunings = self._values(tuning_ids, "tuning_ids")
        self.prune()
        if len(self._snapshots) >= self.snapshot_limit:
            raise HistoryError("query_limit", "Close a query or wait for expiry")
        index = self.entities.get(entity_key)
        rows, charged, examined = [], deep_size(metadata) + 1024, 0
        if charged + self._snapshot_bytes > self.snapshot_byte_limit:
            raise HistoryError("query_budget", "Query metadata exceeds snapshot budget")
        if index:
            if time_field == "first_observed":
                left = 0 if lower is None else bisect.bisect_left(index.by_time, (lower,))
                right = len(index.by_time) if upper is None else bisect.bisect_left(index.by_time, (upper,))
                candidates = (index.by_time[i] for i in range(left, right))
            else:
                candidates = iter(index.by_time)
            for key in candidates:
                examined += 1
                event = self.events[key[2]]
                observed = ticks(event.get(time_field + "_time"))
                if observed is None or (lower is not None and observed < lower) or (upper is not None and observed >= upper):
                    continue
                if not include_internal and event["tier"] != "main":
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
                rows.append(((observed, key[1], key[2]), event))
        rows.sort(key=lambda row: row[0], reverse=order == "desc")
        versions = tuple(row[1] for row in rows)
        original_refs = len(versions)
        if group_effects:
            versions = tuple(self.grouped(versions))
            charged += 512 * len(versions)
            if charged + self._snapshot_bytes > self.snapshot_byte_limit:
                raise HistoryError("query_budget", "Grouped query exceeds snapshot budget")
        query_id = new_id()
        snapshot = {"events": versions, "refs": original_refs, "charge": charged, "expires": self._clock() + self.snapshot_ttl,
                    "page_size": page_size, "entity_key": entity_key, "metadata": copy_data(metadata),
                    "created_at": utc_now(), "examined": examined,
                    "filters": {"time_field": time_field, "from_ticks": str(lower) if lower is not None else None,
                                "to_ticks": str(upper) if upper is not None else None, "order": order,
                                "include_internal": include_internal, "group_effects": group_effects, "event_types": sorted(types) if types else None,
                                "fields": sorted(names) if names else None, "outcomes": sorted(results) if results else None,
                                "tuning_ids": sorted(tunings) if tunings else None}}
        self._snapshots[query_id] = snapshot
        self._snapshot_refs += original_refs
        self._snapshot_bytes += charged
        return self._page(query_id, 0)

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
        records = copy_data(versions[offset:end])
        durable = result["coverage"]["persistence"]["durable_sequence"]
        for record in records:
            record["persistence"] = "written" if record["accepted_sequence"] <= durable else "accepted"
            for effect in record.get("effects", []):
                effect["persistence"] = "written" if effect["accepted_sequence"] <= durable else "accepted"
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
        return result
