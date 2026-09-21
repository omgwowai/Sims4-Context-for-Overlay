"""Shared journal validation and durable prefixes; no runtime/game references."""

import hashlib
import json
import math
import sys
from collections import defaultdict
from collections.abc import Mapping

from context_overlay.history import deep_size


class ViewError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def packed(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def reject_constant(value):
    raise ValueError("Non-finite JSON number: " + value)


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        reject_constant(value)
    return number


def decode(raw):
    return json.loads(raw.decode("utf-8"), parse_constant=reject_constant, parse_float=finite_float)


LINE_LIMIT = 4 * 1024 * 1024


def record_digest(record):
    """Compare retry content independently of JSON whitespace/key ordering."""
    return hashlib.sha256(packed(record)).digest()


def validate_record(raw, session_id, next_sequence, prior_digest, prior_revision, line_limit=LINE_LIMIT):
    """Return a new validated record, or None for an identical sequence retry.

    Lookups only contain previously accepted records. Callers own storage and
    advance their sequence/revision indexes only after validation succeeds.
    """
    if not raw.endswith(b"\n") or len(raw) > line_limit:
        raise ValueError("Truncated or oversized journal record")
    record = decode(raw)
    if not isinstance(record, dict):
        raise ValueError("Invalid journal record")
    seq, session = record["sequence"], record["session_id"]
    if type(seq) is not int or seq < 1 or not isinstance(session, str) or not session:
        raise ValueError("Invalid sequence or journal session")
    if session_id is not None and session != session_id:
        raise ValueError("Mixed journal sessions")
    if seq < next_sequence:
        if prior_digest(seq) != record_digest(record):
            raise ValueError("Conflicting sequence retry")
        return None
    if seq != next_sequence:
        raise ValueError("Missing or reordered sequence")
    if record["kind"] == "event_revision":
        event = record["event"]
        if not isinstance(event, dict):
            raise ValueError("Invalid event revision")
        identifier, revision = event["event_id"], event["revision"]
        if not isinstance(identifier, str) or not identifier.startswith(session + ":"):
            raise ValueError("Event belongs to another session")
        if type(revision) is not int or revision != (prior_revision(identifier) or 0) + 1:
            raise ValueError("Missing or conflicting event revision")
        if not isinstance(event.get("entities"), list):
            raise ValueError("Invalid event entities")
    elif record["kind"] != "observation":
        raise ValueError("Unsupported journal record kind")
    return record


class LatestEvents(Mapping):
    """Keep raw bytes authoritative, avoiding a session-sized tracked object graph."""
    def __init__(self, records, index):
        self.records, self.index = records, index

    def __len__(self):
        return len(self.index)

    def __iter__(self):
        return iter(self.index)

    def __getitem__(self, key):
        return decode(self.records[self.index[key][0]])["event"]

    def selected(self, entity):
        return [key for key, (_, _, entities) in self.index.items() if entity in entities]


def derivation_events(data, cache_budget, checkpoint):
    """Optionally decode once for worker-private, read-only derivation reuse.

    The caller reserves construction space separately and charges the returned
    estimate for as long as it retains this mapping. Raw source bytes remain
    authoritative; public event pages must still decode independent values.
    """
    events = data["events"]
    # Per-event deep sizes were measured by read_prefix. Allow additional space
    # for mapping slots and the ID sequence without rescanning every object.
    size = data["latest_event_bytes"] + 256 + 128 * len(events)
    if size > cache_budget:
        return events, 0
    decoded = {}
    for i, identifier in enumerate(events):
        if i % 32 == 0:
            checkpoint()
        decoded[identifier] = events[identifier]
    checkpoint()
    return decoded, size


def read_prefix(path, session_id, sequence, byte_offset, checkpoint, memory_limit, line_limit=LINE_LIMIT, record_limit=100000):
    """Validate every record before publishing, retaining raw bytes and latest events.

    No FIFO eviction is applied. Equal sequence retries must be identical; gaps,
    mixed sessions, partial rows and conflicting/skipped revisions fail closed.
    """
    if type(sequence) is not int or sequence < 1 or type(byte_offset) is not int or byte_offset < 1:
        raise ViewError("source_unavailable", "No durable journal prefix is available yet")
    records, events, sizes, revisions = {}, {}, {}, defaultdict(list)
    observations, first_seen = [], {}
    total, position, next_sequence = 0, 0, 1
    source_hash = hashlib.sha256()

    def prior_digest(seq):
        return record_digest(decode(records[seq])) if seq in records else None

    def prior_revision(identifier):
        previous = events.get(identifier)
        return previous[1] if previous else 0

    try:
        with open(str(path), "rb") as stream:
            while position < byte_offset:
                checkpoint()
                raw = stream.readline(min(line_limit + 1, byte_offset - position))
                position += len(raw)
                source_hash.update(raw)
                record = validate_record(raw, session_id, next_sequence, prior_digest, prior_revision, line_limit)
                if record is None:
                    continue
                seq = record["sequence"]
                if seq > sequence:
                    raise ValueError("Out-of-prefix sequence")
                if len(records) >= record_limit:
                    raise ViewError("view_budget", "Durable source exceeds the record-count budget")
                next_sequence += 1
                records[seq] = raw
                total += sys.getsizeof(raw) + 128
                if record["kind"] == "event_revision":
                    event = record["event"]
                    identifier, revision = event["event_id"], event["revision"]
                    previous = events.get(identifier)
                    size = deep_size(event)
                    sizes[identifier] = size
                    if previous is None:
                        total += 512 + len(identifier) * 2
                    events[identifier] = (seq, revision, tuple(event["entities"]))
                    first_seen.setdefault(identifier, seq)
                    revisions[identifier].append(seq)
                    total += 64
                elif record["kind"] == "observation":
                    # Entity association is structural; never substring matching.
                    pending, keys = [record.get("data")], set()
                    while pending:
                        item = pending.pop()
                        if isinstance(item, dict):
                            pending.extend(item.values())
                        elif isinstance(item, list):
                            pending.extend(item)
                        elif isinstance(item, str) and item.startswith(("sim:", "object:")):
                            keys.add(item)
                    observations.append((seq, record.get("category"), keys))
                    total += deep_size(keys) + 128
                if total > memory_limit:
                    raise ViewError("view_budget", "Durable source exceeds the view memory budget")
        if position != byte_offset or next_sequence != sequence + 1:
            raise ValueError("Durable prefix is incomplete")
    except ViewError:
        raise
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ViewError("source_invalid", str(exc)) from None
    return {"records": records, "events": LatestEvents(records, events), "revisions": dict(revisions),
            "observations": observations, "first_seen": first_seen,
            "sha256": source_hash.hexdigest(), "memory_bytes": total,
            "latest_event_bytes": sum(sizes.values()), "session_id": session_id,
            "as_of_sequence": sequence, "byte_offset": byte_offset}
