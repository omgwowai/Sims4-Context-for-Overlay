"""Audit actual runtime output; observed coverage is not a full acceptance claim."""

import argparse
from collections import Counter
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_overlay.storage import replay


def audit(directory):
    result = replay(directory / "journal.jsonl")
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
        packet = json.loads(path.read_text(encoding="utf-8"))
        refs = {event["event_id"] for event in packet.get("history", {}).get("events", [])}
        for line in packet.get("rendered", {}).get("history", []):
            if line["event_id"] not in refs:
                errors.append({"export": path.name, "error": "Unresolved semantic evidence reference"})
        exports.append({"file": path.name, "status": packet.get("status"), "target": packet.get("target"),
                        "history_events": len(refs), "fields": list(packet.get("snapshot", {}))})
    return {"session_id": result["session_id"], "integrity_passed": not errors, "errors": errors,
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
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(args.run)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    if not result["integrity_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
