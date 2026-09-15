"""Check event hook symbols against installed game bytecode without running EA.

Run with CPython 3.7. This verifies symbol/signature availability only, not game
behavior or pack ownership. The report can be shared without EA bytecode.
"""

import argparse
import hashlib
import importlib.util
import json
import marshal
import sys
from pathlib import Path
from types import CodeType
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from context_overlay.event_sources import HOOKS, KNOWLEDGE_METHODS


def children(code):
    return [child for child in code.co_consts if isinstance(child, CodeType)]


def find(code, name):
    return next((child for child in children(code) if child.co_name == name), None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, default=Path("D:/Games/The Sims 4"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 7):
        raise SystemExit("Use the game's CPython 3.7 bytecode reader")
    archive_path = args.game / "Data/Simulation/Gameplay/simulation.zip"
    rows = []
    inherited = {("FamilyFunds", "add"): ("sims.funds", "_Funds"),
                 ("FamilyFunds", "try_remove_amount"): ("sims.funds", "_Funds"),
                 ("SentimentTrackTracker", "remove_statistic"): ("statistics.base_statistic_tracker", "BaseStatisticTracker")}
    entries = list(HOOKS) + [("relationships.sim_knowledge", "SimKnowledge", method, "knowledge") for method in KNOWLEDGE_METHODS]
    entries += [("interactions.utils.loot_basic_op", "BaseLootOperation", "_apply_to_subject_and_target", "operation"),
                ("interactions.payment.payment_cost", "_Payment", "on_payment", "payment_context"),
                ("broadcasters.broadcaster_effect", "_BroadcasterEffect", "apply_broadcaster_effect", "broadcast_test_gate")]
    with ZipFile(str(archive_path)) as archive:
        cache = {}
        def module_code(module):
            if module not in cache:
                data = archive.read(module.replace(".", "/") + ".pyc")
                if data[:4] != importlib.util.MAGIC_NUMBER:
                    raise ValueError("Bytecode magic differs from interpreter")
                cache[module] = marshal.loads(data[16:])
            return cache[module]
        for module, cls, method, kind in entries:
            row = {"hook": module + "." + cls + "." + method, "kind": kind}
            try:
                class_code = find(module_code(module), cls)
                if class_code is None:
                    raise ValueError("Class not found")
                method_code = find(class_code, method)
                if method_code is None and (cls, method) in inherited:
                    parent_module, parent = inherited[(cls, method)]
                    method_code = find(find(module_code(parent_module), parent), method)
                    row["inherited_from"] = parent_module + "." + parent
                if method_code is None:
                    raise ValueError("Method not found in declared class/base")
                row.update(state="verified_symbol", parameters=list(method_code.co_varnames[:method_code.co_argcount + method_code.co_kwonlyargcount]),
                           generator=bool(method_code.co_flags & 0x20))
            except (ValueError, KeyError, AttributeError) as exc:
                row.update(state="unresolved", reason=str(exc))
            rows.append(row)
    report = {"python": sys.version, "game_archive": str(archive_path),
              "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
              "symbols": rows, "unresolved": sum(row["state"] != "verified_symbol" for row in rows),
              "limitation": "Static bytecode only; EA code was not imported or executed; no game validation."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"symbols": len(rows), "unresolved": report["unresolved"], "report": str(args.output)}))
    if report["unresolved"]:
        for row in rows:
            if row["state"] == "unresolved":
                print(row["hook"] + ": " + row["reason"])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
