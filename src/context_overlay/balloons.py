"""Observe successful balloon sends; never infer client visibility or thoughts.

Context reads canonical events by time window. Request provenance uses weak references where supported and bounded,
expiring strong references for EA's non-weakrefable slotted requests.
"""

from collections import OrderedDict
import base64
import importlib
import time
import weakref

from context_overlay.hooks import arg
from context_overlay.history import deep_size
from context_overlay.model import copy_data, new_id, number


SOURCE = "balloon.balloon_request.BalloonRequest.distribute"
CATEGORY = "balloon.sent"
PENDING_CAPACITY = 2048
PENDING_BYTES = 1024 * 1024
PENDING_TTL_SECONDS = 300


def resource_key(value):
    if value is None:
        return None
    return {"type": str(int(value.type)), "group": str(int(value.group)),
            "instance": str(int(value.instance))}


def proto_snapshot(value, depth=0):
    """Bounded reflection of already-built icon protos, including unknown fields.

    Preserve 64-bit integers as decimal strings. Do not call game serializers
    which can update trackers or regenerate icons while observing a send.
    """
    if value is None:
        return None
    if depth >= 5:
        return {"status": "unavailable", "reason": "proto_depth_limit"}
    fields = value.ListFields()
    result = {}
    for descriptor, item in fields[:64]:
        def scalar(child):
            if descriptor.type == 11:
                return proto_snapshot(child, depth + 1)
            if descriptor.type in (3, 4, 6, 16, 18):
                return str(int(child))
            if isinstance(child, bytes):
                return {"base64": base64.b64encode(child[:1024]).decode("ascii"),
                        "truncated": len(child) > 1024}
            if isinstance(child, str):
                return child if len(child) <= 512 else {"text": child[:512], "truncated": True}
            if isinstance(child, float):
                return number(child)
            return child
        if descriptor.label == 3:
            result[descriptor.name] = [scalar(child) for child in item[:32]]
            if len(item) > 32:
                result.setdefault("_truncated_fields", []).append(descriptor.name)
        else:
            result[descriptor.name] = scalar(item)
    if len(fields) > 64:
        result["_truncated"] = True
    return result


class BalloonCapture:
    def __init__(self, runtime):
        self.runtime = runtime
        self.coverage = {"send": {"state": "not_installed"}, "provenance": {"state": "not_installed"}}
        self.type_names = {}
        self.pending = OrderedDict()
        self.pending_bytes = 0
        self.provenance_fallbacks = self.provenance_expired = 0
        self.sent = self.provenance_evicted = self.provenance_unavailable = 0
        self.error = None

    def install(self):
        try:
            from balloon.balloon_enums import BALLOON_TYPE_LOOKUP
            self.type_names = {int(pair[0]): str(getattr(kind, "name", kind)).rsplit(".", 1)[-1]
                               for kind, pair in BALLOON_TYPE_LOOKUP.items()}
            self.coverage["types"] = {"state": "loaded", "source": "BALLOON_TYPE_LOOKUP"}
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            self.coverage["types"] = {"state": "unavailable", "reason": str(exc)}
        for key, module, owner, method, callback in (
                ("send", "balloon.balloon_request", "BalloonRequest", "distribute", self.on_distribute),
                ("provenance", "balloon.tunable_balloon", "TunableBalloon", "build_balloon_requests", self.on_requests)):
            try:
                cls = getattr(importlib.import_module(module), owner)
                self.runtime.hooks.after(cls, method, callback)
                self.coverage[key] = {"state": "installed", "validation": "not_tested"}
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                self.coverage[key] = {"state": "unavailable", "reason": str(exc)}

    def status(self):
        return copy_data({"coverage": self.coverage, "error": self.error, "observed_sends": self.sent,
                          "context_storage": "canonical_events", "pending_provenance": len(self.pending),
                          "pending_provenance_bytes": self.pending_bytes,
                          "pending_strong_references": sum(row["weak"] is None for row in self.pending.values()),
                          "provenance_capacity": PENDING_CAPACITY, "provenance_metadata_byte_limit": PENDING_BYTES,
                          "provenance_ttl_real_seconds": PENDING_TTL_SECONDS,
                          "provenance_strong_fallbacks": self.provenance_fallbacks,
                          "provenance_expired": self.provenance_expired,
                          "provenance_evicted": self.provenance_evicted,
                          "provenance_unavailable": self.provenance_unavailable,
                          "scope": "zone_instantiated_current_zone_visit", "client_visibility": "unverified"})

    def on_requests(self, args, kwargs, result):
        try:
            self.remember_requests(args, kwargs, result)
        except Exception as exc:
            # Optional provenance must not prevent capturing the actual send.
            self.provenance_unavailable += 1
            self.coverage["provenance"]["last_error"] = "{}: {}".format(type(exc).__name__, exc)

    def remember_requests(self, args, kwargs, result):
        if self.runtime.closed or not isinstance(result, (list, tuple)):
            return
        self.prune_pending()
        resolver, source = arg(args, kwargs, 0, "resolver"), arg(args, kwargs, 10, "source")
        from_load = any(frame.get("from_load") for frame in self.runtime.sources.frames)
        cause = None if from_load else self.runtime.sources.cause(resolver=resolver,
            interaction=source if getattr(source, "sim", None) is not None else None)
        if cause is None and not from_load:
            return
        cause = copy_data(cause)
        charge = deep_size(cause) + 512
        for request in result:
            key = id(request)
            self.forget_pending(key)
            if charge > PENDING_BYTES:
                self.provenance_evicted += 1
                continue
            try:
                ref = weakref.ref(request, lambda ref, key=key: self.forget_pending(key, ref))
            except TypeError:
                ref = None
                self.provenance_fallbacks += 1
            while self.pending and (len(self.pending) >= PENDING_CAPACITY or
                                    self.pending_bytes + charge > PENDING_BYTES):
                self.forget_pending(next(iter(self.pending)))
                self.provenance_evicted += 1
            self.pending[key] = {"weak": ref, "strong": request if ref is None else None,
                                 "cause": cause, "expires": time.monotonic() + PENDING_TTL_SECONDS,
                                 "charge": charge, "from_load": from_load}
            self.pending_bytes += charge

    def forget_pending(self, key, ref=None):
        row = self.pending.get(key)
        if row is not None and (ref is None or row["weak"] is ref):
            self.pending.pop(key)
            self.pending_bytes -= row["charge"]

    def prune_pending(self):
        now = time.monotonic()
        while self.pending:
            key = next(iter(self.pending))
            if self.pending[key]["expires"] > now:
                break
            self.forget_pending(key)
            self.provenance_expired += 1

    def request_provenance(self, request):
        self.prune_pending()
        row = self.pending.get(id(request))
        if row is not None:
            original = row["weak"]() if row["weak"] is not None else row["strong"]
            if original is request:
                return row
        return None

    def snapshot(self, request):
        missing = []

        def read(name, convert=lambda value: value):
            try:
                return convert(getattr(request, name))
            except Exception as exc:
                missing.append({"field": name, "error": type(exc).__name__})
                return {"status": "unavailable", "reason": "read_failed"}

        def icon_object(obj):
            if obj is None:
                return None
            object_id, manager_id = obj.icon_info
            return {"object_id": str(object_id), "manager_id": str(manager_id),
                    "entity": self.runtime.adapter.event_reference(obj)}

        kind = read("balloon_type", int)
        payload = {"balloon_type": {"value": kind, "name": self.type_names.get(kind) if isinstance(kind, int) else None},
                   "icon": read("icon", resource_key), "icon_object": read("icon_object", icon_object),
                   "overlay": read("overlay", resource_key), "priority": read("priority", int),
                   "duration_seconds": read("duration", number),
                   "delay_seconds": read("delay", lambda v: None if v is None else number(v)),
                   "delay_randomization_seconds": read("delay_randomization", lambda v: None if v is None else number(v)),
                   "category_icon": read("category_icon", proto_snapshot), "icon_info": read("icon_info", proto_snapshot),
                   "view_offset": read("view_offset", lambda v: None if v is None else {k: number(getattr(v, k)) for k in ("x", "y", "z")}),
                   "relationship_track": read("rel_track", lambda v: None if v is None else
                       self.runtime.adapter.resource(v, resource_kind="statistic")),
                   "delivery": "distribute_returned_true", "client_visibility": "unverified",
                   "icon_semantics": "unmapped", "scope_evidence": "subject_local_at_send"}
        if missing:
            payload["unavailable_fields"] = missing
        return copy_data(payload)

    def on_distribute(self, args, kwargs, result):
        if self.runtime.closed or result is not True:
            return
        try:
            request = args[0]
            sim = request._sim
            if not getattr(sim, "is_sim", False) or not self.runtime.adapter.in_scope(sim):
                return
            # Loading-origin effects do not become newly observed life events.
            if any(frame.get("from_load") for frame in self.runtime.sources.frames):
                return
            provenance = self.request_provenance(request)
            if provenance is not None and provenance["from_load"]:
                return
            subject = self.runtime.adapter.reference(sim)
            payload, now = self.snapshot(request), self.runtime.adapter.clock()
            cause = provenance["cause"] if provenance is not None else None
            cause = cause or self.runtime.sources.cause()
            payload["association"] = "recorded_source" if cause else "unavailable"
            observation_id = new_id()
            self.runtime.recorder.fact(CATEGORY, [subject], payload, now, SOURCE,
                roles=[{"entity_key": subject["key"], "role": "subject", "basis": "balloon_request_sim"}],
                cause=cause, evidence="distributor_request", event_id=self.runtime.session_id + ":balloon:" + observation_id)
            self.sent += 1
        except Exception as exc:
            self.error = "{}: {}".format(type(exc).__name__, exc)
            if self.runtime.recorder.enabled:
                self.runtime.fail(SOURCE + ": " + self.error)

    def read(self, target, window=None):
        from context_overlay.balloon_query import read
        return read(self, target, window)

    def close(self):
        self.pending.clear()
        self.pending_bytes = 0
