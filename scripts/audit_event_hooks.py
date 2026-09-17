"""Check event hook symbols against installed game bytecode without running EA.

Run with CPython 3.7. This verifies symbol/signature availability only, not game
behavior or pack ownership. The report can be shared without EA bytecode.
"""

import argparse
import sys
from pathlib import Path

from tool_support import GAME, GameBytecode, report as emit_report, sha256
from context_overlay.event_sources import HOOKS, KNOWLEDGE_METHODS


AUTONOMY_SYMBOLS = [
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
    parser.add_argument("--game", type=Path, default=GAME)
    parser.add_argument("--output", type=Path, help="Save the full report; default prints a summary")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 7):
        raise SystemExit("Use the game's CPython 3.7 bytecode reader")
    reader = GameBytecode(args.game)
    rows = []
    inherited = {("FamilyFunds", "add"): ("sims.funds", "_Funds"),
                 ("FamilyFunds", "try_remove_amount"): ("sims.funds", "_Funds"),
                 ("SentimentTrackTracker", "remove_statistic"): ("statistics.base_statistic_tracker", "BaseStatisticTracker")}
    entries = list(HOOKS) + [("relationships.sim_knowledge", "SimKnowledge", method, "knowledge") for method in KNOWLEDGE_METHODS]
    entries += [("interactions.utils.loot_basic_op", "BaseLootOperation", "_apply_to_subject_and_target", "operation"),
                ("interactions.payment.payment_cost", "_Payment", "on_payment", "payment_context"),
                ("broadcasters.broadcaster_effect", "_BroadcasterEffect", "apply_broadcaster_effect", "broadcast_test_gate")]
    symbols = [("simulation.zip", module.replace(".", "/") + ".pyc", cls + "." + method, kind)
               for module, cls, method, kind in entries]
    symbols += [(*symbol, "autonomy") for symbol in AUTONOMY_SYMBOLS]
    for archive, member, suffix, kind in symbols:
        row = {"archive": archive, "member": member, "function": suffix, "kind": kind}
        try:
            try:
                method_code = reader.symbol(member, suffix, archive)
            except LookupError:
                owner = tuple(suffix.rsplit(".", 1))
                if owner not in inherited:
                    raise
                parent_module, parent = inherited[owner]
                method_code = reader.symbol(parent_module.replace(".", "/") + ".pyc", parent + "." + owner[1], archive)
                row["inherited_from"] = parent_module + "." + parent
            row.update(state="verified_symbol", parameters=list(method_code.co_varnames[:method_code.co_argcount + method_code.co_kwonlyargcount]),
                       generator=bool(method_code.co_flags & 0x20))
        except (ValueError, LookupError, AttributeError) as exc:
            row.update(state="unresolved", reason=str(exc))
        rows.append(row)
    archives = [reader.directory / name for name in sorted({symbol[0] for symbol in symbols})]
    report = {"python": sys.version, "game_directory": str(args.game),
              "archive_sha256": {path.name: sha256(path) for path in archives}, "member_sha256": reader.hashes,
              "symbols": rows, "unresolved": sum(row["state"] != "verified_symbol" for row in rows),
              "limitation": "Static bytecode only; EA code was not imported or executed; no game validation."}
    emit_report(report, {"symbols": len(rows), "unresolved": report["unresolved"]}, args.output, archives)
    if report["unresolved"]:
        for row in rows:
            if row["state"] == "unresolved":
                print(row["member"] + ":" + row["function"] + ": " + row["reason"])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
