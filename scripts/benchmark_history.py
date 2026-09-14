"""Expand recorded event shapes for an offline index/capacity measurement.

No game imports or game-profile writes. Does not benchmark fsync throughput.
"""

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_overlay import VERSION
from context_overlay.history import HistoryIndex, HistoryError, deep_size, ticks
from context_overlay.model import copy_data
from context_overlay.storage import replay


def process_memory():
    if os.name != "nt":
        return None
    size = ctypes.c_size_t
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, size) for name in ("peak_working_set", "working_set", "peak_paged_pool", "paged_pool",
                                     "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile", "private_bytes")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return {key: getattr(counters, key) for key in ("working_set", "peak_working_set", "private_bytes")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("journal", type=Path)
    parser.add_argument("--count", type=int, default=200000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.count < 1 or args.count > 200000:
        raise SystemExit("This benchmark is bounded to 1..200000 events")
    replayed = replay(args.journal, include_observations=False)
    if replayed["errors"] or not replayed["events"]:
        raise SystemExit("Use an intact journal containing events")
    originals = replayed["events"]
    originals.sort(key=lambda e: ticks(e.get("first_observed_time", e["last_observed_time"])))
    lower = min(ticks(e.get("first_observed_time", e["last_observed_time"])) for e in originals)
    upper = max(ticks(e["last_observed_time"]) for e in originals)
    cycle_span = upper - lower + 1
    index = HistoryIndex("offline-benchmark", snapshot_ttl=300)
    baseline = process_memory()
    began, checkpoints, failure = time.perf_counter(), [], None
    for i in range(args.count):
        source = originals[i % len(originals)]
        event = dict(source)
        event["event_id"] = "offline-benchmark:event:" + str(i)
        event["first_observed_time"] = {"ticks": str(ticks(source.get("first_observed_time", source["last_observed_time"])) +
                                                   (i // len(originals)) * cycle_span)}
        # Other captured fields/strings keep their real shapes. Only identity
        # and chronological index anchors are synthesized for this load test.
        try:
            prepared, charge = index.prepare(event)
            index.publish(prepared, i + 1, charge)
        except HistoryError as exc:
            failure = str(exc)
            break
        if (i + 1) % 25000 == 0:
            checkpoint = {"events": i + 1, "elapsed_seconds": time.perf_counter() - began,
                          "charged_bytes": index.memory_bytes, "process": process_memory()}
            checkpoints.append(checkpoint)
            print(json.dumps(checkpoint), flush=True)
    elapsed = time.perf_counter() - began
    event_memory = process_memory()
    actor = max((key for key in index.entities if key.startswith("sim:")), key=lambda key: len(index.entities[key].by_id))

    def legacy():
        matching = [e for e in index.events.values() if actor in e["entities"] and e["tier"] == "main"]
        return copy_data(list(reversed(matching))[:50])

    def indexed():
        return copy_data(index.recent(actor, 50, False)[0])

    assert [e["event_id"] for e in legacy()] == [e["event_id"] for e in indexed()]
    timings = {}
    for name, operation in (("global_scan_ms", legacy), ("entity_index_ms", indexed)):
        values = []
        for _ in range(7):
            started = time.perf_counter()
            operation()
            values.append((time.perf_counter() - started) * 1000)
        timings[name] = {"median": statistics.median(values), "max": max(values)}
    last_ticks = index.entities[actor].by_time[-1][0]
    metadata = {"status": "offline_benchmark", "coverage": {"persistence": {
        "accepted_sequence": len(index.events), "durable_sequence": 0}}, "target": {"key": actor}}
    started = time.perf_counter()
    page = index.query(actor, metadata, page_size=50, from_ticks=last_ticks - cycle_span, to_ticks=last_ticks + 1)
    query_ms = (time.perf_counter() - started) * 1000
    paged, cursor, page_latencies = len(page["events"]), page["next_cursor"], []
    while cursor:
        started = time.perf_counter()
        subsequent = index.next_page(cursor)
        page_latencies.append((time.perf_counter() - started) * 1000)
        paged += len(subsequent["events"])
        cursor = subsequent["next_cursor"]
    assert paged == page["total_matches"]
    index.release(page["cursor"])
    counts = {kind: sum(e["event_type"] == kind for e in originals) for kind in ("interaction", "state_change")}
    report = {"kind": "offline_history_capacity_benchmark", "module_version": VERSION,
              "python": sys.version, "source_journal": str(args.journal),
              "source_sha256": hashlib.sha256(args.journal.read_bytes()).hexdigest(),
              "source_event_count": len(originals), "source_event_types": counts,
              "requested_events": args.count, "loaded_events": len(index.events), "failure": failure,
              "build_seconds": elapsed, "baseline_process_memory": baseline,
              "event_process_memory": event_memory, "final_process_memory": process_memory(),
              "index": index.status(), "checkpoints": checkpoints, "recent_query": timings,
              "filtered_pagination": {"first_page_ms": query_ms, "total_matches": paged,
                                      "candidates_examined": page["candidates_examined"],
                                      "next_page_median_ms": statistics.median(page_latencies) if page_latencies else None},
              "source_latest_deep_bytes": deep_size(originals),
              "estimated_journal_bytes_at_requested_count": round(args.journal.stat().st_size / len(originals) * args.count),
              "disk_free_bytes_at_output": shutil.disk_usage(str(args.output.parent.resolve())).free,
              "notes": ["Synthetic expansion of real event shapes, not a new in-game run.",
                        "Includes current versions and indexes; does not measure the game's own memory or fsync throughput.",
                        "Disk projection uses the old real run's revision/observation mix; byte guards apply to actual new output.",
                        "Only first-observed index times and event IDs are expanded; other event contents remain sample shapes."]}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "loaded": len(index.events), "failure": failure,
                      "index_bytes": index.memory_bytes, "process": event_memory, "query": timings}), flush=True)
    if failure or len(index.events) != args.count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
