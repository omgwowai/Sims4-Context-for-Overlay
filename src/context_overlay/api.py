"""Public ContextOverlay API v1. Importing this module never starts the MOD.

Only get_api_info is independent of the simulation thread. All returned data
is detached and JSON-safe. Runtime objects and recorder internals stay private.
"""

import functools
import inspect
import threading

from context_overlay import SCHEMA_VERSION, VERSION
from context_overlay.collector import FIELDS
from context_overlay.history import HistoryError
from context_overlay.model import copy_data, envelope, new_id
from context_overlay.nearby import MAX_RESULTS, MAX_SCANNED, NearbyError, validate as validate_nearby
from context_overlay.semanticizer import render


API_VERSION = "1.1.0"
__all__ = ["API_VERSION", "APIError", "get_api_info", "get_status", "get_context",
           "query_history", "get_history_page", "close_history", "get_nearby_entities"]
_PRESETS = {"sim": ("identity", "time", "location", "needs", "buffs", "relationships", "interactions"),
            "object": ("identity", "time", "location", "object_states")}


class APIError(RuntimeError):
    """Stable machine-readable code; messages and details may evolve."""
    def __init__(self, code, message, details=None):
        self.code = code
        self.message = message
        self.details = copy_data(details or {})
        super().__init__(code + ": " + message)

    def to_dict(self):
        return copy_data({"code": self.code, "message": self.message, "details": self.details})


def _endpoint(function):
    signature = inspect.signature(function)
    @functools.wraps(function)
    def call(*args, **kwargs):
        try:
            signature.bind(*args, **kwargs)
        except TypeError as exc:
            raise APIError("invalid_request", str(exc)) from None
        try:
            return function(*args, **kwargs)
        except APIError:
            raise
        except NearbyError as exc:
            raise APIError(exc.code, str(exc), exc.details) from None
        except HistoryError as exc:
            raise APIError(exc.code, str(exc)) from None
        except Exception as exc:
            raise APIError("internal_error", "ContextOverlay could not complete the request",
                           {"exception_type": type(exc).__name__, "reason": str(exc)}) from None
    return call


def _session(value, required=False):
    if value is None and not required:
        return
    if not isinstance(value, str) or not value or len(value) > 128:
        raise APIError("invalid_request", "expected_session_id must be a nonempty session string")


def _provider_state():
    from context_overlay import game_runtime
    runtime = game_runtime._runtime
    # The owner is the thread that initialized this run, which need not be
    # Python's main_thread in an embedded interpreter. Check before any reads.
    if runtime is not None and threading.get_ident() != runtime.simulation_thread_id:
        raise APIError("wrong_thread", "Call ContextOverlay from the simulation thread")
    if game_runtime._startup_error:
        return runtime, "startup_failed"
    if runtime is None:
        return None, "waiting_for_zone"
    if runtime.closed:
        return runtime, "closed"
    return runtime, "ready" if runtime.api_ready else "starting"


def _current(expected_session_id=None):
    _session(expected_session_id)
    runtime, state = _provider_state()
    if expected_session_id is not None and runtime is not None and runtime.session_id != expected_session_id:
        raise APIError("session_changed", "Discard data/cursors from the previous run",
                       {"expected_session_id": expected_session_id, "session_id": runtime.session_id})
    if state != "ready":
        raise APIError("session_closed" if state == "closed" else "not_ready",
                       "ContextOverlay is not ready; wait for the zone to load", {"state": state})
    return runtime


def _identifier(kind, identifier):
    if kind not in ("sim", "object"):
        raise APIError("invalid_request", "kind must be sim or object")
    if kind == "sim" and identifier == "active":
        return "active"
    if isinstance(identifier, bool) or not isinstance(identifier, (int, str)):
        raise APIError("invalid_request", "Use an entity instance ID, not a game object or float")
    text = str(identifier)
    if not text or len(text) > 20 or any(char not in "0123456789" for char in text) or not 0 < int(text) < 2 ** 64:
        raise APIError("invalid_request", "Entity ID must be a positive unsigned 64-bit decimal value")
    return str(int(text))


def _boolean(value, name):
    if not isinstance(value, bool):
        raise APIError("invalid_request", name + " must be a boolean")


def _representation(value):
    if value not in ("raw", "text", "both"):
        raise APIError("invalid_request", "representation must be raw, text or both")


def _resolve(runtime, kind, identifier):
    try:
        return runtime.adapter.resolve(kind, identifier)
    except ValueError as exc:
        raise APIError("target_unavailable", str(exc)) from None


@_endpoint
def get_api_info():
    """Pure capability/version discovery, safe before game load or on a worker."""
    from context_overlay.event_sources import LABELS
    return {"api_version": API_VERSION, "module_version": VERSION, "schema_version": SCHEMA_VERSION,
            "capabilities": ["context.read", "history.query", "history.page", "history.close", "text.zh-CN",
                             "history.effects", "history.retained_identity", "history.fifo", "events.gameplay",
                             "context.nearby_entities", "text.resource_details"],
            "resource_text": {"roles": ["name", "description", "tooltip"],
                              "details": "optional_per_resource", "evidence": "hash_and_observed_tokens",
                              "strings": "build_time_official_CHS_CN", "third_party_overrides": "not_verified"},
            "nearby": {"kinds": ["sim", "object"], "metrics": ["horizontal", "euclidean"],
                       "max_results": MAX_RESULTS, "max_scanned": MAX_SCANNED,
                       "max_radius": 1000000, "unit": "game_world_units", "room_filter": True},
            "event_types": ["interaction", "state_change", "game_event"], "event_categories": list(LABELS),
            "retention_policy": "fifo_first_accepted",
            "context_fields": list(FIELDS), "default_fields": copy_data(_PRESETS),
            "max_history_page_size": 500, "max_context_history_limit": 500,
            "thread_policy": "simulation_thread", "transport": "in_process_python",
            "scope": "active_lot_instantiated", "history_scope": "current_session"}


@_endpoint
def get_status():
    """Runtime readiness is separate from recording/translation availability."""
    runtime, state = _provider_state()
    result = {"api_version": API_VERSION, "module_version": VERSION, "schema_version": SCHEMA_VERSION,
              "ready": state == "ready", "state": state,
              "session_id": runtime.session_id if runtime is not None else None}
    if state == "ready":
        result.update({"modules": {"collector_enabled": runtime.collector.enabled,
                                   "semanticizer_enabled": runtime.collector.semantic_enabled,
                                   "recorder_enabled": runtime.recorder.enabled},
                       "recorder": runtime.recorder.status(),
                       "query_limits": {"max_queries": runtime.recorder.index.snapshot_limit,
                                        "max_references": runtime.recorder.index.snapshot_ref_limit,
                                        "max_bytes": runtime.recorder.index.snapshot_byte_limit,
                                        "ttl_seconds": runtime.recorder.index.snapshot_ttl}})
        result["event_coverage"] = runtime.sources.status() if hasattr(runtime, "sources") else {}
        result["event_diagnostics"] = runtime.sources.diagnostics() if hasattr(runtime, "sources") else {}
    return copy_data(result)


@_endpoint
def get_context(kind="sim", identifier="active", *, fields=None, include_history=True,
                history_limit=15, include_internal=False, representation="both", expected_session_id=None):
    """Read selected fields and bounded recent history without writing a file."""
    identifier = _identifier(kind, identifier)
    _boolean(include_history, "include_history")
    _boolean(include_internal, "include_internal")
    _representation(representation)
    if isinstance(history_limit, bool) or not isinstance(history_limit, int) or not 1 <= history_limit <= 500:
        raise APIError("invalid_request", "history_limit must be an integer between 1 and 500")
    selected = _PRESETS[kind] if fields is None else fields
    if (not isinstance(selected, (list, tuple)) or not selected or len(selected) > len(FIELDS)
            or any(not isinstance(name, str) or name not in FIELDS for name in selected)
            or len(set(selected)) != len(selected)):
        raise APIError("invalid_request", "fields must select distinct supported context fields")
    runtime = _current(expected_session_id)
    if not runtime.collector.enabled:
        raise APIError("collector_disabled", "Context collection is disabled")
    # Resolve active once, then pass its fixed identity to the collector.
    target = _resolve(runtime, kind, identifier)
    packet = runtime.collector.collect(kind, target["id"], fields=selected, history_limit=history_limit,
                                       include_history=include_history, include_internal=include_internal,
                                       representation=representation)
    packet["api_version"] = API_VERSION
    return copy_data(packet)


@_endpoint
def get_nearby_entities(identifier="active", *, kinds=("sim",), radius=None,
                        metric="horizontal", same_level=True, same_room=False,
                        include_self=False, limit=32, expected_session_id=None):
    """Find world entities near a Sim; no history, persistence or query handle."""
    identifier = _identifier("sim", identifier)
    query = validate_nearby(kinds, radius, metric, same_level, same_room, include_self, limit)
    runtime = _current(expected_session_id)
    if not runtime.collector.enabled:
        raise APIError("collector_disabled", "Context collection is disabled")
    target = _resolve(runtime, "sim", identifier)
    packet = runtime.collector.nearby(target, query)
    packet["api_version"] = API_VERSION
    return copy_data(packet)


def _history_packet(runtime, page, representation):
    packet = envelope("history", runtime.session_id)
    packet.update({"api_version": API_VERSION, "request_id": new_id(), "target": page["target"],
                   "provenance": runtime.provenance, "history": page, "representation": representation,
                   "status": "complete" if page["status"] == "recording" else "partial"})
    if representation != "raw":
        if runtime.collector.semantic_enabled:
            packet["rendered"] = render(packet)
        else:
            packet["rendered"] = {"status": "disabled", "reason": "Semanticizer is disabled"}
            packet["status"] = "partial"
    return copy_data(packet)


@_endpoint
def query_history(kind="sim", identifier="active", *, page_size=15, include_internal=False,
                  time_field="first_observed", from_ticks=None, to_ticks=None, event_types=None,
                  fields=None, outcomes=None, tuning_ids=None, order="desc", representation="both",
                  expected_session_id=None, group_effects=False):
    """Create a bounded snapshot; its cursors must be closed or allowed to expire."""
    identifier = _identifier(kind, identifier)
    _representation(representation)
    runtime = _current(expected_session_id)
    target = runtime.recorder.references.get("{}:{}".format(kind, identifier))
    if target is None:
        target = _resolve(runtime, kind, identifier)
    page = runtime.recorder.query_history(target["key"], target=target, page_size=page_size,
        include_internal=include_internal, time_field=time_field, from_ticks=from_ticks, to_ticks=to_ticks,
        event_types=event_types, fields=fields, outcomes=outcomes, tuning_ids=tuning_ids, order=order,
        group_effects=group_effects)
    try:
        return _history_packet(runtime, page, representation)
    except Exception:
        runtime.recorder.close_query(page["cursor"])
        raise


@_endpoint
def get_history_page(cursor, *, expected_session_id, representation="both"):
    """Fetch the immutable event revisions identified by an opaque next_cursor."""
    _session(expected_session_id, required=True)
    _representation(representation)
    runtime = _current(expected_session_id)
    return _history_packet(runtime, runtime.recorder.history_page(cursor), representation)


@_endpoint
def close_history(cursor, *, expected_session_id):
    """Idempotent cleanup: expired/ended runs count as already released."""
    _session(expected_session_id, required=True)
    if not isinstance(cursor, str) or not cursor or len(cursor) > 200:
        raise APIError("invalid_cursor", "Expected an opaque history cursor")
    try:
        runtime = _current(expected_session_id)
        runtime.recorder.close_query(cursor)
    except (APIError, HistoryError) as exc:
        if exc.code not in ("cursor_expired", "session_changed", "session_closed", "not_ready"):
            raise
        return {"api_version": API_VERSION, "released": False, "reason": exc.code}
    return {"api_version": API_VERSION, "released": True, "reason": "closed"}
