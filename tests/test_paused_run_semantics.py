"""Regressions from the paused September 21 run, with anonymized identities."""

import copy
import unittest

from support import ROOT
from context_overlay.experience.experience_labels import resolve_label
from context_overlay.experience.experience_policy import classify, compact
from experience_view import build_experiences
from test_experience_view import action, audit, units
from test_filter_events import event, micro_fixture, time
from test_experience_recap import make
from experience_recap import markdown
from context_overlay.experience.experience_quality import quality_report


def bath_pair():
    bath = action("bath", ("13084", "bathtub_TakeBath"), 21248181, 21320728)
    bath["facts"].update(visible=True, is_super=True, outcome_result="SUCCESS")
    posture = action("posture", ("13427", "generic_Bath"), 21248181, 21320728)
    posture["facts"].update(visible=False, is_super=True, trigger={"name": "POSTURE_GRAPH"})
    posture["tier"] = "internal"
    for e, queued in ((bath, 21227728), (posture, 21244848)):
        e["first_observed_time"] = time(queued)
        e["observations"] = [{"phase": "queued", "game_time": time(queued)}]
    return bath, posture


class PausedRunSemanticTests(unittest.TestCase):
    def test_bath_posture_is_support_and_preserves_root_and_consequences(self):
        bath, posture = bath_pair()
        effect = event("effect", "payment.completed", {"actual_amount": -3})
        effect["cause"] = {"event_id": posture["event_id"]}
        before = copy.deepcopy([bath, posture, effect])
        result = build_experiences([bath, posture, effect], "run")
        root, = units(result, "activities")
        self.assertEqual(root["action_tuning"], "bathtub_TakeBath")
        self.assertEqual(root["queued"], bath["first_observed_time"])
        self.assertEqual(root["started"], bath["started_time"])
        self.assertEqual(root["ended"], bath["ended_time"])
        self.assertEqual(root["result_branch"], "SUCCESS")
        self.assertEqual(root["continuation_step_count"], 1)
        self.assertEqual(audit(result, "bath")["units"], audit(result, "posture")["units"])
        self.assertEqual(units(result, "facts")[0]["activity"], root["id"])
        self.assertEqual(result["audit"]["links"][0]["basis"], "reviewed_posture_provider_same_execution")
        self.assertEqual([bath, posture, effect], before)

    def test_posture_missing_ambiguous_or_incomplete_root_remains_detail(self):
        bath, posture = bath_pair()
        duplicate = copy.deepcopy(bath)
        duplicate["event_id"] = "run:second_bath"
        duplicate["facts"]["interaction_id"] = "second_bath"
        scenarios = [[], [bath, duplicate]]
        for field, value in (("started_time", time(21248180)), ("ended_time", time(21320729)),
                             ("ended_time", None), ("zone_visit", 2)):
            candidate = copy.deepcopy(bath)
            candidate[field] = value
            scenarios.append([candidate])
        for field, value in (("actor", {"key": "sim:other"}), ("target", {"key": "object:other"}), ("visible", False)):
            candidate = copy.deepcopy(bath)
            candidate["facts"][field] = value
            scenarios.append([candidate])
        for roots in scenarios:
            with self.subTest(roots=roots):
                result = build_experiences(roots + [posture], "run")
                detail = result["details"]
                self.assertTrue(any(u["action_tuning"] == "generic_Bath" for u in detail))
                self.assertFalse(any(l["merged"] for l in result["audit"]["links"]))

    def test_support_requires_runtime_shape_and_respects_conflicting_parent(self):
        bath, posture = bath_pair()
        for changes in ({"visible": True}, {"is_super": False}, {"trigger": None}, {"trigger": {"name": "AUTONOMY"}},
                        {"parent_event_id": "run:another_activity"}):
            with self.subTest(changes=changes):
                child = copy.deepcopy(posture)
                child["facts"].update(changes)
                result = build_experiences([bath, child], "run")
                self.assertEqual(len(units(result, "activities")), 1)
                self.assertEqual(len(result["details"]), 1)
                self.assertNotEqual(audit(result, "bath")["units"], audit(result, "posture")["units"])
        for child_time in (None, time(21320728)):
            child = copy.deepcopy(posture)
            child["started_time"] = child_time
            result = build_experiences([bath, child], "run")
            self.assertEqual(len(result["details"]), 1)

    def test_unknown_support_identity_or_version_is_never_folded(self):
        bath, posture = bath_pair()
        posture["facts"]["tuning_name"] += "_Modded"
        result = build_experiences([bath, posture], "run")
        self.assertEqual(len(result["review"]), 1)
        self.assertFalse(result["audit"]["links"])
        bath, posture = bath_pair()
        result = build_experiences([bath, posture], "run", game_version="future-version")
        self.assertEqual(len(result["review"]), 2)
        self.assertFalse(result["audit"]["links"])

    def test_overnight_nap_and_game_preserve_open_and_interrupted_execution(self):
        nap = action("nap", ("13136", "chair_Nap"), 21790326, 21949982)
        game = action("game", ("13230", "computer_PlayGame_Blicblock"), 21994376, 22219734)
        game["facts"]["finishing_type"] = "INTERACTION_INCOMPATIBILITY"
        ongoing = action("ongoing", ("13136", "chair_Nap"), 22228561)
        ongoing.update(ended_time=None, outcome="unknown", stage="started")
        ongoing["facts"].update(finishing_type=None, outcome_result=None)
        result = build_experiences([nap, game, ongoing], "run")
        self.assertEqual(len(units(result, "activities")), 3)
        self.assertFalse(result["review"])
        by_tuning = units(result, "activities")
        self.assertEqual(by_tuning[1]["exit"], "INTERACTION_INCOMPATIBILITY")
        self.assertIsNone(by_tuning[2]["ended"])
        self.assertEqual(by_tuning[2]["outcome"], "unknown")

    def test_new_activity_variants_and_linked_mixer_remain_distinct(self):
        for root_resource, child_resource in (
                (("13230", "computer_PlayGame_Blicblock"), ("31664", "Computer_Use_PlayGame_Mild")),
                (("9111", "tv_WatchNews"), ("30304", "mixer_TV_WatchNewsAndCooking")),
                (("128720", "movie_Watch_DiamondsAreForSims"), ("128871", "watch-movie"))):
            root, child, decision = micro_fixture()
            root["facts"].update(tuning_id=root_resource[0], tuning_name=root_resource[1])
            child["facts"].update(tuning_id=child_resource[0], tuning_name=child_resource[1])
            decision["payload"]["selected"]["action"] = {"id": child_resource[0], "tuning_name": child_resource[1]}
            decision["payload"]["stages"][0]["candidates"][0]["action"] = {"id": root_resource[0], "tuning_name": root_resource[1]}
            result = build_experiences([root, child, decision], "run")
            self.assertEqual(len(units(result, "activities")), 1)
            self.assertEqual(audit(result, "provider")["units"], audit(result, "micro")["units"])
        for resource in (("31741", "computer_PlayGame_IncredibleSports"), ("14240", "sink_washDishes"),
                         ("13835", "Puddle_Mop"), ("27285", "Puddle_Mop_Large"), ("29829", "counter_Clean")):
            item = action("normal", resource)
            self.assertEqual(classify(item), "action")
            item["facts"]["tuning_name"] += "_SimilarName"
            self.assertEqual(classify(item), "unknown")

    def test_cooking_fire_chance_is_a_step_not_fire_or_completed_cooking(self):
        phase = action("phase", ("97734", "Cooking_Shared_Passive_FireChance"))
        phase["facts"]["name"] = {"text": "Cooking_Shared_Passive_FireChance", "status": "no_display_name"}
        result = build_experiences([phase], "run")
        self.assertFalse(units(result, "activities"))
        detail, = result["details"]
        self.assertEqual(detail["category"], "activity_phase")
        self.assertNotIn("importance", detail)
        label = resolve_label(compact(phase["facts"]), "interaction")
        self.assertEqual(label["text"], "烹饪过程的内部步骤")
        self.assertEqual(label["observed"], "Cooking_Shared_Passive_FireChance")
        self.assertEqual(len(label["source"]["xml_sha256"]), 64)

    def test_object_name_failures_keep_status_and_never_leak_tokens_into_recap(self):
        for name in ({"text": "〈未解析：0.ObjectName〉", "status": "unresolved_tokens", "unresolved": [{}]},
                     {"text": "unmapped_object", "status": "unmapped"}):
            item = action("shovel", ("180061", "snowDrift_Shovel"))
            item["facts"]["target"] = {"kind": "object", "key": "object:2", "name": name}
            bundle = make([item])
            row, = bundle["recap"]["activities"]
            self.assertEqual(row["target"], ["物件名称未解析"])
            self.assertNotIn("〈未解析：", markdown(bundle))
            issue = next(r for r in bundle["audit"]["labels"] if r["identity"][0] == "object")
            self.assertEqual(issue["observed"], name["text"])
            self.assertEqual(issue["source_status"], name["status"])
            self.assertEqual(quality_report(bundle)["totals"]["name_issue_units"], 1)
        item["facts"]["target"]["name"] = {"text": "自定义雪堆", "status": "resolved"}
        self.assertEqual(make([item])["recap"]["activities"][0]["target"], ["自定义雪堆"])


if __name__ == "__main__":
    unittest.main()
