"""Offline compatibility entry point for the shared experience core."""

import argparse
import hashlib
import json
from pathlib import Path

from tool_support import ROOT, report, write_text
from tool_support import report as emit_report
from offline import read_journal
from context_overlay.experience.filter_events import *


def analyze(path, entity_key=None):
    if path.suffix.lower() != ".jsonl":
        raise ValueError("This experiment accepts a complete journal.jsonl or stable copy only")
    loaded = read_journal(path, "all", include_observations=False)
    if not loaded["complete"]:
        raise ValueError("Invalid journal: " + str(loaded["errors"]))
    result = filter_events(loaded["events"], loaded["session_id"], entity_key)
    result["source"] = {"path": str(path.resolve()), "sha256": loaded["sha256"],
                        "event_scope": "all_recorded_events_latest_revision", "metadata": loaded["metadata"]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--entity", help="Linked entity key, e.g. sim:123; omitted means all events")
    parser.add_argument("--output", type=Path, help="Save retained events and omission audit; default prints metrics only")
    args = parser.parse_args()
    result = analyze(args.input, args.entity)
    emit_report(result, dict(result["filtering"]["metrics"], policy_version=POLICY_VERSION,
                            entity_key=args.entity, source_sha256=result["source"]["sha256"]), args.output, [args.input])


if __name__ == "__main__":
    main()
