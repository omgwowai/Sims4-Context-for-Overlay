"""Measure current event retention, FIFO churn and entity cleanup without EA.

Uses captured interaction shapes plus synthetic new-adapter payloads. No game
imports or profile writes. JSON byte counts are projections, not fsync timings.
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from benchmark_history import process_memory
from context_overlay import VERSION
from context_overlay.model import copy_data, entity
from context_overlay.recorder import Recorder
from context_overlay.storage import replay


class CountingJournal:
    def __init__(self):
        self.sequence = self.bytes = 0

    def append(self, record):
        self.sequence += 1
        self.bytes += len(json.dumps(dict(record, sequence=self.sequence), ensure_ascii=False).encode("utf-8")) + 1
        return self.sequence

    def status(self):
        return {"accepted_sequence": self.sequence, "durable_sequence": 0, "error": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("journal", type=Path)
    parser.add_argument("--capacity", type=int, default=200000)
    parser.add_argument("--extra", type=int, default=10000)
    parser.add_argument("--shape", choices=("compact", "rich"), default="compact")
    parser.add_argument("--memory-mb", type=int, default=1536)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.capacity <= 200000 or not 1 <= args.extra <= 20000:
        raise SystemExit("Bounded benchmark: capacity 1..200000, extra 1..20000")
    if not 1 <= args.memory_mb <= 3072:
        raise SystemExit("Benchmark memory budget is bounded to 1..3072 MiB")
    recovered = replay(args.journal, include_observations=False)
    seeds = [e for e in recovered["events"] if e["event_type"] == "interaction"]
    if recovered["errors"] or not seeds:
        raise SystemExit("Use an intact journal with interactions")
    journal = CountingJournal()
    recorder = Recorder(journal, "fifo-capacity", capacity=args.capacity, snapshot_ttl=600,
                        memory_bytes=args.memory_mb * 1024 * 1024)
    baseline = process_memory()
    began, checkpoints, counts = time.perf_counter(), [], {}
    first_id, snapshot = None, None
    total = args.capacity + args.extra
    for i in range(total):
        # Thirty percent captured actions; the remainder use current adapters'
        # discrete payloads, resource names, causal evidence and role shapes.
        event = copy_data(seeds[i % len(seeds)])
        subject = event["facts"]["actor"]
        target = entity("object", 100000000 + i, "FIFO fixture " + str(i))
        resource = {"id": "16658", "tuning_name": "fixture_statistic", "resource_kind": "statistic",
                    "name": {"text": "fixture label", "status": "mapped", "source": {"kind": "runtime_tuning"}}}
        if i % 10 < (3 if args.shape == "rich" else 1):
            event.update(event_id="fifo-capacity:interaction:" + str(i), revision=1,
                         first_observed_time=i, last_observed_time=i, entities=[subject["key"], target["key"]],
                         participants=[subject, target], roles=[{"entity_key": subject["key"], "role": "actor", "basis": "interaction.sim"}])
            event["facts"].update(target=target, participants=[subject, target], outcome_result="SUCCESS",
                                  classification="gameplay_or_unclassified")
            event.pop("accepted_sequence", None)
            saved = recorder._save(event)
        else:
            category = ("statistic.direct", "skill.level", "relationship.knowledge", "inventory.transfer")[i % 4]
            cause = {"event_id": "fifo-capacity:interaction:" + str(i - 1), "actor": subject,
                     "basis": "resolver.interaction", "operation": resource, "operation_id": str(i)}
            payload = {"before": 10, "after": 20, "resource": resource, "scope_evidence": "local_at_call"}
            if category == "relationship.knowledge":
                payload.update(before={"_known_traits": []}, after={"_known_traits": [resource]}, changed_fields=["_known_traits"])
            if category == "inventory.transfer":
                payload.update(before={"container": None, "stack_count": 3, "hidden": False},
                               after={"container": subject, "stack_count": 3, "hidden": True}, operation="inventory_insert")
            saved = recorder.fact(category, [subject, target], payload, i, "offline_shape_fixture",
                cause=cause if args.shape == "rich" or i % 10 > 7 else None,
                roles=[{"entity_key": target["key"], "role": "target", "basis": "operation"}],
                event_id="fifo-capacity:fact:" + str(i), evidence="observed_transition")
        if saved is None:
            break
        counts[saved["event_type"]] = counts.get(saved["event_type"], 0) + 1
        if i == 0:
            first_id = saved["event_id"]
            snapshot = recorder.query_history(target["key"], page_size=1, include_internal=True)
        if (i + 1) % 25000 == 0 or i + 1 in (args.capacity, total):
            row = {"accepted_events": i + 1, "retained": len(recorder.events), "evicted": recorder.evicted,
                   "charged_bytes": recorder.index.memory_bytes, "elapsed_seconds": time.perf_counter() - began,
                   "process": process_memory()}
            checkpoints.append(row)
            print(json.dumps(row), flush=True)
    state = recorder.status()
    assertions = {}
    if not recorder.paused:
        assertions["bounded_count"] = len(recorder.events) == args.capacity
        assertions["expected_fifo_evictions"] = recorder.evicted == args.extra
        assertions["oldest_gone"] = first_id not in recorder.events
        assertions["fixed_snapshot_survives"] = recorder.history_page(snapshot["cursor"])["events"][0]["event_id"] == first_id
        assertions["stale_identities_removed"] = all("object:" + str(100000000 + i) not in recorder.references for i in range(args.extra))
        assertions["exact_reference_count"] = recorder.index.reference_count == sum(len(e["entities"]) for e in recorder.events.values())
        assertions["indexes_match_live_events"] = all(event_id in recorder.events for idx in recorder.index.entities.values() for event_id in idx.by_id)
        recorder.close_query(snapshot["cursor"])
        assertions["snapshot_released"] = recorder.index.status()["snapshot_references"] == 0
    final_memory = process_memory()
    report = {"module_version": VERSION, "python": sys.version, "kind": "offline_fifo_capacity", "shape": args.shape,
              "source_journal_sha256": hashlib.sha256(args.journal.read_bytes()).hexdigest(),
              "captured_interaction_shapes": len(seeds), "requested_capacity": args.capacity, "requested_extra": args.extra,
              "generated_types": counts, "seconds": time.perf_counter() - began,
              "baseline_process_memory": baseline, "final_process_memory": final_memory,
              "checkpoints": checkpoints, "serialized_journal_bytes": journal.bytes,
              "assertions": assertions, "recorder": state,
              "limitations": ["Synthetic load, not a game run. Compact: 10% captured actions, 20% causal facts; rich: 30% actions, 70% causal facts.",
                              "Unique object references stress entity churn. Not a prediction of all gameplay event sizes.",
                              "Counting sink measures serialized bytes; it does not write/fsync a real 200000-event journal.",
                              "Snapshots, game, other mods and source-hook caches add memory beyond the event index budget."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output), "state": state["state"], "error": state["error"], "assertions": assertions}))
    if recorder.paused or not all(assertions.values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
