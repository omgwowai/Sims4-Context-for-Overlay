"""Read balloon sends from canonical history; keep no independent event cache."""

from context_overlay.history import HistoryError, ticks
from context_overlay.model import field

DEFAULT_MINUTES = 5
DEFAULT_LIMIT = 50


def validate_window(window):
    if window is None:
        window = {}
    if not isinstance(window, dict) or set(window) - {"past_sim_minutes", "from_ticks", "to_ticks", "limit"}:
        raise HistoryError("invalid_query", "balloon_window accepts past_sim_minutes or from_ticks/to_ticks, and limit")
    limit = window.get("limit", DEFAULT_LIMIT)
    if type(limit) is not int or not 1 <= limit <= 500:
        raise HistoryError("invalid_query", "balloon_window.limit must be an integer from 1 to 500")
    absolute = "from_ticks" in window or "to_ticks" in window
    if absolute:
        if "past_sim_minutes" in window or window.get("from_ticks") is None or window.get("to_ticks") is None:
            raise HistoryError("invalid_query", "Provide both from_ticks and to_ticks without past_sim_minutes")
        lower, upper = ticks(window["from_ticks"]), ticks(window["to_ticks"])
        if lower >= upper:
            raise HistoryError("invalid_query", "Time range is [from, to), with from < to")
        return {"from_ticks": str(lower), "to_ticks": str(upper), "limit": limit}
    minutes = window.get("past_sim_minutes", DEFAULT_MINUTES)
    if type(minutes) not in (int, float) or not 0 < minutes <= 10080:
        raise HistoryError("invalid_query", "past_sim_minutes must be positive and at most 10080 (one game week)")
    return {"past_sim_minutes": minutes, "limit": limit}


def read(capture, target, window=None):
    window = validate_window(window)
    runtime, coverage = capture.runtime, capture.status()
    recorder = runtime.recorder
    state = recorder.status()
    if not recorder.enabled:
        return field(status="disabled", source="Recorder.events", reason="recorder_disabled")
    if state["state"] != "recording":
        return field(status="error", source="Recorder.events", reason=state.get("error") or "recorder_failed")
    if capture.error:
        return field(status="error", source="Recorder.events", reason=capture.error)
    if coverage["coverage"]["send"]["state"] != "installed":
        return field(status="unsupported", source="Recorder.events", reason="balloon_send_hook_unavailable")
    now = runtime.adapter.clock()
    if "past_sim_minutes" in window:
        span = runtime.adapter.sim_minutes_to_ticks(window["past_sim_minutes"])
        if type(span) is not int or span < 1:
            raise HistoryError("invalid_query", "Time window must cover at least one game tick")
        window.update(from_ticks=str(ticks(now) - span), to_ticks=str(ticks(now) + 1))
    options = {"from_ticks": window["from_ticks"], "to_ticks": window["to_ticks"],
               "event_types": ["game_event"], "fields": ["balloon.sent"], "entity_role": "subject",
               "zone_visit": recorder.zone_visit, "order": "desc", "page_size": window["limit"]}
    page = recorder.query_history(target["key"], target=target, **options)
    # A Context read owns no cursor. The same filters can open a paged history
    # query; persisted Events can recover anything outside the retained index.
    try:
        gap = page["coverage"]["evicted_events"] > 0
        value = {"format": "balloon_event_window_v1", "events": page["events"], "window": window, "as_of_time": now,
                 "as_of_sequence": page["as_of_sequence"], "scope": "current_zone_visit_event_window",
                 "zone_visit": recorder.zone_visit, "client_visibility": "unverified",
                 "total_matches": page["total_matches"], "has_more": page["has_more"],
                 "retention_gap": gap, "complete": not gap and not page["has_more"],
                 "coverage": {"capture": coverage, "recording": page["coverage"],
                              "retention_basis": "conservative_any_session_fifo_eviction",
                              "meaning": "recorded_sends_not_all_visible_balloons"},
                 "history_query": dict(options, kind="sim", identifier=target["id"]),
                 "durable_query": {"view": "events", "kind": "sim", "identifier": target["id"],
                     "from_ticks": window["from_ticks"], "to_ticks": window["to_ticks"],
                     "fields": ["balloon.sent"], "entity_role": "subject", "zone_visit": recorder.zone_visit,
                     "order": "desc"}}
        return field(value, source="Recorder.events")
    finally:
        recorder.close_query(page["cursor"])
