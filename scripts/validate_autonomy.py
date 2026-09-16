"""Reproduce offline Autonomy tests, native symbol audit and a labelled sample.

Run with the game's CPython 3.7. Never starts/imports the game runtime. Only pure
native function bodies execute, under the controlled test harness.
"""

import argparse
import io
import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from context_overlay import VERSION
from context_overlay.semanticizer import explain_event
import test_autonomy as probes


SYMBOLS = [
    ("simulation.zip", "autonomy/autonomy_service.pyc", "AutonomyService._select_best_result"),
    ("simulation.zip", "autonomy/autonomy_service.pyc", "AutonomyService.choose_best_interaction"),
    ("simulation.zip", "autonomy/autonomy_component.pyc", "AutonomyComponent.get_multitasking_roll"),
    ("simulation.zip", "autonomy/autonomy_component.pyc", "AutonomyComponent._should_run_cached_interaction"),
    ("simulation.zip", "objects/components/__init__.pyc", "ComponentMetaclass.__new__.build_exported_func.exported_func"),
    ("simulation.zip", "objects/components/__init__.pyc", "ComponentMetaclass.__new__.build_exported_func"),
    ("simulation.zip", "objects/components/__init__.pyc", "ComponentMetaclass.__new__.apply_component_methods"),
    ("simulation.zip", "interactions/interaction_queue.pyc", "InteractionQueue.append"),
    ("simulation.zip", "elements.pyc", "GeneratorElementBase._run"),
    ("simulation.zip", "interactions/aop.pyc", "AffordanceObjectPair.execute_interaction"),
    ("simulation.zip", "interactions/base/interaction.pyc", "Interaction.invalidate"),
    ("simulation.zip", "autonomy/autonomy_modes.pyc", "_MixerAutonomy._run_gen"),
    ("simulation.zip", "autonomy/autonomy_modes.pyc", "_MixerAutonomy._create_and_score_mixers"),
    ("simulation.zip", "autonomy/autonomy_mixer_provider_scoring.pyc", "_MixerProviderScoring.get_mixer_provider"),
    ("core.zip", "sims4/random.pyc", "weighted_random_index"),
    ("core.zip", "sims4/random.pyc", "weighted_random_item"),
    ("core.zip", "sims4/gsi/archive.pyc", "BaseArchiver.archive_enable_fn"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "docs/validation/2026-09-16-autonomy-implementation.json")
    args = parser.parse_args()
    if not probes.NATIVE_AVAILABLE:
        raise SystemExit("Requires CPython 3.7 and the installed simulation.zip; native checks cannot be skipped")
    report = {"module_version": VERSION, "scope": "offline_native_function_harness_not_live_gameplay", "symbols": []}
    for archive, member, suffix in SYMBOLS:
        code = probes.native_code(member, suffix, archive)
        report["symbols"].append({"member": member, "function": suffix,
            "parameters": list(code.co_varnames[:code.co_argcount + code.co_kwonlyargcount]),
            "generator": bool(code.co_flags & 32)})
    output = io.StringIO()
    started = time.monotonic()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(probes))
    report["tests"] = {"run": result.testsRun, "failed": len(result.failures), "errors": len(result.errors),
                       "skipped": len(result.skipped), "seconds": time.monotonic() - started}
    report["checks"] = output.getvalue().splitlines()
    if not result.wasSuccessful() or result.skipped:
        print(output.getvalue())
        raise SystemExit(1)
    fixture = probes.NativeChecks("test_actual_pool_top_five_and_forced_lowest")
    fixture.setUp()
    try:
        fixture.test_actual_pool_top_five_and_forced_lowest()
        event = fixture.decisions()[0]
        report["controlled_example"] = {"synthetic_inputs": True, "event": event, "rendered": explain_event(event)}
        report["diagnostics_example"] = fixture.capture.status()
        # Exercise bounded cache saturation with exact identities, without disk I/O.
        from context_overlay.autonomy import PendingDecisions
        cache = PendingDecisions(capacity=3, byte_limit=100000)
        instances = [probes.Item() for _ in range(10)]
        for obj in instances:
            cache.put(obj, {"sample": True})
        report["bounded_buffer_probe"] = cache.status()
        assert cache.status()["pending"] == 3 and cache.status()["drops"]["capacity"] == 7
    finally:
        fixture.doCleanups()
    report["native_input_sha256"] = probes.NATIVE_INPUTS
    report["remaining_live_checks"] = ["installed_MOD_loading_and_hook_health", "actual_cache_queue_and_immediate_behavior",
        "GSI_scoring_generation_cost_and_total_frame_time", "single_session_volume_and_buffer_drops", "native_Inspector_layout"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"tests": report["tests"], "symbols": len(SYMBOLS), "report": str(args.output)}))


if __name__ == "__main__":
    main()
