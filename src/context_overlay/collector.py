"""Request validation and composition, independent of the EA adapter."""

from context_overlay.model import copy_data, envelope, new_id
from context_overlay.semanticizer import render


FIELDS = ("identity", "location", "time", "interactions", "needs", "buffs",
          "relationships", "object_states")


def has_unavailable(value):
    if isinstance(value, dict):
        if value.get("status") in ("error", "unsupported", "out_of_scope", "disabled"):
            return True
        return any(has_unavailable(child) for child in value.values())
    if isinstance(value, list):
        return any(has_unavailable(child) for child in value)
    return False


class Collector:
    def __init__(self, adapter, recorder, enabled=True, semantic_enabled=True, provenance=None):
        self.adapter = adapter
        self.recorder = recorder
        self.enabled = enabled
        self.semantic_enabled = semantic_enabled
        self.provenance = copy_data(provenance or {"status": "not_supplied"})

    def nearby(self, target, query):
        from context_overlay.nearby import collect
        if not self.enabled:
            raise ValueError("Context collector is disabled")
        return collect(self.adapter, target, self.recorder.session_id, self.provenance, query)

    def collect(self, kind, identifier, fields=None, history_limit=50,
                include_history=True, include_internal=False, representation="both", history_query=None):
        if not self.enabled:
            raise ValueError("Context collector is disabled")
        selected = tuple(fields) if fields is not None else FIELDS
        if not selected or len(set(selected)) != len(selected) or any(name not in FIELDS for name in selected):
            raise ValueError("Unknown, duplicate or empty field selection")
        if representation not in ("raw", "text", "both"):
            raise ValueError("Unknown representation")
        if not 1 <= history_limit <= 500:
            raise ValueError("History limit must be between 1 and 500")
        if history_query is not None and (not include_history or not isinstance(history_query, dict)):
            raise ValueError("history_query requires enabled history and a filter dictionary")
        target = self.adapter.resolve(kind, identifier)
        packet = envelope("context", self.recorder.session_id)
        packet.update({"request_id": new_id(), "target": target,
                       "provenance": copy_data(self.provenance),
                       "requested_fields": list(selected), "snapshot": {},
                       "scope": self.adapter.scope(), "read_started": self.adapter.clock()})
        for name in selected:
            packet["snapshot"][name] = self.adapter.read(target, name)
        packet["read_finished"] = self.adapter.clock()
        if history_query is not None:
            options = dict(history_query)
            options.setdefault("page_size", history_limit)
            options.setdefault("include_internal", include_internal)
            packet["history"] = self.recorder.query_history(target["key"], target=target, **options)
        else:
            packet["history"] = self.recorder.history(target["key"], history_limit, include_internal) if include_history else {
                "status": "not_requested", "events": []}
        packet["status"] = "partial" if has_unavailable(packet["snapshot"]) else "complete"
        if include_history and packet["history"]["status"] != "recording":
            packet["status"] = "partial"
        packet["representation"] = representation
        if representation != "raw":
            if self.semantic_enabled:
                packet["rendered"] = render(packet)
            else:
                packet["rendered"] = {"status": "disabled", "reason": "Semanticizer is disabled"}
                packet["status"] = "partial"
        # Raw evidence remains attached even for text requests, so references are resolvable.
        return packet
