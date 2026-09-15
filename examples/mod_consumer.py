"""Minimal direct public API consumer, requiring ContextOverlay 0.5.0+ / API v1.

Call from a game interaction/alarm callback after the zone is ready.
For an optional dependency and managed cursors, use sdk/context_overlay_client.py.
"""


def capture_context(kind="sim", identifier="active"):
    from context_overlay import api
    return api.get_context(kind, identifier, history_limit=15)


def open_history(kind="sim", identifier="active", **filters):
    from context_overlay import api
    packet = api.query_history(kind, identifier, **filters)
    return {"session_id": packet["session_id"], "page": packet["history"]}


def next_history(session_id, cursor):
    from context_overlay import api
    return api.get_history_page(cursor, expected_session_id=session_id)["history"]


def close_history(session_id, cursor):
    from context_overlay import api
    return api.close_history(cursor, expected_session_id=session_id)
