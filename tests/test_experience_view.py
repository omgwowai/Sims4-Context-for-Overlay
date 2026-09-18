"""Exercise boundaries where compression could change the meaning of an event."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import PYTHON, ROOT
from experience_view import build_experiences
from test_filter_events import event, interaction, micro_fixture, repeated_micro_fixture, time


def action(identifier, resource=("13395", "fridge_CreateTray"), start=100, end=500):
    result = interaction(identifier, resource, start, end)
    result["facts"]["name"] = "制作食物"
    result["facts"]["roles"] = [{"entity_key": "sim:1", "role": "actor"}, {"entity_key": "object:2", "role": "target"}]
    return result


def buff(identifier, at, add=True, subject="sim:1", resource=("10651", "Buff_CoreSocial_Embarrassing")):
    result = event(identifier, "buffs")
    result.update(event_type="state_change", first_observed_time=time(at), entities=[subject],
                  roles=[{"entity_key": subject, "role": "subject"}])
    value = {"id": resource[0], "tuning_name": resource[1], "resource_kind": "buff", "name": "尴尬"}
    result.update(before=None if add else value, after=value if add else None)
    return result


def units(result, lane):
    return result["organized"][lane]


def audit(result, identifier):
    return next(row for row in result["audit"]["evidence"].values() if row["event_id"] == "run:" + identifier)


def mood(identifier, at, old, new, subject="sim:1"):
    result = event(identifier, "mood.changed", {"old_mood": {"id": old, "resource_kind": "mood", "name": old},
        "new_mood": {"id": new, "resource_kind": "mood", "name": new}, "old_intensity": 0, "new_intensity": 0})
    result.update(first_observed_time=time(at), roles=[{"entity_key": subject, "role": "subject"}])
    return result


class ExperienceViewTests(unittest.TestCase):
    def test_reviewed_action_label_preserves_original_localization(self):
        research = action("research", ("381007", "computer_Research_Thanatology"))
        research["facts"]["name"] = "无名之马"
        result = build_experiences([research], "run")
        row, = result["consumer_packet"]["activities"]
        self.assertEqual(row["action"], "研究死亡学")
        self.assertEqual(row["observed_action_name"], "无名之马")
        self.assertEqual(units(result, "activities")[0]["action"]["name"], "无名之马")
        research["facts"]["tuning_name"] += "_Modded"
        result = build_experiences([research], "run")
        self.assertFalse(result["consumer_packet"]["activities"])

    def test_handle_refresh_requires_identical_meaning_and_nonempty_handles(self):
        snapshot = {"buff": {"id": "10651", "tuning_name": "Buff_CoreSocial_Embarrassing"},
                    "handles": ["1", "2"], "mood_weight": 1, "reason": "social"}
        refresh = event("refresh", "buff.refreshed", {"before": snapshot, "after": dict(snapshot, handles=["2"])})
        result = build_experiences([refresh], "run")
        self.assertEqual(audit(result, "refresh")["semantic_role"], "handle_refresh")
        self.assertFalse(units(result, "states"))
        for field, value in (("mood_weight", 2), ("reason", "new cause"), ("handles", [])):
            changed = copy.deepcopy(refresh)
            changed["payload"]["after"][field] = value
            self.assertEqual(len(build_experiences([changed], "run")["review"]), 1)

    def test_utility_goal_and_enrollment_marker_are_not_personal_achievements(self):
        goal = event("goal", "aspiration.goal_completed", {"aspiration_type": "FULL_ASPIRATION",
            "aspiration": {"id": "33188", "tuning_name": "aspiration_Utility_AllChannelsStations"}})
        marker = event("marker", "trait.added", {"trait_type": "HIDDEN", "trait": {
            "id": "230113", "tuning_name": "trait_Hidden_UniversityEnrollment_HasSeenEnrollmentInfo"}})
        fear = event("fear", "trait.added", {"trait_type": "FEAR", "trait": {"id": "new_fear"}})
        result = build_experiences([goal, marker, fear], "run")
        self.assertEqual(len(units(result, "facts")), 1)
        self.assertEqual(audit(result, "goal")["semantic_role"], "goal_internal")
        self.assertEqual(audit(result, "marker")["semantic_role"], "goal_internal")
        goal["payload"]["aspiration"]["tuning_name"] += "_Changed"
        self.assertEqual(len(units(build_experiences([goal], "run"), "facts")), 1)

    def test_chat_mixer_needs_instance_link_and_keeps_its_effect(self):
        provider, child, decision = micro_fixture()
        resources = [("13998", "SocialSuperInteraction"), ("27173", "Idle_Chatting_STC")]
        # Use the exact curated name for the social container.
        from experience_policy import RESOURCES
        resources[0] = next((i, n) for (k, i, n) in RESOURCES if k == "interaction" and i == "13998")
        for item, (identifier, name) in zip((provider, child), resources):
            item["facts"].update(tuning_id=identifier, tuning_name=name)
        decision["payload"]["selected"]["action"] = {"id": resources[1][0], "tuning_name": resources[1][1]}
        decision["payload"]["stages"][0]["candidates"][0]["action"] = {"id": resources[0][0], "tuning_name": resources[0][1]}
        effect = event("effect", "relationship.knowledge", {"after": {"skill": "piano"}})
        effect["cause"] = {"event_id": child["event_id"]}
        result = build_experiences([provider, child, decision, effect], "run")
        self.assertEqual(len(units(result, "activities")), 1)
        self.assertEqual(units(result, "facts")[0]["activity"], units(result, "activities")[0]["id"])
        result = build_experiences([provider, child, effect], "run")
        self.assertEqual(len(units(result, "activities")), 2)

    def test_television_mixer_does_not_merge_different_viewers_or_invent_completion(self):
        provider, child, decision = micro_fixture()
        provider["facts"].update(tuning_id="128726", tuning_name="movie_Watch_RoaringHeights")
        child["facts"].update(tuning_id="14543", tuning_name="watch-passive")
        decision["payload"]["selected"]["action"] = {"id": "14543", "tuning_name": "watch-passive"}
        decision["payload"]["stages"][0]["candidates"][0]["action"] = {"id": "128726", "tuning_name": "movie_Watch_RoaringHeights"}
        provider["facts"]["finishing_type"] = "USER_CANCEL"
        result = build_experiences([provider, child, decision], "run")
        self.assertEqual(audit(result, "micro")["units"], audit(result, "provider")["units"])
        self.assertEqual(units(result, "activities")[0]["exit"], "USER_CANCEL")
        child["facts"]["actor"]["key"] = "sim:2"
        result = build_experiences([provider, child, decision], "run")
        self.assertNotEqual(audit(result, "micro")["units"], audit(result, "provider")["units"])

    def test_native_score_is_kept_when_structured_evidence_is_missing(self):
        from experience_digest import scores
        raw = {"native_text": "native score", "native_text_truncated": False, "missing": ["some_fields"]}
        self.assertEqual(scores(raw)["native_scoring_excerpt"], "native score")
        structured = dict(raw, nonzero_commodity_contributions=[{"commodity": {"name": "Fun"}, "score": 5}])
        result = scores(structured)
        self.assertNotIn("native_scoring_excerpt", result)
        self.assertTrue(result["native_scoring_in_details"])
        self.assertEqual(result["source_missing"], ["some_fields"])

    def test_only_same_tick_mood_excursion_without_consequences_is_folded(self):
        events = [mood("fine", 100, "happy", "fine"), mood("happy", 100, "fine", "happy")]
        result = build_experiences(events, "run")
        state, = units(result, "states")
        self.assertEqual(state["state"]["mood"]["id"], "happy")
        self.assertTrue(state["same_tick_excursion_folded"])
        self.assertTrue(audit(result, "fine")["units"])
        consequence = event("effect", "skill.level", {"before": 1, "after": 2})
        consequence["cause"] = {"event_id": "run:fine"}
        result = build_experiences(events + [consequence], "run")
        self.assertEqual(len(units(result, "states")), 2)
        events[1]["first_observed_time"] = time(101)
        self.assertEqual(len(units(build_experiences(events, "run"), "states")), 2)

    def test_mood_subjects_and_discontinuous_before_values_are_preserved(self):
        events = [mood("a", 100, "fine", "happy"), mood("b", 200, "sad", "fine"),
                  mood("c", 300, "happy", "fine", "sim:2")]
        result = build_experiences(events, "run")
        first, second, third = units(result, "states")
        self.assertIsNone(first["ended"])
        self.assertEqual(first["boundary"], "discontinuous_observations")
        self.assertIsNone(second["ended"])
        self.assertEqual(third["subject"], "sim:2")

    def test_consequence_protects_micro_decision_and_new_numeric_payload(self):
        events = micro_fixture()
        effect = event("effect", "skill.level", {"before": 1, "after": 2})
        effect["cause"] = {"event_id": events[-1]["event_id"]}
        result = build_experiences(events + [effect], "run")
        self.assertTrue(audit(result, "decision")["units"])
        self.assertTrue([u for u in result["review"] if u.get("category") == "submission"])
        value = event("numeric", "statistic.direct", {"statistic": {"id": "16655", "tuning_name": "motive_Fun"},
                                                       "before": 2, "after": 3, "new_fact": "preserve"})
        result = build_experiences([value], "run")
        self.assertEqual(result["review"][0]["payload"]["new_fact"], "preserve")

    def test_cooking_continuations_share_one_activity_but_eating_stays_separate(self):
        cook = action("cook")
        phase = action("phase", ("13277", "counter_MakeFood_Staging_Basic"), 600, 1000)
        phase["facts"].update(parent_event_id=cook["event_id"], target={"key": "object:3"})
        eat = action("eat", ("13433", "generic_consume_food"), 1100, 1500)
        eat["facts"]["parent_event_id"] = phase["event_id"]
        result = build_experiences([cook, phase, eat], "run")
        activities = units(result, "activities")
        self.assertEqual(len(activities), 2)
        self.assertEqual(activities[0]["continuation_step_count"], 2)
        self.assertEqual(activities[0]["ended"], time(1000))
        self.assertEqual(audit(result, "cook")["units"], audit(result, "phase")["units"])
        self.assertNotEqual(audit(result, "cook")["units"], audit(result, "eat")["units"])

    def test_same_name_same_object_and_adjacent_time_do_not_merge_instances(self):
        result = build_experiences([action("first"), action("second", start=500, end=900)], "run")
        self.assertEqual(len(units(result, "activities")), 2)

    def test_phase_links_cannot_cross_actor_visit_time_or_unknown_resource(self):
        for case in ("actor", "visit", "time", "resource", "cycle"):
            with self.subTest(case=case):
                cook = action("cook")
                phase = action("phase", ("13277", "counter_MakeFood_Staging_Basic"), 600, 1000)
                phase["facts"]["parent_event_id"] = cook["event_id"]
                if case == "actor": phase["facts"]["actor"]["key"] = "sim:9"
                if case == "visit": phase["zone_visit"] = 2
                if case == "time": phase["started_time"] = time(50)
                if case == "resource": phase["facts"]["tuning_name"] += "_Changed"
                if case == "cycle": phase["facts"]["parent_event_id"] = phase["event_id"]
                result = build_experiences([cook, phase], "run")
                self.assertEqual(units(result, "activities")[0]["continuation_step_count"], 1)
                self.assertTrue(audit(result, "phase")["units"])

    def test_cancelled_started_activity_and_unstarted_attempt_remain_distinct(self):
        played = action("played", ("31740", "computer_PlayGame_SimsForeverRenamed"))
        played.update(outcome="cancelled")
        played["facts"].update(finishing_type="INTERACTION_INCOMPATIBILITY", outcome_result="SUCCESS")
        attempted = action("attempt", ("33975", "Bed_Undercovers_Cry"), 600, 700)
        attempted["started_time"] = None
        attempted["facts"].update(finishing_type="FAILED_TESTS", outcome_result=None)
        result = build_experiences([played, attempted], "run")
        first, second = units(result, "activities")
        self.assertEqual(first["execution"], "started")
        self.assertEqual(first["outcome"], "cancelled")
        self.assertEqual(second["execution"], "start_not_observed")
        self.assertEqual(second["result_branch"], None)

    def test_buff_interval_can_use_end_outside_entity_index_and_keeps_true_owner(self):
        added = buff("added", 100, subject="sim:2")
        added["entities"].append("sim:1")
        removed = buff("removed", 500, False, subject="sim:2")
        result = build_experiences([added, removed], "run", "sim:1")
        state, = units(result, "states")
        self.assertEqual(state["subject"], "sim:2")
        self.assertEqual(state["boundary"], "paired_observations")
        self.assertEqual(state["ended"], time(500))
        self.assertTrue(audit(result, "removed")["outside_entity_index"])

    def test_buff_boundaries_do_not_cross_visit_owner_or_repeated_add(self):
        for case in ("visit", "subject", "repeat"):
            added, removed = buff("add", 100), buff("remove", 500, False)
            if case == "visit": removed["zone_visit"] = 2
            if case == "subject": removed["roles"][0]["entity_key"] = "sim:2"
            events = [added, removed]
            if case == "repeat": events.insert(1, buff("repeated", 300))
            result = build_experiences(events, "run")
            self.assertEqual(len(units(result, "states")), 2)
            self.assertIsNone(units(result, "states")[0]["ended"])
            if case == "repeat": self.assertEqual(units(result, "states")[0]["boundary"], "ambiguous_repeated_add")

    def test_unknown_end_is_not_filled_using_last_log_time(self):
        sleep = action("sleep", ("13094", "bed_sleep"))
        sleep.update(ended_time=None, stage="running", outcome="unknown")
        result = build_experiences([sleep, buff("state", 200), event("later", "unknown")], "run")
        self.assertIsNone(units(result, "activities")[0]["ended"])
        self.assertIsNone(units(result, "states")[0]["ended"])

    def test_effects_keep_roles_and_only_use_valid_cause(self):
        source = action("social", ("26693", "mixer_social_LieAboutCareer_group_mischief_skills"))
        source["facts"]["outcome_result"] = "FAILURE"
        consequence = event("knowledge", "relationship.knowledge", {"after": {"_known_stats": ["gaming"]}})
        consequence.update(roles=[{"entity_key": "sim:2", "role": "subject"}, {"entity_key": "sim:1", "role": "target"}],
                           cause={"event_id": source["event_id"]})
        result = build_experiences([source, consequence], "run", "sim:1")
        fact, = units(result, "facts")
        self.assertEqual(fact["roles"][0]["entity_key"], "sim:2")
        self.assertEqual(fact["activity"], units(result, "activities")[0]["id"])
        consequence["zone_visit"] = 2
        result = build_experiences([source, consequence], "run")
        self.assertNotIn("activity", units(result, "facts")[0])
        self.assertTrue(units(result, "facts")[0]["unresolved_cause_event_id"])

    def test_dependency_activity_does_not_pull_unrelated_people_effects(self):
        source = action("social", ("26693", "mixer_social_LieAboutCareer_group_mischief_skills"))
        source["entities"] = ["sim:2"]
        source["facts"]["actor"]["key"] = "sim:2"
        selected = event("selected", "skill.level", {"before": 1, "after": 2})
        selected["cause"] = {"event_id": source["event_id"]}
        unrelated = event("unrelated", "skill.level", {"before": 3, "after": 4})
        unrelated.update(entities=["sim:3"], roles=[{"entity_key": "sim:3", "role": "subject"}],
                         cause={"event_id": source["event_id"]})
        result = build_experiences([source, selected, unrelated], "run", "sim:1")
        self.assertEqual(len(units(result, "activities")), 1)
        self.assertEqual(len(units(result, "facts")), 1)
        self.assertNotIn("run:unrelated", [row["event_id"] for row in result["audit"]["evidence"].values()])

    def test_digest_preserves_attempts_direction_and_probability_scope(self):
        attempted = action("attempt")
        attempted["started_time"] = None
        knowledge = event("knowledge", "relationship.knowledge", {"after": {"known_skill": "gaming"}})
        knowledge["roles"] = [{"entity_key": "sim:2", "role": "subject"}, {"entity_key": "sim:1", "role": "target"}]
        result = build_experiences([attempted, knowledge], "run")
        packet = result["consumer_packet"]
        self.assertEqual(packet["activities"][0]["execution"], "start_not_observed")
        self.assertEqual(packet["standalone_facts"][0]["roles"], [["subject", "sim:2"], ["target", "sim:1"]])
        self.assertTrue(packet["scope"]["not_player_knowledge"])
        self.assertIn("not_net", packet["scope"]["numeric_changes"])

    def test_product_identity_connects_cooking_to_eating_without_merging(self):
        cook = action("cook")
        made = event("made", "crafting.completed", {"crafted_object": {"kind": "object", "key": "object:food"}, "quality": "poor"})
        made.update(cause={"event_id": cook["event_id"]}, first_observed_time=time(500))
        eat = action("eat", ("13433", "generic_consume_food"), 600, 1000)
        eat["facts"]["target"] = {"key": "object:food"}
        result = build_experiences([cook, made, eat], "run")
        first, second = units(result, "activities")
        self.assertEqual(len(second["product_links"]), 1)
        self.assertEqual(second["product_links"][0]["activity"], first["id"])
        eat["facts"]["target"] = {"key": "object:other"}
        result = build_experiences([cook, made, eat], "run")
        self.assertFalse(units(result, "activities")[1]["product_links"])

    def test_index_only_interaction_goes_to_review_without_assigning_participation(self):
        other = action("other")
        other["facts"]["actor"]["key"] = "sim:2"
        result = build_experiences([other], "run", "sim:1")
        self.assertFalse(units(result, "activities"))
        self.assertEqual(len(result["review"]), 1)

    def test_provider_scores_are_extracted_before_micro_decision_omission(self):
        events = repeated_micro_fixture()
        for e in events:
            if e.get("category") == "autonomy.decision":
                e["payload"]["stages"][0]["candidates"][0]["score_components"] = {"native_text": "FUN " * 400, "missing": ["structured_provider_contributions"]}
        result = build_experiences(events, "run", "sim:1")
        provider, = [u for u in units(result, "decisions") if u["category"] == "activity_provider_scoring"]
        self.assertEqual(provider["observed_count"], 2)
        self.assertEqual(len(provider["samples"]), 2)
        self.assertTrue(provider["samples"][0]["choice"]["selected"]["score"]["native_text_truncated"])
        self.assertTrue(audit(result, "second_decision")["units"])

    def test_provider_does_not_join_wrong_actor_instance_or_interval(self):
        for case in ("actor", "instance", "time", "target"):
            events = micro_fixture()
            parent, child, decision = events
            if case == "actor": parent["facts"]["actor"]["key"] = "sim:9"
            if case == "instance": parent["facts"]["interaction_id"] = "different"
            if case == "time": parent["started_time"] = time(300)
            if case == "target": parent["facts"]["target"]["key"] = "object:other"
            result = build_experiences(events, "run")
            self.assertFalse([u for u in units(result, "decisions") if u["category"] == "activity_provider_scoring"])

    def test_cooking_direction_decision_is_not_treated_as_a_micro_step(self):
        decision = event("choose_food", "autonomy.decision", {"actor": {"key": "sim:1"},
            "is_script_request": False, "selected": {"action": {"id": "13388", "tuning_name": "fridge_CookAutonomously"}},
            "stages": [{"kind": "interaction", "pool_count": 2, "omitted_count": 0, "probability_basis": "complete_stage_pool",
                        "candidates": [{"selected": True, "weight": 0.6, "probability": 0.6, "action": {"id": "13388", "tuning_name": "fridge_CookAutonomously"}},
                                       {"selected": False, "weight": 0.4, "probability": 0.4, "action": {"id": "snack", "tuning_name": "get_snack"}}]}]})
        result = build_experiences([decision], "run")
        unit, = units(result, "decisions")
        self.assertEqual(unit["category"], "choice")
        self.assertEqual(unit["execution"], "start_not_confirmed")
        self.assertEqual(unit["choices"][0]["alternatives"][0]["probability"], 0.4)

    def test_numeric_observations_are_not_summed_into_a_net_delta(self):
        play = action("play", ("31740", "computer_PlayGame_SimsForeverRenamed"))
        def change(name, at):
            result = event(name, "statistic.direct", {"statistic": {"id": "16655", "tuning_name": "motive_Fun"}, "before": 99, "after": 100})
            result.update(first_observed_time=time(at), cause={"event_id": play["event_id"]})
            return result
        result = build_experiences([play, change("one", 200), change("two", 300)], "run")
        unit, = units(result, "facts")
        self.assertTrue(unit["not_a_net_delta"])
        self.assertEqual(len(unit["observations"]), 2)
        self.assertNotIn("net_delta", unit)

    def test_unknown_resource_version_and_external_payload_are_not_semantically_deleted(self):
        item = action("unknown")
        item["facts"]["tuning_name"] += "_Modded"
        external = event("external", "mood.changed", ["opaque", {"instruction": "not game data"}])
        external.update(origin="external", event_type="external_event")
        result = build_experiences([item, external], "run")
        self.assertEqual(len(result["review"]), 1)
        self.assertEqual(len(result["external"]), 1)
        self.assertFalse(units(result, "states"))
        result = build_experiences(micro_fixture(), "run", game_version="different")
        self.assertEqual(result["metrics"]["selected_evidence_without_units"], 0)

    def test_source_partition_and_reference_integrity_and_no_mutation(self):
        events = [action("cook"), buff("add", 100), buff("end", 500, False), event("unknown", "new.event")]
        before = copy.deepcopy(events)
        result = build_experiences(events, "run", "sim:1")
        all_units = [u for lane, us in result["organized"].items() if isinstance(us, list) for u in us]
        all_units += result["review"] + result["details"] + result["external"]
        ids = {u["id"] for u in all_units}
        for unit in all_units:
            for alias in unit["evidence"]:
                self.assertIn(alias, result["audit"]["evidence"])
        for row in result["audit"]["evidence"].values():
            self.assertTrue(set(row["units"]) <= ids)
            self.assertNotEqual(row.get("disposition"), "unresolved")
        self.assertEqual(result["metrics"]["selected_events"], 4)
        self.assertEqual(events, before)
        self.assertEqual(result, build_experiences(events, "run", "sim:1"))
        result["organized"]["activities"][0]["action"]["name"] = "changed"
        self.assertEqual(events, before)

    def test_rejects_duplicate_revisions_and_source_overwrite_and_damaged_journal(self):
        with self.assertRaises(ValueError): build_experiences([action("a"), action("a")], "run")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "journal.jsonl"
            output = Path(directory) / "view.json"
            source.write_text(json.dumps({"sequence": 1, "session_id": "run", "kind": "event_revision", "event": action("a")}) + "\n", encoding="utf-8")
            original = source.read_bytes()
            command = PYTHON + [str(ROOT / "scripts/experience_view.py"), str(source)]
            run = subprocess.run(command, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [source])
            run = subprocess.run(command + ["--output", str(source)], capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(source.read_bytes(), original)
            output.write_text("previous", encoding="utf-8")
            source.write_bytes(original + b'{"sequence":')
            run = subprocess.run(command + ["--output", str(output)], capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(output.read_text(), "previous")


if __name__ == "__main__":
    unittest.main()
