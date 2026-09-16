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
STATUSES = {"resolved", "raw_text", "unmapped", "unresolved_tokens", "rule_resolved", "no_display_name", "empty_display_name", "not_present"}


def reference_fields(value):
    if isinstance(value, list):
        for child in value:
            yield from reference_fields(child)
    elif isinstance(value, dict):
        reference = value.get("reference_semantics", {})
        for role, field in reference.get("fields", {}).items():
            for link in field["alternatives"]:
                yield (reference["resource_key"], role, link["attribute"], link.get("index"), link["hash"]), link["rendered"]
        for key, child in value.items():
            if key not in SKIP and key != "reference_semantics":
                yield from reference_fields(child)


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
    gaps, expressions, references = Counter(), Counter(), {}
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
                if after["status"] not in ("resolved", "raw_text", "rule_resolved"):
                    gaps[after.get("reason", after["status"])] += 1
                for gap in after.get("unresolved", []):
                    expressions[(gap["expression"], gap["reason"])] += 1
                # Report distinct transformations, so identical text belonging
                # to different resources is not silently counted as one result.
                key = (before.get("text"), before.get("hash"), before["status"],
                       after.get("text"), after.get("hash"), after["status"])
                unique[key] = (before["status"], after["status"])
                if before.get("text") != after.get("text") or before["status"] != after["status"]:
                    changes[key] = {"before": before, "after": after,
                                    "example_path": directory.name + "/" + path.name + location}
            references.update(reference_fields(enriched))
    return {"module_version": VERSION, "runs": [path.name for path in directories],
            "method": "Latest event revisions per journal plus every context export; display labels counted once, raw_name evidence excluded. Exports may repeat journal facts. Unique counts use distinct before/after text, hash and status transformations, not entity count or coverage of all game resources.",
            "occurrences_before": dict(counts_before), "occurrences_after": dict(counts_after),
            "unique_before": dict(Counter(value[0] for value in unique.values())),
            "unique_after": dict(Counter(value[1] for value in unique.values())),
            "gap_reasons_after": dict(gaps),
            "unresolved_expressions_after": [{"expression": key[0], "reason": key[1], "count": count}
                                              for key, count in expressions.most_common()],
            "static_reference_fields": {role: dict(Counter(value["status"] for key, value in references.items() if key[1] == role))
                                        for role in ("name", "description", "tooltip")},
            "static_reference_method": "Distinct resource kind/id + role + attribute + index + hash seen in these logs. Templates have no invented historical tokens. These are supplementary references, not newly observed historical descriptions.",
            "sources": sources, "errors": errors, "changes": list(changes.values())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--strings", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--string-sources", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = {args.strings.resolve(), args.catalog.resolve()}
    if args.string_sources:
        inputs.add(args.string_sources.resolve())
    inputs.update(path.resolve() for directory in args.runs for path in directory.glob("*.json*"))
    if args.output.resolve() in inputs:
        raise SystemExit("Report must not replace input evidence or resources")
    metadata = json.loads(args.string_sources.read_text(encoding="utf-8")) if args.string_sources else None
    if metadata and metadata.get("strings_sha256") != hashlib.sha256(args.strings.read_bytes()).hexdigest():
        raise SystemExit("String source metadata does not match dictionary")
    catalog = NameCatalog(json.loads(args.catalog.read_text(encoding="utf-8")),
                          Localizer(json.loads(args.strings.read_text(encoding="utf-8")), metadata))
    result = audit(args.runs, catalog)
    result["catalog_sha256"] = hashlib.sha256(args.catalog.read_bytes()).hexdigest()
    result["strings_sha256"] = hashlib.sha256(args.strings.read_bytes()).hexdigest()
    result["string_sources_sha256"] = hashlib.sha256(args.string_sources.read_bytes()).hexdigest() if args.string_sources else None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("occurrences_before", "occurrences_after", "unique_before", "unique_after", "errors")}, ensure_ascii=False))
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
