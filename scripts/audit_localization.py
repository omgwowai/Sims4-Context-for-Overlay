"""Compare recorded labels with resource-based enrichment, without game access."""

import argparse
from collections import Counter
from pathlib import Path

from offline import load_catalog, read_packet
from tool_support import report as emit_report
from context_overlay import VERSION
from context_overlay.localization import gap_category


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


def audit(directories, catalog, event_scope="all"):
    counts_before, counts_after = Counter(), Counter()
    unique, changes, sources, errors = {}, {}, [], []
    gaps, expressions, references, categories = Counter(), Counter(), {}, Counter()
    for directory in directories:
        journal = directory / "journal.jsonl"
        for path in [journal] + sorted(directory.glob("context-*.json")):
            try:
                packet, digest = read_packet(path, event_scope)
            except (ValueError, OSError) as exc:
                errors.append({"run": directory.name, "file": path.name, "error": str(exc)})
                continue
            sources.append({"run": directory.name, "file": path.name, "sha256": digest})
            enriched = catalog.enrich(packet)
            for before, after, location in label_pairs(packet, enriched):
                counts_before[before["status"]] += 1
                counts_after[after["status"]] += 1
                if after["status"] not in ("resolved", "raw_text", "rule_resolved"):
                    gaps[after.get("reason", after["status"])] += 1
                for gap in after.get("unresolved", []):
                    expressions[(gap["expression"], gap["reason"])] += 1
                    categories[gap.get("category", gap_category(gap["reason"]))] += 1
                # Report distinct transformations, so identical text belonging
                # to different resources is not silently counted as one result.
                key = (before.get("text"), before.get("hash"), before["status"],
                       after.get("text"), after.get("hash"), after["status"])
                unique[key] = (before["status"], after["status"])
                if before.get("text") != after.get("text") or before["status"] != after["status"]:
                    changes[key] = {"before": before, "after": after,
                                    "example_path": directory.name + "/" + path.name + location}
            references.update(reference_fields(enriched))
    return {"module_version": VERSION, "event_scope": event_scope, "runs": [path.name for path in directories],
            "method": "Latest event revisions in the declared event_scope plus every context export; display labels counted once, raw_name evidence excluded. Exports may repeat journal facts. Unique counts use distinct before/after text, hash and status transformations, not entity count or coverage of all game resources.",
            "occurrences_before": dict(counts_before), "occurrences_after": dict(counts_after),
            "unique_before": dict(Counter(value[0] for value in unique.values())),
            "unique_after": dict(Counter(value[1] for value in unique.values())),
            "gap_reasons_after": dict(gaps),
            "unresolved_expressions_after": [{"expression": key[0], "reason": key[1], "count": count}
                                              for key, count in expressions.most_common()],
            "unresolved_expression_categories": dict(categories),
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
    parser.add_argument("--output", type=Path, help="Save the full report; default prints a summary")
    parser.add_argument("--event-scope", choices=("all", "retained"), default="all")
    args = parser.parse_args()
    inputs = [args.strings, args.catalog, args.string_sources]
    inputs.extend(path for directory in args.runs for path in directory.glob("*.json*"))
    catalog, hashes = load_catalog(args.strings, args.catalog, args.string_sources)
    result = audit(args.runs, catalog, args.event_scope)
    result.update(hashes)
    summary = {key: result[key] for key in ("event_scope", "occurrences_before", "occurrences_after", "unique_before", "unique_after", "errors")}
    emit_report(result, summary, args.output, inputs)
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
