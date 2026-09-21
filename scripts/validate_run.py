"""Audit actual runtime output; observed coverage is not a full acceptance claim."""

import argparse
import json
from collections import Counter
from pathlib import Path

from offline import read_journal, read_packet
from tool_support import report as emit_report


def capture_status(directory, loaded):
    ends = [o for o in loaded["observations"] if o.get("category") == "session_end"]
    result = {"state": "open_or_unclosed", "capture_complete": None, "session_end_present": bool(ends),
              "last_sequence": loaded["last_sequence"], "note": "A valid journal prefix does not prove complete session capture."}
    path = directory / "run-status.json"
    if path.exists():
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            persistence = report["recorder"]["persistence"]
            matches = report["session_id"] == loaded["session_id"]
            complete = (matches and loaded["complete"] and report["capture_complete"] is True and
                        report["phase"] == "closed" and bool(ends) and ends[-1]["sequence"] == loaded["last_sequence"] and
                        report.get("session_end_sequence") == loaded["last_sequence"] and
                        report["recorder"].get("state") == "recording" and not report["recorder"].get("error") and
                        not report.get("cleanup_errors") and report.get("journal_drained") is True and
                        persistence.get("pending_bytes") == 0 and not persistence.get("error") and
                        not persistence.get("io_failed") and
                        persistence["accepted_sequence"] == persistence["durable_sequence"] == loaded["last_sequence"] and
                        persistence.get("durable_byte_offset") == (directory / "journal.jsonl").stat().st_size)
            result.update(state="complete" if complete else "incomplete" if report["phase"] in ("closed", "failed") else "open_or_unclosed",
                          capture_complete=bool(complete) if report["phase"] in ("closed", "failed") else None,
                          recording_error=report["recorder"].get("error"), report_matches_session=matches,
                          accepted_sequence=persistence["accepted_sequence"], durable_sequence=persistence["durable_sequence"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.update(state="invalid_status", capture_complete=False, error=str(exc))
    elif ends:
        result["state"] = "end_boundary_without_final_status"
    return result


def audit(directory, event_scope="all"):
    result = read_journal(directory / "journal.jsonl", event_scope)
    events = result["events"]
    interactions = [item for item in events if item["event_type"] == "interaction"]
    changes = [item for item in events if item["event_type"] == "state_change"]
    errors = list(result["errors"])
    for event in events:
        if not event["event_id"].startswith(result["session_id"] + ":"):
            errors.append({"event_id": event["event_id"], "error": "Event outside declared session"})
        if event["event_type"] == "interaction":
            facts = event["facts"]
            if not isinstance(facts["actor"]["id"], str):
                errors.append({"event_id": event["event_id"], "error": "Numeric entity ID"})
            if event["outcome"] == "completed" and (event["stage"] != "ended" or facts["finishing_type"] != "NATURAL"):
                errors.append({"event_id": event["event_id"], "error": "Unsubstantiated completion"})
    exports = []
    for path in sorted(directory.glob("context-*.json")):
        try:
            packet, _ = read_packet(path)
        except (ValueError, OSError) as exc:
            errors.append({"export": path.name, "error": str(exc)})
            continue
        refs = {event["event_id"] for event in packet.get("history", {}).get("events", [])}
        for line in packet.get("rendered", {}).get("history", []):
            if line["event_id"] not in refs:
                errors.append({"export": path.name, "error": "Unresolved semantic evidence reference"})
        exports.append({"file": path.name, "status": packet.get("status"), "target": packet.get("target"),
                        "history_events": len(refs), "fields": list(packet.get("snapshot", {}))})
    return {"session_id": result["session_id"], "event_scope": event_scope, "source_sha256": result["sha256"], "integrity_passed": not errors, "errors": errors,
            "capture": capture_status(directory, result),
            "event_count": len(events), "observation_count": len(result["observations"]),
            "interaction_tiers": dict(Counter(item["tier"] for item in interactions)),
            "interaction_outcomes": dict(Counter(item["outcome"] for item in interactions)),
            "trigger_sources": dict(Counter(item["facts"]["trigger"]["name"] for item in interactions)),
            "changed_fields": dict(Counter(item["field"] for item in changes)), "exports": exports,
            "validation_markers": [item for item in result["observations"] if item.get("category") == "validation_marker"],
            "note": "Integrity and observed coverage only; game scenarios and independent state checks still require recorded acceptance evidence."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("run", type=Path)
    parser.add_argument("--output", type=Path, help="Save the full report; default prints a summary")
    parser.add_argument("--event-scope", choices=("all", "retained"), default="all")
    parser.add_argument("--require-closed", action="store_true", help="Fail unless a complete closed run is verified against its final status")
    args = parser.parse_args()
    inputs = list(args.run.glob("*.json*"))
    result = audit(args.run, args.event_scope)
    summary = {key: result[key] for key in ("session_id", "event_scope", "event_count", "observation_count", "integrity_passed", "capture", "errors")}
    emit_report(result, dict(summary, exports=len(result["exports"])), args.output, inputs)
    if not result["integrity_passed"] or (args.require_closed and result["capture"]["capture_complete"] is not True):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
