"""Shared, JSON-safe identities and clocks; no game imports."""

import datetime
import json
import math
import uuid

from context_overlay import SCHEMA_VERSION, VERSION


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def new_id():
    return uuid.uuid4().hex


def entity(kind, identifier, name=None, definition_id=None):
    if kind not in ("sim", "object"):
        raise ValueError("Unsupported entity kind: " + str(kind))
    if isinstance(identifier, bool) or int(identifier) <= 0:
        raise ValueError("Entity ID must be a positive integer")
    result = {"kind": kind, "id": str(int(identifier)), "name": name}
    result["key"] = kind + ":" + result["id"]
    if definition_id is not None:
        result["definition_id"] = str(int(definition_id))
    return result


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite numeric value")
    return result


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def copy_data(value):
    # This both detaches mutable input and rejects accidental game objects.
    return json.loads(encode(value))


def field(value=None, status="available", source=None, reason=None):
    result = {"status": status, "value": value, "source": source}
    if reason is not None:
        result["reason"] = str(reason)
    return result


def envelope(kind, session_id):
    return {"schema_version": SCHEMA_VERSION, "module_version": VERSION,
            "kind": kind, "session_id": session_id, "recorded_at": utc_now()}


def outcome(finishing_type, exited):
    if not exited:
        return "unknown"
    if finishing_type == "NATURAL":
        return "completed"
    if finishing_type in ("FAILED_TESTS", "TRANSITION_FAILURE"):
        return "failed"
    if finishing_type in ("KILLED", "RESET", "UNKNOWN", None):
        return "unknown"
    if finishing_type in ("USER_CANCEL", "SI_FINISHED", "TARGET_DELETED",
                          "DISPLACED", "AUTO_EXIT", "INTERACTION_INCOMPATIBILITY",
                          "INTERACTION_QUEUE", "PRIORITY", "SOCIALS", "WAIT_IN_LINE",
                          "OBJECT_CHANGED", "SITUATIONS", "CRAFTING", "LIABILITY",
                          "DIALOG", "CONDITIONAL_EXIT", "FIRE", "WEDDING",
                          "ROUTING_FORMATION"):
        return "cancelled"
    return "unknown"
