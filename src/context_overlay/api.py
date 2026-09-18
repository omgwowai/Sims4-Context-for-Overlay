"""Public ContextOverlay API v2. Importing this module never starts the MOD.

Only get_api_info is independent of the simulation thread. All returned data
is detached and JSON-safe. Runtime objects and recorder internals stay private.
"""

import functools
import inspect
import threading

from context_overlay import SCHEMA_VERSION, VERSION
from context_overlay.collector import FIELDS, PRESETS
from context_overlay.history import HistoryError
from context_overlay.external import ExternalError, LIMITS
from context_overlay.model import copy_data
from context_overlay.localization import FORMAT_PROFILE
from context_overlay.nearby import MAX_RESULTS, MAX_SCANNED, NearbyError, validate as validate_nearby
from context_overlay.view_source import ViewError


API_VERSION = "2.2.0"
__all__ = ["API_VERSION", "APIError", "get_api_info", "get_status", "get_context",
           "query_history", "get_history_page", "close_history", "get_nearby_entities",
           "append_event", "read_event_changes", "query_event_view", "get_event_view_status",
           "get_event_view_page", "explain_event_view", "close_event_view"]


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
        except ExternalError as exc:
            raise APIError(exc.code, str(exc), exc.details) from None
        except (HistoryError, ViewError) as exc:
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


def _resolve(runtime, kind, identifier, history=False):
    try:
        if history:
            return runtime.collector.resolve_history(kind, identifier)
        return runtime.adapter.resolve(kind, identifier)
    except ValueError as exc:
        raise APIError("target_unavailable", str(exc)) from None


@_endpoint
def get_api_info():
    """Pure capability/version discovery, safe before game load or on a worker."""
    from context_overlay.semanticizer import LABELS
    return {"api_version": API_VERSION, "module_version": VERSION, "schema_version": SCHEMA_VERSION,
            "capabilities": ["context.read", "history.query", "history.page", "history.close", "text.zh-CN",
                             "history.effects", "history.retained_identity", "history.fifo", "events.gameplay",
                             "context.nearby_entities", "text.resource_details", "events.autonomy_decision",
                             "events.append", "history.sources", "history.global", "history.changes", "history.travel",
                             "event_views.query", "event_views.explain", "event_views.durable_session"],
            "event_views": {"schema_version": "event_views_v1", "views": ["records", "events", "organized", "recap"],
                            "sources": ["durable_session"], "profiles": ["recap_v1"], "max_page_size": 100,
                            "construction": "asynchronous_in_process_worker", "time_windows": "whole_durable_session",
                            "facets": ["lineage", "policy", "labels", "revisions", "events", "units"]},
            "session_lifecycle": {"travel": "preserved", "reload": "new_session", "restart": "new_session",
                                  "loading": "temporarily_unavailable", "query_ttl": "wall_clock"},
            "external_events": dict(LIMITS, payload="opaque_json", writes="append_only",
                                    time_basis="received", default_origin_filter="all"),
            "changes": {"revision_policy": "latest_per_event", "checkpoint_scope": "current_session",
                        "gap_detection": "conservative_all_sources"},
            "autonomy": {"category": "autonomy.decision", "default_top_n_per_stage": 5,
                         "retention_gates": ["queue_success", "immediate_entered"],
                         "probabilities": "original_complete_stage_pool", "runtime_status": "autonomy"},
            "resource_text": {"roles": ["name", "description", "tooltip"],
                              "details": "optional_per_resource", "evidence": "hash_and_observed_tokens",
                              "rendered_details": "rendered.resource_details", "max_rendered_details": 128,
                              "format_profile": FORMAT_PROFILE,
                              "strings": "build_time_official_CHS_CN", "third_party_overrides": "not_verified"},
            "nearby": {"kinds": ["sim", "object"], "metrics": ["horizontal", "euclidean"],
                       "max_results": MAX_RESULTS, "max_scanned": MAX_SCANNED,
                       "max_radius": 1000000, "unit": "game_world_units", "room_filter": True},
            "event_types": ["interaction", "state_change", "game_event", "external_event"], "event_categories": list(LABELS),
            "retention_policy": "fifo_first_accepted",
            "context_fields": list(FIELDS), "default_fields": copy_data(PRESETS),
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
        result["event_coverage"] = runtime.sources.status()
        result["event_diagnostics"] = runtime.sources.diagnostics()
        result["event_views"] = runtime.event_views.metrics() if getattr(runtime, "event_views", None) is not None else {"state": "not_started"}
        result["autonomy"] = runtime.autonomy.status()
    return copy_data(result)


@_endpoint
def get_context(kind="sim", identifier="active", *, fields=None, include_history=True,
                history_limit=15, include_internal=False, representation="both", expected_session_id=None,
                origins=None, producers=None):
    """Read selected fields and bounded recent history without writing a file."""
    identifier = _identifier(kind, identifier)
    _boolean(include_history, "include_history")
    _boolean(include_internal, "include_internal")
    _representation(representation)
    from context_overlay.history import HistoryIndex
    HistoryIndex.source_filters(origins, producers)
    if isinstance(history_limit, bool) or not isinstance(history_limit, int) or not 1 <= history_limit <= 500:
        raise APIError("invalid_request", "history_limit must be an integer between 1 and 500")
    selected = PRESETS[kind] if fields is None else fields
    if (not isinstance(selected, (list, tuple)) or not selected or len(selected) > len(FIELDS)
            or any(not isinstance(name, str) or name not in FIELDS for name in selected)
            or len(set(selected)) != len(selected)):
        raise APIError("invalid_request", "fields must select distinct supported context fields")
    runtime = _current(expected_session_id)
    if not runtime.collector.enabled:
        raise APIError("collector_disabled", "Context collection is disabled")
    target = _resolve(runtime, kind, identifier)
    packet = runtime.collector.collect(target, fields=selected, history_limit=history_limit,
                                       include_history=include_history, include_internal=include_internal,
                                       representation=representation, origins=origins, producers=producers)
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
    return packet


@_endpoint
def query_history(kind="sim", identifier="active", *, page_size=15, include_internal=False,
                  time_field="first_observed", from_ticks=None, to_ticks=None, event_types=None,
                  fields=None, outcomes=None, tuning_ids=None, order="desc", representation="both",
                  expected_session_id=None, group_effects=False, origins=None, producers=None):
    """Create a bounded snapshot; its cursors must be closed or allowed to expire."""
    if kind is not None or identifier is not None:
        identifier = _identifier(kind, identifier)
    _representation(representation)
    runtime = _current(expected_session_id)
    target = _resolve(runtime, kind, identifier, history=True) if kind is not None else None
    packet = runtime.collector.query_history(target, representation, page_size=page_size,
        include_internal=include_internal, time_field=time_field, from_ticks=from_ticks, to_ticks=to_ticks,
        event_types=event_types, fields=fields, outcomes=outcomes, tuning_ids=tuning_ids, order=order,
        group_effects=group_effects, origins=origins, producers=producers)
    return dict(packet, api_version=API_VERSION)


@_endpoint
def append_event(producer, payload, *, entities=None, idempotency_key=None, expected_session_id):
    """Append opaque JSON on the simulation thread; retries may reuse a key."""
    _session(expected_session_id, required=True)
    runtime = _current(expected_session_id)
    result = runtime.recorder.external.append(producer, payload, entities, idempotency_key, runtime.adapter.clock())
    return dict(result, api_version=API_VERSION, schema_version=SCHEMA_VERSION, module_version=VERSION)


@_endpoint
def read_event_changes(checkpoint=None, *, start=None, kind=None, identifier=None, origins=None,
                       producers=None, include_internal=None, page_size=50, representation="both", expected_session_id):
    """Freeze latest changed revisions; commit checkpoint only after the last page."""
    _session(expected_session_id, required=True)
    _representation(representation)
    if checkpoint is not None and any(v is not None for v in (start, kind, identifier, origins, producers, include_internal)):
        raise APIError("invalid_request", "Checkpoint binds the initialization filters; omit them on continuation")
    if kind is not None or identifier is not None:
        identifier = _identifier(kind, identifier)
    runtime = _current(expected_session_id)
    target = _resolve(runtime, kind, identifier, history=True) if kind is not None else None
    page = runtime.recorder.read_changes(checkpoint=checkpoint, start=start,
        entity_key=target["key"] if target else None, origins=origins, producers=producers,
        include_internal=include_internal, page_size=page_size)
    try:
        packet = runtime.collector.history_packet(page, representation)
    except Exception:
        runtime.recorder.close_query(page["cursor"])
        raise
    return dict(packet, api_version=API_VERSION)


@_endpoint
def get_history_page(cursor, *, expected_session_id, representation="both"):
    """Fetch the immutable event revisions identified by an opaque next_cursor."""
    _session(expected_session_id, required=True)
    _representation(representation)
    runtime = _current(expected_session_id)
    packet = runtime.collector.history_packet(runtime.recorder.history_page(cursor), representation)
    return dict(packet, api_version=API_VERSION)


@_endpoint
def close_history(cursor, *, expected_session_id):
    """Idempotent cleanup: expired/ended runs count as already released."""
    _session(expected_session_id, required=True)
    if not isinstance(cursor, str) or not cursor or len(cursor) > 200:
        raise APIError("invalid_cursor", "Expected an opaque history cursor")
    try:
        runtime, state = _provider_state()
        if not (runtime is not None and runtime.session_id == expected_session_id and
                state == "closed" and getattr(runtime, "history_suspended", False)):
            runtime = _current(expected_session_id)
        # Closing a retained query during travel touches no game objects and
        # must actually release it, even though reads/writes await the new lot.
        runtime.recorder.close_query(cursor)
    except (APIError, HistoryError) as exc:
        if exc.code not in ("cursor_expired", "session_changed", "session_closed", "not_ready"):
            raise
        return {"api_version": API_VERSION, "released": False, "reason": exc.code}
    return {"api_version": API_VERSION, "released": True, "reason": "closed"}


def _views(runtime):
    if getattr(runtime, "event_views", None) is None:
        from context_overlay.event_views import ViewStore
        config = runtime.config
        runtime.event_views = ViewStore(runtime.writer.path, runtime.session_id,
            runtime.provenance.get("build_game_version"),
            max_queries=config["event_view_query_limit"], memory_bytes=config["event_view_memory_mb"] * 1024 * 1024,
            source_bytes=config["event_view_source_mb"] * 1024 * 1024, ttl=config["event_view_ttl_seconds"],
            build_seconds=config["event_view_build_seconds"])
    return runtime.event_views


@_endpoint
def query_event_view(view="recap", kind="sim", identifier="active", *, source="durable_session",
                     source_snapshot_id=None, profile="recap_v1", page_size=20, expected_session_id):
    """Start a bounded durable view; poll status, then request its first cursor."""
    _session(expected_session_id, required=True)
    identifier = _identifier(kind, identifier)
    if source != "durable_session":
        raise APIError("invalid_request", "Only durable_session is supported by event views")
    if source_snapshot_id is not None and (not isinstance(source_snapshot_id, str) or len(source_snapshot_id) != 32):
        raise APIError("invalid_request", "Expected an opaque source_snapshot_id")
    runtime = _current(expected_session_id)
    # Explicit historical IDs must not depend on the live lot or FIFO identity cache.
    key = _resolve(runtime, kind, identifier)["key"] if identifier == "active" else kind + ":" + identifier
    head = runtime.recorder.status()
    head = dict(head.get("persistence", {}), recorder_state=head.get("state"),
                capture_scope="active_lot_instantiated")
    return dict(_views(runtime).query(view, key, head, source_snapshot_id, profile, page_size), api_version=API_VERSION)


@_endpoint
def get_event_view_status(request_id, *, expected_session_id):
    _session(expected_session_id, required=True)
    return dict(_views(_current(expected_session_id)).status(request_id), api_version=API_VERSION)


@_endpoint
def get_event_view_page(cursor, *, expected_session_id):
    _session(expected_session_id, required=True)
    return dict(_views(_current(expected_session_id)).page(cursor), api_version=API_VERSION)


@_endpoint
def explain_event_view(snapshot_id, item_id, *, facet="lineage", page_size=20, expected_session_id):
    _session(expected_session_id, required=True)
    if not isinstance(snapshot_id, str) or len(snapshot_id) != 64:
        raise APIError("invalid_request", "Expected an event-view snapshot_id")
    return dict(_views(_current(expected_session_id)).explain(snapshot_id, item_id, facet, page_size), api_version=API_VERSION)


@_endpoint
def close_event_view(request_id, *, expected_session_id):
    """Cancel a build or release a ready/failed request; safe during travel."""
    _session(expected_session_id, required=True)
    if not isinstance(request_id, str) or len(request_id) != 32:
        raise APIError("invalid_request", "Expected an event-view request_id")
    runtime, state = _provider_state()
    if runtime is None or runtime.session_id != expected_session_id or getattr(runtime, "event_views", None) is None:
        return {"api_version": API_VERSION, "released": False}
    return dict(runtime.event_views.close(request_id), api_version=API_VERSION)
