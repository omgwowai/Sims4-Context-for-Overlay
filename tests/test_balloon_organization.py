"""Verify reviewed mixer links and distinguish missing sources from missing activities."""

import copy
import unittest

from support import ROOT
from context_overlay.experience.experience_recap import markdown, resolve
from context_overlay.experience.experience_view import build_experiences
from context_overlay.experience.experience_quality import quality_report
from test_balloons import balloon
from test_experience_recap import make
from test_experience_view import action
from test_filter_events import micro_fixture


def linked_fixture(root=("14489", "tv-watch-sports"), child=("14545", "watch-sports")):
    events = micro_fixture()
    provider, mixer, decision = events
    for item, resource in ((provider, root), (mixer, child)):
        item["facts"].update(tuning_id=resource[0], tuning_name=resource[1])
    decision["payload"]["selected"]["action"] = {"id": child[0], "tuning_name": child[1]}
    candidate = decision["payload"]["stages"][0]["candidates"][0]
    candidate["action"] = {"id": root[0], "tuning_name": root[1]}
    events.append(balloon(cause={"event_id": mixer["event_id"], "actor": {"key": "sim:1"}}))
    return events


class BalloonOrganizationTests(unittest.TestCase):
    def test_reviewed_mixer_families_attach_via_exact_provider_instance(self):
        pairs = [(("14489", "tv-watch-sports"), ("9125", "watch-sports2")),
                 (("14489", "tv-watch-sports"), ("9138", "watch-sports3")),
                 (("227249", "stereo_listen"), ("14319", "stereo_listenPassive")),
                 (("14315", "stereo_Dance"), ("14316", "stereo_danceActive")),
                 (("14315", "[Join]stereo_Dance"), ("14316", "stereo_danceActive")),
                 (("40074", "computer_Browse_Art"), ("13189", "Computer_Browse_Mouse")),
                 (("35953", "art_Trait_Snob_SnobbilyAssess"), ("100361", "painting_View_Critique"))]
        for root, child in pairs:
            with self.subTest(root=root, child=child):
                events = linked_fixture(root, child)
                original = copy.deepcopy(events)
                bundle = make(events)
                row, = bundle["recap"]["balloons"]
                self.assertEqual(row["association_state"], "activity_linked")
                activity, = bundle["recap"]["activities"]
                self.assertEqual(row["activity"], activity["ref"])
                self.assertEqual(row["source_event_id"], "run:micro")
                evidence = resolve(bundle, bundle["snapshot_id"], row["ref"], "evidence")["items"]
                self.assertEqual([e["event_id"] for e in evidence], ["run:balloon"])
                self.assertTrue(all(quality_report(bundle)["checks"].values()))
                self.assertEqual(events, original)

    def test_matching_time_and_object_do_not_replace_verified_provider_evidence(self):
        for case in ("missing_link", "actor", "target", "visit", "end", "family", "identity", "game_version"):
            with self.subTest(case=case):
                events = linked_fixture()
                provider, mixer, decision, signal = events
                version = None
                if case == "missing_link": mixer["facts"].pop("decision_event_id")
                if case == "actor": provider["facts"]["actor"]["key"] = "sim:9"
                if case == "target": provider["facts"]["target"]["key"] = "object:9"
                if case == "visit": provider["zone_visit"] = 2
                if case == "end": provider["ended_time"] = {"ticks": "250"}
                if case == "family":
                    provider["facts"].update(tuning_id="227249", tuning_name="stereo_listen")
                    decision["payload"]["stages"][0]["candidates"][0]["action"] = {"id": "227249", "tuning_name": "stereo_listen"}
                if case == "identity": mixer["facts"]["tuning_name"] += "_Modified"
                if case == "game_version": version = "different_game_build"
                view = build_experiences(events, "run", "sim:1", version)
                row, = [u for u in view["organized"]["facts"] if u["type"] == "balloon.sent"]
                self.assertNotIn("activity", row)
                self.assertEqual(row["association_state"], "source_recorded_activity_unresolved")

    def test_known_unclassified_source_is_displayed_without_inventing_activity(self):
        source = action("unknown", ("999999", "custom_unknown_action"))
        source["facts"]["name"] = "未分类测试动作"
        signal = balloon(cause={"event_id": source["event_id"], "actor": {"key": "sim:1"}})
        bundle = make([source, signal])
        row, = bundle["recap"]["balloons"]
        self.assertNotIn("activity", row)
        self.assertEqual(row["association_state"], "source_recorded_activity_unresolved")
        self.assertEqual(row["source_action"], "未分类测试动作")
        self.assertEqual(row["source_event_id"], source["event_id"])
        self.assertIn("来源动作：未分类测试动作", markdown(bundle))
        self.assertIn("来源已记录，所属活动未关联", markdown(bundle))
        view = build_experiences([source, signal], "run", "sim:1")
        facts = view["consumer_packet"]["standalone_facts"]
        self.assertEqual(facts[0]["source_interaction"]["event_id"], source["event_id"])

    def test_missing_mismatched_and_unrecorded_sources_remain_distinct(self):
        for case in ("missing", "actor", "visit", "no_cause"):
            with self.subTest(case=case):
                source = action("source", ("13950", "shower_TakeShower"))
                signal = balloon(cause={"event_id": source["event_id"], "actor": {"key": "sim:1"}})
                events = [source, signal]
                if case == "missing": events.pop(0)
                if case == "actor": signal["cause"]["actor"]["key"] = "sim:9"
                if case == "visit": signal["zone_visit"] = 2
                if case == "no_cause": signal["cause"] = None
                row, = make(events)["recap"]["balloons"]
                self.assertNotIn("activity", row)
                self.assertNotIn("source_action", row)
                self.assertEqual(row["association_state"], "source_unrecorded" if case == "no_cause" else "source_unavailable")

    def test_reviewed_direct_actions_keep_observed_outcome_and_each_balloon(self):
        for resource in (("13950", "shower_TakeShower"), ("99408", "phone_PlayGames_AutonomousOnly")):
            with self.subTest(resource=resource):
                source = action("source", resource)
                source.update(outcome="cancelled")
                source["facts"]["finishing_type"] = "USER_CANCEL"
                events = [source] + [balloon(str(i), cause={"event_id": source["event_id"]}) for i in range(2)]
                bundle = make(events)
                self.assertEqual(len(bundle["recap"]["balloons"]), 2)
                self.assertTrue(all(row["association_state"] == "activity_linked" for row in bundle["recap"]["balloons"]))
                unit, = [u for u in bundle["units"].values() if u.get("category") == "action"]
                self.assertEqual(unit["outcome"], "cancelled")


if __name__ == "__main__":
    unittest.main()
