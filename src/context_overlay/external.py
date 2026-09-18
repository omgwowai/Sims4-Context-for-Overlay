"""Opaque external records: bounded JSON validation and session-local deduplication."""

import hashlib
import json
import math
import time

from context_overlay.history import HistoryError, deep_size
from context_overlay.model import entity, new_id, utc_now


LIMITS = {"payload_bytes": 65536, "payload_depth": 16, "payload_nodes": 32768,
          "entities": 32, "identifier_chars": 128, "dedup_keys": 10000,
          "dedup_bytes": 8 * 1024 * 1024, "rate_per_second": 20, "burst": 40}


class ExternalError(ValueError):
    def __init__(self, code, message, details=None):
        self.code, self.details = code, details or {}
        super().__init__(message)


def identifier(value, name):
    if (not isinstance(value, str) or not value or len(value) > LIMITS["identifier_chars"]
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ExternalError("invalid_request", name + " must contain 1-128 printable ASCII characters without spaces")
    return value


def validate(producer, payload, entities, key):
    identifier(producer, "producer")
    if key is not None:
        identifier(key, "idempotency_key")
    if entities is None:
        entities = []
    if not isinstance(entities, (list, tuple)) or len(entities) > LIMITS["entities"]:
        raise ExternalError("invalid_request", "entities must be a list of at most 32 entity keys")
    normalized = set()
    for value in entities:
        parts = value.split(":") if isinstance(value, str) else []
        if (len(parts) != 2 or parts[0] not in ("sim", "object") or not 1 <= len(parts[1]) <= 20
                or any(c not in "0123456789" for c in parts[1]) or not 0 < int(parts[1]) < 2 ** 64):
            raise ExternalError("invalid_request", "Use sim:<uint64> or object:<uint64> entity keys")
        normalized.add(parts[0] + ":" + str(int(parts[1])))
    nodes = [0]
    ancestors = set()

    def walk(value, depth):
        nodes[0] += 1
        if depth > LIMITS["payload_depth"] or nodes[0] > LIMITS["payload_nodes"]:
            raise ExternalError("payload_limit", "JSON depth or node limit exceeded")
        kind = type(value)
        if kind in (dict, list):
            if id(value) in ancestors:
                raise ExternalError("invalid_request", "JSON cannot contain cycles")
            ancestors.add(id(value))
            if kind is dict:
                for k, child in value.items():
                    if type(k) is not str:
                        raise ExternalError("invalid_request", "JSON object keys must be strings")
                    walk(k, depth + 1)
                    walk(child, depth + 1)
            else:
                for child in value:
                    walk(child, depth + 1)
            ancestors.remove(id(value))
        elif kind is str:
            if len(value) > LIMITS["payload_bytes"]:
                raise ExternalError("payload_limit", "JSON string exceeds payload limit")
        elif kind is float:
            if not math.isfinite(value):
                raise ExternalError("invalid_request", "JSON numbers must be finite")
        elif kind is int:
            if value.bit_length() > 4096:
                raise ExternalError("payload_limit", "JSON integer is too large")
        elif value is not None and kind is not bool:
            raise ExternalError("invalid_request", "payload must be JSON data, not Python or game objects")
    walk(payload, 0)
    try:
        chunks, size = [], 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, sort_keys=True,
                                      separators=(",", ":")).iterencode(payload):
            size += len(chunk.encode("utf-8"))
            if size > LIMITS["payload_bytes"]:
                raise ExternalError("payload_limit", "Encoded payload exceeds 64 KiB")
            chunks.append(chunk)
        encoded = "".join(chunks)
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, ExternalError):
            raise
        raise ExternalError("invalid_request", "JSON encoding failed: " + str(exc)) from None
    entities = sorted(normalized)
    digest = hashlib.sha256((json.dumps(entities) + "\n" + encoded).encode("utf-8")).hexdigest()
    return json.loads(encoded), entities, digest


class ExternalWriter:
    def __init__(self, recorder, rate=20, burst=40, clock=None):
        self.recorder = recorder
        self.rate, self.burst = rate, burst
        self.clock = clock or time.monotonic
        self.tokens, self.updated = float(burst), self.clock()
        self.keys, self.key_bytes = {}, 0

    def status(self):
        return {"dedup_keys": len(self.keys), "dedup_bytes": self.key_bytes,
                "rate_per_second": self.rate, "burst": self.burst}

    def append(self, producer, payload, entities, key, game_time):
        payload, entities, digest = validate(producer, payload, entities, key)
        rec = self.recorder
        identity = (producer, key)
        previous = self.keys.get(identity) if key is not None else None
        if previous:
            if previous[0] != digest:
                raise ExternalError("idempotency_conflict", "This key was already used with different content")
            return self.receipt(previous[1], True)
        state = rec.status()
        if state["state"] != "recording":
            raise ExternalError("recorder_" + ("disabled" if not rec.enabled else "failed"),
                                "Recorder is not accepting events", {"state": state["state"]})
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + max(0, now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < 1:
            raise ExternalError("rate_limited", "External write rate exceeded",
                                {"retry_after_seconds": (1 - self.tokens) / self.rate})
        event_id = rec.session_id + ":external:" + new_id()
        receipt = {"event_id": event_id, "session_id": rec.session_id, "revision": 1,
                   "accepted_sequence": 0}
        charge = deep_size((identity, digest, receipt)) + 512
        if key is not None and (len(self.keys) >= LIMITS["dedup_keys"] or self.key_bytes + charge > LIMITS["dedup_bytes"]):
            raise ExternalError("dedup_capacity", "Session deduplication capacity reached; existing keys remain valid")
        participants = []
        for value in entities:
            kind, number = value.split(":")
            ref = rec.references.get(value)
            participants.append(ref if ref is not None else dict(entity(kind, number), identity_status="unverified"))
        event = {"event_id": event_id, "revision": 1, "event_type": "external_event",
                 "origin": "external", "producer": producer, "payload": payload,
                 "entities": entities, "participants": participants, "roles": [], "tier": "main",
                 "last_observed_time": game_time, "recorded_at": utc_now(),
                 "source": "public_api", "evidence_type": "producer_submission"}
        try:
            saved = rec._save(event, external=True)
        except HistoryError as exc:
            raise ExternalError("write_busy", str(exc)) from None
        receipt["accepted_sequence"] = saved["accepted_sequence"]
        self.tokens -= 1
        if key is not None:
            self.keys[identity] = (digest, receipt)
            self.key_bytes += charge
        return self.receipt(receipt, False)

    def receipt(self, value, duplicate):
        state = self.recorder.journal.status()
        return dict(value, duplicate=duplicate, retained=value["event_id"] in self.recorder.events,
                    persistence="written" if value["accepted_sequence"] <= state["durable_sequence"] else "accepted",
                    persistence_error=state.get("error"))
