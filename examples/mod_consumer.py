"""Copy into your own Python 3.7 MOD under its own package name.

Experimental integration pinned to ContextOverlay 0.3.2, not a stable SDK.
Call only on the game's simulation thread after the zone is ready.
"""


def _current_runtime():
    from context_overlay import VERSION, game_runtime
    if VERSION != "0.3.2":
        raise RuntimeError("This integration example expects ContextOverlay 0.3.2")
    runtime = game_runtime._runtime
    if runtime is None or runtime.closed or game_runtime._startup_error:
        raise RuntimeError("ContextOverlay is not ready; wait for the zone to load")
    return runtime


def capture_context(kind="sim", identifier="active"):
    runtime = _current_runtime()
    return runtime.collector.collect(
        kind, identifier,
        fields=("identity", "time", "location", "needs", "buffs", "relationships", "interactions")
        if kind == "sim" else ("identity", "time", "location", "object_states"),
        include_history=True, history_limit=15,
        include_internal=False, representation="both")


def open_history(kind="sim", identifier="active", **filters):
    runtime = _current_runtime()
    target = runtime.adapter.resolve(kind, identifier)
    options = {"page_size": 15, "include_internal": False,
               "time_field": "first_observed", "order": "desc"}
    options.update(filters)
    page = runtime.recorder.query_history(target["key"], target=target, **options)
    return {"session_id": runtime.session_id, "page": page}


def next_history(session_id, cursor):
    runtime = _current_runtime()
    if runtime.session_id != session_id:
        raise RuntimeError("Session changed; discard the old cursor and query again")
    return runtime.recorder.history_page(cursor)


def close_history(session_id, cursor):
    runtime = _current_runtime()
    if runtime.session_id == session_id:
        runtime.recorder.close_query(cursor)
