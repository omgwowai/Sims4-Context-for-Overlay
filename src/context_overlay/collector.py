"""Compose current state and history for a fixed target."""

from context_overlay.model import copy_data, envelope, new_id, entity
from context_overlay.semanticizer import render


FIELDS = ("identity", "location", "time", "interactions", "needs", "buffs",
          "relationships", "object_states")
PRESETS = {"sim": ("identity", "time", "location", "needs", "buffs", "relationships", "interactions"),
           "object": ("identity", "time", "location", "object_states")}


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
        return collect(self.adapter, target, self.recorder.session_id, self.provenance, query)

    def resolve_history(self, kind, identifier):
        reference = self.recorder.references.get("{}:{}".format(kind, identifier))
        if reference is None and "{}:{}".format(kind, identifier) in self.recorder.index.entities:
            return dict(entity(kind, identifier), identity_status="unverified")
        return copy_data(reference) if reference is not None else self.adapter.resolve(kind, identifier)

    def history_packet(self, page, representation="both", target=None):
        packet = envelope("history", self.recorder.session_id)
        packet.update(request_id=new_id(), target=page.get("target", target),
                      provenance=copy_data(self.provenance), history=page,
                      status="complete" if page["status"] == "recording" else "partial")
        return self.render_packet(packet, representation)

    def query_history(self, target, representation="both", **filters):
        page = self.recorder.query_history(target["key"] if target else None, target=target, **filters)
        try:
            return self.history_packet(page, representation)
        except Exception:
            self.recorder.close_query(page["cursor"])
            raise

    def collect(self, target, fields=None, history_limit=50,
                include_history=True, include_internal=False, representation="both", origins=None, producers=None):
        selected = PRESETS[target["kind"]] if fields is None else fields
        packet = envelope("context", self.recorder.session_id)
        packet.update({"request_id": new_id(), "target": target,
                       "provenance": copy_data(self.provenance),
                       "requested_fields": list(selected), "snapshot": {},
                       "scope": self.adapter.scope(), "read_started": self.adapter.clock()})
        for name in selected:
            packet["snapshot"][name] = self.adapter.read(target, name)
        packet["read_finished"] = self.adapter.clock()
        packet["history"] = self.recorder.history(target["key"], history_limit, include_internal,
            origins=origins, producers=producers) if include_history else {
            "status": "not_requested", "events": []}
        packet["status"] = "partial" if has_unavailable(packet["snapshot"]) else "complete"
        if include_history and packet["history"]["status"] != "recording":
            packet["status"] = "partial"
        return self.render_packet(packet, representation)

    def render_packet(self, packet, representation):
        packet["representation"] = representation
        if representation != "raw":
            if self.semantic_enabled:
                packet["rendered"] = render(packet)
            else:
                packet["rendered"] = {"status": "disabled", "reason": "Semanticizer is disabled"}
                packet["status"] = "partial"
        # Raw evidence remains attached even for text requests, so references are resolvable.
        return packet
