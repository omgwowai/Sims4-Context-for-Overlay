"""Important observations, evidence-only phase folding and conservative accounting."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from support import ROOT, PYTHON
from context_overlay.experience.experience_policy import RESOURCES
from context_overlay.experience.experience_quality import quality_report, compare_bundles
from experience_recap import markdown, review_groups, snapshot_digest
from test_experience_recap import make, reference
from test_experience_view import action, buff
from test_filter_events import event, micro_fixture, time


def phases(parent=("13117", "book_read"), child=("13121", "Book_Read_Passive")):
    root, step, decision = micro_fixture()
    for item, resource in ((root, parent), (step, child)):
        item["facts"].update(tuning_id=resource[0], tuning_name=resource[1], name=resource[1],
            roles=[{"entity_key": "sim:1", "role": "actor"}, {"entity_key": "object:2", "role": "target"}])
    decision["payload"]["selected"]["action"] = {"id": child[0], "tuning_name": child[1]}
    decision["payload"]["stages"][0]["candidates"][0]["action"] = {"id": parent[0], "tuning_name": parent[1]}
    return [root, step, decision]


class SemanticQualityTests(unittest.TestCase):
    def test_fire_actions_visible_without_inventing_outcomes(self):
        values = [("40870", "fire_OnFire"), ("40111", "fire_Extinguish"),
                  ("39466", "fire_Panic"), ("75247", "sim_Fire_RouteToSafety"), ("75076", "fire_reacting_to_fire")]
        events = [action(str(i), resource) for i, resource in enumerate(values)]
        for e in events:
            e["facts"]["name"] = e["facts"]["tuning_name"]
            e["facts"]["outcome_result"] = "SUCCESS"
        events[1]["facts"]["finishing_type"] = "USER_CANCEL"
        events[3]["started_time"] = None
        bundle = make(events)
        rows = bundle["recap"]["activities"]
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row["importance"] == "critical" for row in rows))
        self.assertIn("主动取消", next(r for r in rows if r["action"] == "灭火")["execution"])
        self.assertIn("未见开始", next(r for r in rows if r["action"] == "因火灾前往安全处")["execution"])
        self.assertNotIn("成功", markdown(bundle))
        report = quality_report(bundle)
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(len(report["critical_events"]), 5)

    def test_phase_families_fold_only_recorded_instances_and_keep_effects(self):
        families = [(("13117", "book_read"), ("13121", "Book_Read_Passive")),
                    (("40870", "fire_OnFire"), ("77464", "Fire_OnFire_PassiveMixer")),
                    (("128729", "movie_Watch_SuperHeroes"), ("128871", "watch-movie")),
                    (("13084", "bathtub_TakeBath"), ("13085", "bathtub_TakeBath_Loop")),
                    (("13155", "Chess_Social"), ("13152", "chess_practice"))]
        for parent, child in families:
            with self.subTest(parent=parent):
                events = phases(parent, child)
                effect = event("effect", "skill.level", {"new_level": 2})
                effect["cause"] = {"event_id": "run:micro"}
                events.append(effect)
                bundle = make(events)
                self.assertEqual(reference(bundle, "micro"), reference(bundle, "provider"))
                root = bundle["units"][bundle["routes"][reference(bundle, "provider")][0]]
                self.assertEqual(root["ended"], events[0]["ended_time"])
                self.assertEqual(bundle["recap"]["results"][0]["activity"], reference(bundle, "provider"))
                self.assertTrue(all(quality_report(bundle)["checks"].values()))

    def test_bad_provider_or_unstarted_phase_never_folds(self):
        for case in ("missing", "actor", "visit", "target", "child_target", "family", "identity", "instance", "selection_time", "child_end", "unstarted", "ambiguous"):
            with self.subTest(case=case):
                root, child, decision = phases()
                candidate = decision["payload"]["stages"][0]["candidates"][0]
                if case == "actor": decision["payload"]["actor"]["key"] = "sim:9"
                if case == "visit": decision["zone_visit"] = 2
                if case == "target": candidate["target"]["key"] = "object:9"
                if case == "child_target": child["facts"]["target"]["key"] = "object:9"
                if case == "family":
                    child["facts"].update(tuning_id="13085", tuning_name="bathtub_TakeBath_Loop")
                    decision["payload"]["selected"]["action"] = {"id": "13085", "tuning_name": "bathtub_TakeBath_Loop"}
                if case == "identity": candidate["action"]["tuning_name"] += "_Override"
                if case == "instance": candidate["interaction_id"] = "different"
                if case == "selection_time": decision["payload"]["selection_time"] = time(99999)
                if case == "child_end": child["ended_time"] = time(99999)
                if case == "unstarted": child["started_time"] = None
                if case == "ambiguous": decision["payload"]["stages"].append(copy.deepcopy(decision["payload"]["stages"][0]))
                bundle = make([root, child] if case == "missing" else [root, child, decision])
                self.assertNotEqual(reference(bundle, "micro"), reference(bundle, "provider"))
                row = bundle["units"][bundle["routes"][reference(bundle, "micro")][0]]
                self.assertIn("association_missing", row["review_reasons"])

    def test_exact_identity_and_version_gates_still_apply(self):
        from experience_recap import build_recap
        from test_experience_recap import loaded
        e = action("fire", ("40870", "fire_OnFire"))
        self.assertFalse(build_recap(loaded([e]), "sim:1", "future.version")["recap"]["activities"])
        e["facts"]["tuning_name"] += "_Modded"
        self.assertFalse(make([e])["recap"]["activities"])
        e = action("watch", ("13506", "[Join]guitar_Watch"))
        self.assertFalse(make([e])["recap"]["activities"])

    def test_review_reasons_and_execution_are_independent(self):
        unknown = action("unknown", ("9999", "unknown_Action"))
        unknown["facts"]["name"] = "显示名已解析"
        unnamed = action("unnamed", ("9998", "unmapped_Action"))
        unnamed["facts"]["name"] = "unmapped_Action"
        phase = phases()[1]
        protected = action("protected", ("31664", "Computer_Use_PlayGame_Mild"))
        effect = event("effect", "payment.completed", {"actual_amount": -3})
        effect["cause"] = {"event_id": protected["event_id"]}
        bundle = make([unknown, unnamed, phase, protected, effect])
        def row(key):
            return next(r for r in bundle["recap"]["review_actions"] if r["ref"] == reference(bundle, key))
        self.assertEqual(row("unknown")["review_reasons"], ["classification_missing"])
        self.assertIn("已执行", row("unknown")["execution"])
        self.assertIn("name_unresolved", row("unnamed")["review_reasons"])
        self.assertEqual(bundle["units"][bundle["routes"][reference(bundle, "micro")][0]]["review_reasons"], ["association_missing"])
        protected_unit = bundle["ledger"][bundle["routes"][reference(bundle, "protected")][0]]
        self.assertIn("protected_detail", protected_unit["review_reasons"])
        self.assertEqual(protected_unit["placement"], "detail")
        self.assertIn("association_missing", bundle["ledger"][bundle["routes"][reference(bundle, "effect")][0]]["review_reasons"])
        self.assertEqual(len(bundle["recap"]["results"]), 1)

    def test_review_grouping_retains_all_refs_actor_visit_and_execution_boundaries(self):
        one, two, other = [action(key, ("9999", "unknown_Action")) for key in ("one", "two", "other")]
        two["started_time"] = None
        other["zone_visit"] = 2
        bundle = make([one, two, other])
        groups = review_groups(bundle["recap"])
        self.assertEqual(sorted(map(len, groups)), [1, 2])
        self.assertEqual({r["ref"] for g in groups for r in g}, {reference(bundle, k) for k in ("one", "two", "other")})
        text = markdown(bundle)
        self.assertIn("未见开始", text)
        self.assertTrue(all("[{}]".format(r["ref"]) in text for g in groups for r in g))

    def test_accounting_and_comparison_ignore_ref_renumbering(self):
        bundle = make([action("cook"), buff("add", 150), buff("remove", 250, False)])
        report = quality_report(bundle)
        self.assertEqual(report["totals"]["selected_events"], 3)
        self.assertEqual(sum(report["totals"]["event_destinations"].values()), 3)
        moved = copy.deepcopy(bundle)
        renamed = {}
        for uid, row in moved["ledger"].items():
            old = row["ref"]
            row["ref"] = "renumbered:" + old
            renamed[old] = row["ref"]
            moved["routes"][row["ref"]] = moved["routes"].pop(old)
        def replace(value):
            if isinstance(value, dict): return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list): return [replace(item) for item in value]
            return renamed.get(value, value) if isinstance(value, str) else value
        moved["recap"] = replace(moved["recap"])
        moved["snapshot_id"] = moved["recap"]["snapshot_id"] = snapshot_digest(moved)
        self.assertEqual(compare_bundles(bundle, moved)["changed_events"], 0)
        self.assertEqual(compare_bundles(bundle, moved)["changed_sections"], [])
        e = action("book", ("13117", "book_read"))
        with patch.dict(RESOURCES, {("interaction", "13117", "book_read"): "unknown"}):
            before = make([e])
        after = make([e])
        difference = compare_bundles(before, after)
        self.assertEqual(difference["changed_events"], 1)
        self.assertEqual(difference["changes"][0]["before"]["destination"], "review")
        self.assertEqual(difference["changes"][0]["after"]["destination"], "recap")
        edited = copy.deepcopy(after)
        uid = next(iter(edited["units"]))
        edited["units"][uid]["ended"] = time(800)
        edited["snapshot_id"] = edited["recap"]["snapshot_id"] = snapshot_digest(edited)
        self.assertEqual(compare_bundles(after, edited)["changed_events"], 1)
        rendered = copy.deepcopy(after)
        rendered["recap"]["activities"][0]["execution"] = "New rendering of exit reason"
        rendered["snapshot_id"] = rendered["recap"]["snapshot_id"] = snapshot_digest(rendered)
        self.assertEqual(compare_bundles(after, rendered)["changed_sections"], ["activities"])

    def test_modified_bundle_and_different_sources_are_rejected(self):
        original = make([action("one")])
        modified = copy.deepcopy(original)
        modified["recap"]["activities"][0]["action"] = "invented"
        with self.assertRaises(ValueError): quality_report(modified)
        with self.assertRaises(ValueError): compare_bundles(original, make([action("two")]))
        missing = copy.deepcopy(original)
        missing["audit"]["evidence"] = {}
        missing["snapshot_id"] = missing["recap"]["snapshot_id"] = snapshot_digest(missing)
        with self.assertRaises(ValueError): quality_report(missing)

    def test_json_key_order_does_not_change_recap_or_quality(self):
        from experience_recap import build_recap
        from test_experience_recap import loaded
        events = [event("sentiment", "relationship.sentiment", {
            "z": {"id": "20", "tuning_name": "Unmapped_First", "name": "Unmapped_First", "resource_kind": "statistic"},
            "a": {"id": "21", "tuning_name": "Unmapped_Second", "name": "Unmapped_Second", "resource_kind": "statistic"}})]
        source = loaded(events)
        reordered = dict(source, events=json.loads(json.dumps(events, sort_keys=True)))
        before, after = [build_recap(value, "sim:1") for value in (source, reordered)]
        self.assertEqual(before, after)
        self.assertEqual(quality_report(before), quality_report(after))
        self.assertEqual(compare_bundles(before, after)["changed_events"], 0)

    def test_cli_reports_and_comparison_preserve_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source, output = folder / "bundle.json", folder / "quality.json"
            source.write_text(json.dumps(make([action("one")])), encoding="utf-8")
            command = PYTHON + [str(ROOT / "scripts/experience_recap.py")]
            for args in (["quality", str(source), "--output", str(output), "--markdown", str(folder / "quality.md")],
                         ["compare", str(source), str(output)]):
                result = subprocess.run(command + args, capture_output=True)
                if args[0] == "quality": self.assertEqual(result.returncode, 0, result.stderr)
                else: self.assertNotEqual(result.returncode, 0)
            after = folder / "after.json"
            after.write_bytes(source.read_bytes())
            result = subprocess.run(command + ["compare", str(source), str(after), "--output", str(output)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())["changed_events"], 0)
            result = subprocess.run(command + ["quality", str(source), "--output", str(source)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
