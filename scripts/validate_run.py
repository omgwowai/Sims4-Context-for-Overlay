"""Audit actual runtime output; observed coverage is not a full acceptance claim."""

import argparse
from collections import Counter
from pathlib import Path

from offline import read_journal, read_packet
from tool_support import report as emit_report


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
    args = parser.parse_args()
    inputs = list(args.run.glob("*.json*"))
    result = audit(args.run, args.event_scope)
    summary = {key: result[key] for key in ("session_id", "event_scope", "event_count", "observation_count", "integrity_passed", "errors")}
    emit_report(result, dict(summary, exports=len(result["exports"])), args.output, inputs)
    if not result["integrity_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
