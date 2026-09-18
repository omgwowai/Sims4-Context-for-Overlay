"""User-invoked public API smoke check; writes two labelled external records."""

import json
import os

from context_overlay import api
from context_overlay.model import new_id, utc_now


def save(path, report):
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(pending), str(path))


def run(path):
    info = api.get_api_info()
    context = api.get_context(fields=["identity", "time"], include_history=False)
    session = context["session_id"]
    token = new_id()
    producer = "context_overlay.selftest." + token
    report = {"created_at": utc_now(), "session_id": session, "producer": producer,
              "target": context["target"], "scope": context["scope"],
              "zone_visit": api.get_status()["recorder"]["zone_visit"],
              "versions": info, "checks": [], "passed": False}
    cursors = []

    def check(name, condition):
        report["checks"].append({"name": name, "passed": bool(condition)})
        if not condition:
            raise ValueError("Check failed: " + name)

    def remember(packet):
        cursors.append(packet["history"]["cursor"])
        return packet["history"]

    try:
        game_history = remember(api.query_history(None, None, origins=["game"], page_size=5,
                                                  expected_session_id=session))
        report["game_event_ids"] = [event["event_id"] for event in game_history["events"]]
        initial = remember(api.read_event_changes(start="now", producers=[producer], expected_session_id=session))
        report["checkpoint"] = initial["checkpoint"]
        payload = {"text": "Overlay 接口自检：中文与 JSON 读写正常。", "private_state": [1, True, None],
                   "status": "consumer_defined"}
        first = api.append_event(producer, payload, entities=[context["target"]["key"]],
                                 idempotency_key=token, expected_session_id=session)
        second = api.append_event(producer, {"text": "没有实体关联的测试记录"}, expected_session_id=session)
        retry = api.append_event(producer, payload, entities=[context["target"]["key"]],
                                 idempotency_key=token, expected_session_id=session)
        report["receipts"] = [first, second]
        check("retry_same_event", retry["duplicate"] and retry["event_id"] == first["event_id"])
        try:
            api.append_event(producer, {"changed": True}, idempotency_key=token, expected_session_id=session)
        except api.APIError as exc:
            check("conflict_rejected", exc.code == "idempotency_conflict")
        else:
            check("conflict_rejected", False)
        history = remember(api.query_history(None, None, producers=[producer], order="asc", expected_session_id=session))
        check("global_read_and_json", len(history["events"]) == 2 and history["events"][0]["payload"] == payload
              and history["events"][1]["entities"] == [])
        filtered = remember(api.query_history(None, None, origins=["game"], producers=[producer], expected_session_id=session))
        check("game_filter_excludes_external", filtered["events"] == [])
        mixed = api.get_context(context["target"]["kind"], context["target"]["id"],
                                fields=["identity"], producers=[producer], expected_session_id=session)
        check("context_default_includes_external", len(mixed["history"]["events"]) == 1)
        delta = remember(api.read_event_changes(initial["checkpoint"], page_size=1, expected_session_id=session))
        check("delta_first_page", delta["has_more"] and delta["checkpoint"] is None and len(delta["events"]) == 1)
        tail = api.get_history_page(delta["next_cursor"], expected_session_id=session)["history"]
        check("delta_last_page", not tail["has_more"] and tail["checkpoint"] is not None
              and tail["events"][0]["event_id"] == second["event_id"])
        repeated = api.get_history_page(delta["next_cursor"], expected_session_id=session)["history"]
        check("page_retry", repeated["events"] == tail["events"])
        empty = remember(api.read_event_changes(tail["checkpoint"], expected_session_id=session))
        check("delta_continuation_empty", empty["events"] == [])
        report["checkpoint"] = empty["checkpoint"]
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        for cursor in cursors:
            api.close_history(cursor, expected_session_id=session)
        save(path, report)
    return {"passed": report["passed"], "checks": len(report["checks"]), "report": str(path),
            "producer": producer, "target": context["target"], "inspect_command": "co.api_inspect",
            "error": report.get("error")}


def inspect_target(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    status = api.get_status()
    if not status["ready"] or status["session_id"] != report["session_id"]:
        raise ValueError("Run co.api_test in this session first")
    return report["target"]


def verify_travel(report, current):
    session = current["session_id"]
    cursors = []

    def history(packet):
        cursors.append(packet["history"]["cursor"])
        return packet["history"]

    try:
        global_page = history(api.query_history(None, None, producers=[report["producer"]],
                                                expected_session_id=session))
        linked_page = history(api.query_history(report["target"]["kind"], report["target"]["id"],
                                               producers=[report["producer"]], expected_session_id=session))
        delta = history(api.read_event_changes(report["checkpoint"], expected_session_id=session))
        first = report["receipts"][0]
        event = next((event for event in global_page["events"] if event["event_id"] == first["event_id"]), None)
        duplicate = api.append_event(report["producer"], event["payload"], entities=event["entities"],
            idempotency_key=report["producer"].split(".")[-1], expected_session_id=session) if event else None
        # The source event time is unchanged; receipt time is not rewritten by a retry.
        checks = {
            "global_external_retained": {event["event_id"] for event in global_page["events"]} ==
                                        {receipt["event_id"] for receipt in report["receipts"]},
            "entity_external_retained": [event["event_id"] for event in linked_page["events"]] == [first["event_id"]],
            "checkpoint_continues": not delta["has_more"] and delta["events"] == [],
            "dedup_continues": bool(duplicate and duplicate["duplicate"] and duplicate["event_id"] == first["event_id"]),
        }
        # Inspect retained game evidence through the public, paginated API too.
        remaining = set(report.get("game_event_ids", []))
        if remaining:
            page = history(api.query_history(None, None, origins=["game"], page_size=500, expected_session_id=session))
            while True:
                remaining.difference_update(event["event_id"] for event in page["events"])
                if not remaining or not page["has_more"]:
                    break
                page = api.get_history_page(page["next_cursor"], expected_session_id=session)["history"]
            checks["game_history_retained"] = not remaining
        return {"check": "travel_history", "passed": all(checks.values()), "checks": checks,
                "session_id": session, "previous_scope": report.get("scope"),
                "current_scope": current["recorder"]["observation_scope"],
                "zone_visit": current["recorder"]["zone_visit"],
                "evicted_events": current["recorder"]["evicted_events"]}
    finally:
        for cursor in cursors:
            api.close_history(cursor, expected_session_id=session)


def verify(path):
    report = json.loads(path.read_text(encoding="utf-8"))
    current = api.get_status()
    if not current["ready"]:
        raise ValueError("Wait for a loaded lot")
    if (current["session_id"] == report["session_id"] and
            current["recorder"]["zone_visit"] > report.get("zone_visit", 0)):
        try:
            result = verify_travel(report, current)
        except Exception as exc:
            result = {"check": "travel_history", "passed": False, "error": str(exc),
                      "code": getattr(exc, "code", None)}
    elif current["session_id"] == report["session_id"]:
        maximum = max(item["accepted_sequence"] for item in report["receipts"])
        durable = current["recorder"]["persistence"]["durable_sequence"]
        result = {"check": "durable_write", "passed": durable >= maximum,
                  "durable_sequence": durable, "required_sequence": maximum,
                  "recorder_state": current["recorder"]["state"]}
    else:
        codes = []
        for call in (lambda: api.append_event(report["producer"], {}, expected_session_id=report["session_id"]),
                     lambda: api.read_event_changes(report["checkpoint"], expected_session_id=current["session_id"])):
            try:
                call()
                codes.append("unexpected_success")
            except api.APIError as exc:
                codes.append(exc.code)
        result = {"check": "old_session_rejected", "passed": codes == ["session_changed", "session_changed"],
                  "codes": codes, "current_session": current["session_id"]}
    report.setdefault("verification", {})[result["check"]] = dict(result, checked_at=utc_now())
    save(path, report)
    return dict(result, report=str(path))
