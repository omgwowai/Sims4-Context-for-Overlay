"""Read-only, offline audit of the supplied Atlas and Experience reference data.

Uses only the standard library; never imports or executes reference-repo code.
Counts describe the available files, not everything that happened in the game.
"""

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


DATASETS = ("win0910", "win0903", "aotrace0903")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def input_digest(root, paths):
    """Hash sorted UTF-8 records: relative POSIX path + NUL + SHA-256 + LF."""
    records = sorted((p.relative_to(root).as_posix(), sha256(p)) for p in paths)
    digest = hashlib.sha256()
    for relative, checksum in records:
        digest.update((relative + "\0" + checksum + "\n").encode("utf-8"))
    return {"files": len(records), "sha256": digest.hexdigest()}


def read_json(path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8-sig") as handle:
        return json.load(handle)


def audit_atlas(root):
    manifest_path = root / "dist/dashboard/manifest.json"
    export_path = root / "export-manifest.json"
    manifest = read_json(manifest_path)
    export = read_json(export_path)
    inputs = [manifest_path, export_path]
    all_ids = Counter()
    groups = {}
    for name in ("registered", "attributes", "locals"):
        path = root / "dist/dashboard" / (name + ".json.gz")
        inputs.append(path)
        rows = read_json(path)
        identities = Counter(row[0] for row in rows)
        all_ids.update(identities)
        groups[name] = {
            "rows": len(rows),
            "unique_ids": len(identities),
            "duplicate_id_extra_rows": sum(n - 1 for n in identities.values()),
            "declared_rows": manifest["groups"][name],
        }
    relation_path = root / "inputs/ledger/relations.jsonl.gz"
    inputs.append(relation_path)
    relation_statuses = Counter()
    with gzip.open(relation_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                relation_statuses[json.loads(line).get("status", "missing")] += 1
    return {
        "export_site_version": export["site_version"],
        "export_source_commit": export["source_commit"],
        "source_revisions": manifest["source"],
        "input_digest": input_digest(root, inputs),
        "groups": groups,
        "total_rows": sum(all_ids.values()),
        "unique_ids_across_groups": len(all_ids),
        "duplicate_id_extra_rows_across_groups": sum(n - 1 for n in all_ids.values()),
        "ledger_relation_statuses": dict(sorted(relation_statuses.items())),
        "parse_failures_declared": manifest["parseFailures"],
        "limits": [
            "Identity uniqueness is not runtime alias resolution.",
            "Relation statuses are source labels, not new runtime verification.",
            "Parse failures are declared by the source manifest; AST was not rebuilt.",
        ],
    }


def audit_events(root):
    files = sorted((root / "events").rglob("*.jsonl"))
    if not files:
        raise ValueError("No event JSONL files under " + str(root / "events"))
    events = []
    for path in files:
        with path.open(encoding="utf-8-sig") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("event is not an object")
                    for key in ("event_id", "kind", "sim_id", "verb"):
                        if key not in event:
                            raise ValueError("missing " + key)
                except ValueError as exc:
                    raise ValueError(f"{path}:{number}: {exc}") from exc
                events.append(event)

    identities = Counter(e["event_id"] for e in events)
    # Include every Sim file and every supplied slot in this dataset before
    # resolving references. Never treat a time-window slice as the full index.
    parents = [e for e in events if e.get("parent_id")]
    containers = [e for e in events if e.get("container_id")]
    dangling_parents = [e for e in parents if e["parent_id"] not in identities]
    dangling_containers = [e for e in containers if e["container_id"] not in identities]
    received_keys = Counter(
        (e["parent_id"], e["sim_id"], json.dumps(e.get("by"), sort_keys=True),
         e["verb"], e.get("status"))
        for e in events if e["kind"] == "received" and e.get("parent_id")
    )
    did = [e for e in events if e["kind"] == "did"]
    health_path = root / "health/session.json"
    health = read_json(health_path) if health_path.exists() else None
    hooks = health.get("hooks", []) if health else []
    inputs = files + ([health_path] if health else [])
    return {
        "input_digest": input_digest(root, inputs),
        "event_files": len(files),
        "events": len(events),
        "unique_event_ids": len(identities),
        "duplicate_id_extra_rows": sum(n - 1 for n in identities.values()),
        "kinds": dict(sorted(Counter(e["kind"] for e in events).items())),
        "parent_link_rows": len(parents),
        "dangling_parent_rows": len(dangling_parents),
        "dangling_parent_verbs": dict(sorted(Counter(e["verb"] for e in dangling_parents).items())),
        "container_link_rows": len(containers),
        "dangling_container_rows": len(dangling_containers),
        "received_same_parent_duplicate_candidate_groups": sum(n > 1 for n in received_keys.values()),
        "received_same_parent_duplicate_candidate_extra_rows": sum(n - 1 for n in received_keys.values()),
        "did_rows": len(did),
        "did_co_present_field_present": sum("co_present" in e for e in did),
        "did_co_present_nonempty": sum(bool(e.get("co_present")) for e in did),
        "events_with_context_ref": sum(bool(e.get("context_ref")) for e in events),
        "health": {
            "present": health is not None,
            "declared_mod_version": (health.get("launch") or {}).get("mod_version") if health else None,
            "reported_dropped_total": sum(h.get("dropped", 0) for h in hooks),
            "reported_errors_total": sum(h.get("errors", 0) for h in hooks),
            "reported_dedup_hits_total": sum(h.get("dedup_hits", 0) for h in hooks),
            "hooks": [
                {key: h.get(key) for key in
                 ("hook", "injected", "emitted", "dropped", "errors", "dedup_hits", "last_error")}
                for h in hooks
            ],
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources-root", type=Path,
                        default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path,
                        help="Write a UTF-8 JSON report; default is stdout.")
    args = parser.parse_args()
    report = {
        "schema": "sims4-context.reference-audit/1",
        "method": "Read-only static data audit; no game execution or reference-code imports.",
        "atlas": audit_atlas(args.sources_root / "Sims4-Context-Atlas"),
        "experience_datasets": {
            name: audit_events(args.sources_root / "Sims4-Experience-Mod" / name)
            for name in DATASETS
        },
        "limits": [
            "Missing references are missing in the supplied dataset; the cause is undetermined.",
            "Distinct event IDs do not establish absence of semantically duplicated events.",
            "Health counters are reported values and may describe a different time boundary.",
            "Dropped counters can include policy filters and buffer losses; do not infer a loss rate.",
            "Historical datasets do not validate the current collector revision.",
        ],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print("Wrote " + str(args.output))
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
