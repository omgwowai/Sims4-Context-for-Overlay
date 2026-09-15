"""Compare recorded labels with resource-based enrichment, without game access."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_overlay import VERSION
from context_overlay.localization import Localizer
from context_overlay.name_catalog import NameCatalog
from context_overlay.storage import replay


SKIP = {"rendered", "semantic_view", "localization", "raw_name"}
STATUSES = {"resolved", "raw_text", "unmapped", "unresolved_tokens", "rule_resolved", "no_display_name"}


def label_pairs(before, after, path=""):
    if isinstance(before, dict):
        if "text" in before and before.get("status") in STATUSES:
            yield before, after, path
            return  # A rule's raw_name is evidence, not a second displayed label.
        for key, value in before.items():
            if key not in SKIP:
                yield from label_pairs(value, after[key], path + "." + key)
    elif isinstance(before, list):
        for old, new in zip(before, after):
            yield from label_pairs(old, new, path + "[]")


def audit(directories, catalog):
    counts_before, counts_after = Counter(), Counter()
    unique, changes, sources, errors = {}, {}, [], []
    for directory in directories:
        journal = directory / "journal.jsonl"
        loaded = replay(journal, include_observations=False)
        errors.extend({"run": directory.name, **item} for item in loaded["errors"])
        packets = [(journal, {"history": {"events": loaded["events"]}})]
        for path in sorted(directory.glob("context-*.json")):
            packets.append((path, json.loads(path.read_text(encoding="utf-8-sig"))))
        for path, packet in packets:
            sources.append({"run": directory.name, "file": path.name,
                            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            enriched = catalog.enrich(packet)
            for before, after, location in label_pairs(packet, enriched):
                counts_before[before["status"]] += 1
                counts_after[after["status"]] += 1
                # Report distinct transformations, so identical text belonging
                # to different resources is not silently counted as one result.
                key = (before.get("text"), before.get("hash"), before["status"],
                       after.get("text"), after.get("hash"), after["status"])
                unique[key] = (before["status"], after["status"])
                if before.get("text") != after.get("text") or before["status"] != after["status"]:
                    changes[key] = {"before": before, "after": after,
                                    "example_path": directory.name + "/" + path.name + location}
    return {"module_version": VERSION, "runs": [path.name for path in directories],
            "method": "Latest event revisions per journal plus every context export; display labels counted once, raw_name evidence excluded. Exports may repeat journal facts. Unique counts use distinct before/after text, hash and status transformations, not entity count or coverage of all game resources.",
            "occurrences_before": dict(counts_before), "occurrences_after": dict(counts_after),
            "unique_before": dict(Counter(value[0] for value in unique.values())),
            "unique_after": dict(Counter(value[1] for value in unique.values())),
            "sources": sources, "errors": errors, "changes": list(changes.values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--strings", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {args.strings.resolve(), args.catalog.resolve()}
    inputs.update(path.resolve() for directory in args.runs for path in directory.glob("*.json*"))
    if args.output.resolve() in inputs:
        raise SystemExit("Report must not replace input evidence or resources")
    catalog = NameCatalog(json.loads(args.catalog.read_text(encoding="utf-8")),
                          Localizer(json.loads(args.strings.read_text(encoding="utf-8"))))
    result = audit(args.runs, catalog)
    result["catalog_sha256"] = hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    result["strings_sha256"] = hashlib.sha256(args.strings.read_bytes()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("occurrences_before", "occurrences_after", "unique_before", "unique_after", "errors")}, ensure_ascii=False))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
