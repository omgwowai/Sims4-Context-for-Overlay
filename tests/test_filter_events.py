"""False-positive and provenance checks for the offline diary-detail policy."""

import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import PYTHON, ROOT
from filter_events import analyze, filter_events


def time(ticks):
    return {"ticks": str(ticks)}


def interaction(identifier, resource=("31740", "computer_PlayGame_SimsForeverRenamed"), start=100, end=10000):
    return {"event_id": "run:" + identifier, "revision": 3, "event_type": "interaction", "origin": "game",
        "tier": "main", "zone_visit": 1, "entities": ["sim:1", "object:2"],
        "first_observed_time": time(start), "started_time": time(start), "ended_time": time(end),
        "stage": "ended", "outcome": "completed", "facts": {
            "interaction_id": identifier, "tuning_id": resource[0], "tuning_name": resource[1],
            "actor": {"key": "sim:1"}, "target": {"key": "object:2"}, "visible": False,
            "finishing_type": "NATURAL", "outcome_result": "NONE"}}


def micro_fixture():
    provider = interaction("provider")
    child = interaction("micro", ("31664", "Computer_Use_PlayGame_Mild"), 200, 1700)
    child["facts"]["decision_event_id"] = "run:decision"
    decision = {"event_id": "run:decision", "revision": 2, "event_type": "game_event", "origin": "game",
        "category": "autonomy.decision", "entities": ["sim:1"], "zone_visit": 1, "tier": "internal",
        "first_observed_time": time(200), "payload": {
            "actor": {"key": "sim:1"}, "interaction_event_id": child["event_id"],
            "selected": {"action": {"id": "31664", "tuning_name": "Computer_Use_PlayGame_Mild"}},
            "mode": "SubActionAutonomy", "context_source": "AUTONOMY", "is_script_request": False,
            "mixer_rejected_attempts": 0, "retention_gate": "queue_success",
            "execution_result": {"returned_success": True, "exception_type": None},
            "stages": [
                {"kind": "mixer_provider", "candidates": [{"selected": True, "interaction_id": "provider",
                    "action": {"id": "31740", "tuning_name": "computer_PlayGame_SimsForeverRenamed"},
                    "target": {"key": "object:2"}}]},
                {"kind": "interaction", "omitted_count": 0, "candidates": [{"action": {
                    "id": "31664", "tuning_name": "Computer_Use_PlayGame_Mild", "resource_kind": "interaction"}}]}]}}
    return [provider, child, decision]


def repeated_micro_fixture():
    events = micro_fixture()
    child, decision = copy.deepcopy(events[1:])
    child.update(event_id="run:second_micro", first_observed_time=time(2000),
                 started_time=time(2000), ended_time=time(3500))
    child["facts"].update(interaction_id="second_micro", decision_event_id="run:second_decision")
    decision.update(event_id="run:second_decision", first_observed_time=time(2000))
    decision["payload"]["interaction_event_id"] = child["event_id"]
    return events + [child, decision]


def event(identifier, category, payload=None):
    return {"event_id": "run:" + identifier, "revision": 1, "event_type": "game_event", "origin": "game",
            "zone_visit": 1, "entities": ["sim:1"], "roles": [{"role": "subject", "entity_key": "sim:1"}],
            "category": category, "first_observed_time": time(200), "payload": payload or {}}


def refill(identifier, at, before=99.583333, parent="run:nap"):
    item = event(identifier, "statistic.direct", {"statistic": {
        "id": "29111", "tuning_name": "commodity_Trait_Autonomy_Lazy", "visible": False},
        "before": before, "after": 100})
    item.update(first_observed_time=time(at), cause={"event_id": parent, "operation": {"type": "StatisticSetMaxOp"}})
    return item


def retained(result):
    return {e["event_id"] for e in result["history"]["events"]}


class FilterEventsTests(unittest.TestCase):
    def test_micro_link_uses_instance_and_retains_real_cancelled_activity(self):
        events = micro_fixture()
        events[0]["outcome"] = "cancelled"
        events[0]["facts"]["finishing_type"] = "INTERACTION_INCOMPATIBILITY"
        original = copy.deepcopy(events)
        result = filter_events(events, "run")
        self.assertEqual(retained(result), {"run:provider", "run:decision"})
        audit = result["filtering"]["omitted"]
        self.assertEqual(audit[0]["related"], [{"event_id": "run:provider", "revision": 3},
                                               {"event_id": "run:decision", "revision": 2}])
        self.assertEqual(audit[0]["link_basis"], "recorded_mixer_provider_instance_not_parent_event_id")
        self.assertEqual(events, original)
        self.assertEqual(result, filter_events(events, "run"))
        result["history"]["events"][0]["facts"]["tuning_name"] = "changed"
        self.assertEqual(events, original)

    def test_repeated_micro_decisions_keep_first_provider_sample(self):
        events = repeated_micro_fixture()
        result = filter_events(events, "run")
        self.assertEqual(retained(result), {"run:provider", "run:decision"})
        omitted = {e["event_id"]: e for e in result["filtering"]["omitted"]}
        self.assertEqual(omitted["run:second_decision"]["related"][-1], {"event_id": "run:decision", "revision": 2})
        self.assertEqual(retained(filter_events(list(reversed(events)), "run")), retained(result))

    def test_unknown_resource_identity_and_missing_parent_evidence_survive(self):
        cases = ["id", "name", "link", "actor", "target", "visit", "missing_visit", "ambiguous", "out_of_interval", "long", "open", "failure"]
        for case in cases:
            with self.subTest(case=case):
                events = micro_fixture()
                provider, child, decision = events
                if case == "id": child["facts"]["tuning_id"] = "999"
                if case == "name": child["facts"]["tuning_name"] += "_Modded"
                if case == "link": child["facts"].pop("decision_event_id")
                if case == "actor": provider["facts"]["actor"]["key"] = "sim:9"
                if case == "target": provider["facts"]["target"]["key"] = "object:9"
                if case == "visit": provider["zone_visit"] = 2
                if case == "missing_visit":
                    for e in events: e.pop("zone_visit")
                if case == "ambiguous": events.append(dict(copy.deepcopy(provider), event_id="run:ambiguous"))
                if case == "out_of_interval": provider["started_time"] = time(300)
                if case == "long": child["ended_time"] = time(25000)
                if case == "open": child.update(ended_time=None, stage="running", outcome="unknown")
                if case == "failure": child["facts"]["outcome_result"] = "FAILURE"
                self.assertEqual(retained(filter_events(events, "run")), {e["event_id"] for e in events})

    def test_decision_with_rejections_unseen_choices_or_meaningful_alternative_survives(self):
        for case in ("rejections", "truncated", "social", "unknown", "script", "exception", "different_decision"):
            with self.subTest(case=case):
                events = repeated_micro_fixture()
                decision = events[-1]
                payload = decision["payload"]
                stage = payload["stages"][-1]
                if case == "rejections": payload["mixer_rejected_attempts"] = 1
                if case == "truncated": stage["omitted_count"] = 1
                if case == "social": stage["candidates"].append({"action": {"id": "26683", "tuning_name": "mixer_social_StartPreposterousRumor_group_mischief_skills", "resource_kind": "interaction"}})
                if case == "unknown": stage["candidates"][0]["action"]["tuning_name"] = "Unknown"
                if case == "script": payload["is_script_request"] = True
                if case == "exception": payload["execution_result"]["exception_type"] = "Error"
                if case == "different_decision":
                    decision = dict(copy.deepcopy(decision), event_id="run:unlinked_decision")
                    events.append(decision)
                self.assertIn(decision["event_id"], retained(filter_events(events, "run")))

    def test_effect_prevents_action_and_decision_suppression_even_outside_entity_query(self):
        events = micro_fixture()
        effect = event("result", "skill.level", {"before": 1, "after": 2})
        effect.update(entities=["sim:9"], cause={"event_id": "run:micro"})
        events.append(effect)
        result = filter_events(events, "run", "sim:1")
        self.assertEqual(retained(result), {"run:provider", "run:micro", "run:decision"})
        self.assertEqual(result["filtering"]["metrics"]["source_latest_events"], 4)

    def test_zero_step_does_not_remove_long_unknown_or_failed_technical_interactions(self):
        base = interaction("step", ("13983", "sim-stand"), 100, 100)
        base["tier"] = "internal"
        base["facts"].update(classification="technical_interaction_source", trigger={"name": "POSTURE_GRAPH"})
        self.assertFalse(retained(filter_events([base], "run")))
        for case in ("long", "unknown", "visible", "failure", "source"):
            item = copy.deepcopy(base)
            if case == "long": item["ended_time"] = time(10000)
            if case == "unknown": item["facts"]["tuning_id"] = "999"
            if case == "visible": item["facts"]["visible"] = True
            if case == "failure": item["facts"]["outcome_result"] = "FAILURE"
            if case == "source": item["facts"]["trigger"] = {"name": "AUTONOMY"}
            self.assertIn(item["event_id"], retained(filter_events([item], "run")))

    def test_router_needs_started_continuation_same_actor_and_visit(self):
        router = interaction("router", ("30917", "SocialPickerSI"), 100, 100)
        child = interaction("social")
        child["facts"]["parent_event_id"] = router["event_id"]
        self.assertEqual(retained(filter_events([router, child], "run")), {"run:social"})
        self.assertEqual(retained(filter_events([router], "run")), {"run:router"})
        for case in ("no_start", "actor", "visit"):
            changed = copy.deepcopy(child)
            if case == "no_start": changed["started_time"] = None
            if case == "actor": changed["facts"]["actor"]["key"] = "sim:9"
            if case == "visit": changed["zone_visit"] = 2
            self.assertIn(router["event_id"], retained(filter_events([router, changed], "run")))

    def test_buff_refresh_compares_entire_captured_state_except_handles(self):
        before = {"buff": {"id": "10"}, "reason": None, "mood": None, "handles": ["1"]}
        item = event("refresh", "buff.refreshed", {"before": before, "after": dict(before, handles=["2"])})
        self.assertFalse(retained(filter_events([item], "run")))
        for field in ("reason", "mood", "mood_weight", "unknown"):
            changed = copy.deepcopy(item)
            changed["payload"]["after"][field] = "new"
            self.assertIn(item["event_id"], retained(filter_events([changed], "run")))
        item["payload"]["new_capture_field"] = "important"
        self.assertIn(item["event_id"], retained(filter_events([item], "run")))

    def test_lazy_refills_keep_initial_transition_and_representative_not_sum(self):
        nap = interaction("nap", ("14305", "sofa_Nap"), 100, 20000)
        events = [nap, refill("initial", 200, -100), refill("first", 1700), refill("second", 3200), refill("third", 4700)]
        result = filter_events(events, "run")
        self.assertEqual(retained(result), {"run:nap", "run:initial", "run:first"})
        fold = result["filtering"]["folds"][0]
        self.assertEqual(fold["observed_count"], 3)
        self.assertEqual(fold["omitted_count"], 2)
        self.assertEqual(fold["first_time"], time(1700))
        self.assertEqual(fold["last_time"], time(4700))
        self.assertTrue(fold["not_a_net_statistic_delta"])
        self.assertEqual(fold["representative"], {"event_id": "run:first", "revision": 1})

    def test_lazy_refills_do_not_cross_gaps_causes_visits_people_or_intervening_change(self):
        for case in ("gap", "cause", "visit", "subject", "intervening", "different_value", "protected"):
            with self.subTest(case=case):
                nap = interaction("nap", ("14305", "sofa_Nap"), 100, 20000)
                first, second = refill("first", 1700), refill("second", 3200)
                events = [nap, first, second]
                if case == "gap": second["first_observed_time"] = time(10000)
                if case == "cause": second["cause"]["event_id"] = "run:another_nap"
                if case == "visit": second["zone_visit"] = 2
                if case == "subject": second["roles"][0]["entity_key"] = "sim:9"
                if case == "intervening": events.append(refill("transition", 2500, -100))
                if case == "different_value": second["payload"]["before"] = 99.8
                if case == "protected": events.append(dict(event("result", "unknown"), cause={"event_id": second["event_id"]}))
                self.assertIn(second["event_id"], retained(filter_events(events, "run")))

    def test_rare_outcomes_hidden_motives_social_decisions_and_attempts_survive(self):
        events = [event(name, name, {"visible": False, "outcome_result": "FAILURE"}) for name in (
            "skill.level", "aspiration.goal_completed", "payment.completed", "crafting.completed",
            "relationship.knowledge", "pregnancy.started", "career.promoted", "unknown")]
        decision = micro_fixture()[-1]
        decision["payload"]["selected"]["action"] = {"id": "26683", "tuning_name": "mixer_social_StartPreposterousRumor_group_mischief_skills"}
        attempt = interaction("cry", ("24238", "Bed_Undercovers_Cry"))
        attempt.update(started_time=None, outcome="cancelled")
        attempt["facts"]["finishing_type"] = "FAILED_TESTS"
        events.extend([decision, attempt, event("future_payload", "unknown_event_shape", ["future"])])
        self.assertEqual(retained(filter_events(events, "run")), {e["event_id"] for e in events})

    def test_external_payload_is_opaque_and_unknown_origins_are_kept(self):
        # A non-object payload would crash game rules if traversed accidentally.
        external = event("external", "statistic.direct", ["opaque"])
        external.update(event_type="external_event", origin="external")
        odd = dict(copy.deepcopy(external), event_id="run:odd", origin="game")
        unknown = dict(copy.deepcopy(external), event_id="run:future", origin="future")
        result = filter_events([external, odd, unknown], "run")
        self.assertEqual(result["history"]["events"], [external, odd, unknown])

    def test_coverage_and_entity_filter_do_not_imply_subject_or_observation(self):
        events = micro_fixture()
        result = filter_events(events, "run", "object:2")
        metrics = result["filtering"]["metrics"]
        self.assertEqual(metrics["selected_latest_events"], 2)
        ids = retained(result) | {row["event_id"] for row in result["filtering"]["omitted"]}
        self.assertEqual(ids, {"run:provider", "run:micro"})
        self.assertEqual(metrics["retained_events"] + metrics["omitted_events"], 2)
        self.assertEqual(metrics["retained_events_json_bytes"], len(json.dumps(result["history"]["events"],
            ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")))
        self.assertEqual(filter_events(events, "run", "sim:missing")["filtering"]["metrics"]["selected_latest_events"], 0)

    def test_rejects_duplicate_revisions_other_sessions_and_grouped_packets(self):
        item = interaction("a")
        for events in ([item, copy.deepcopy(item)], [dict(item, event_id="other:a")], [dict(item, effects=[])], [dict(item, revision=0)]):
            with self.assertRaises(ValueError): filter_events(events, "run")

    def test_cli_replays_revisions_keeps_inputs_and_does_not_write_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "journal.jsonl", Path(directory) / "view.json"
            initial = interaction("a")
            latest = dict(copy.deepcopy(initial), revision=4, outcome="cancelled")
            rows = [dict(sequence=i, session_id="run", kind="event_revision", event=e) for i, e in enumerate([initial, latest], 1)]
            source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
            original = source.read_bytes()
            command = PYTHON + [str(ROOT / "scripts/filter_events.py"), str(source)]
            run = subprocess.run(command, capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [source])
            self.assertEqual(json.loads(run.stdout)["selected_latest_events"], 1)
            run = subprocess.run(command + ["--output", str(output)], capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["history"]["events"], [latest])
            self.assertEqual(result["source"]["path"], str(source.resolve()))
            run = subprocess.run(command + ["--output", str(source)], capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(source.read_bytes(), original)
            source.write_bytes(original + b'{"sequence":')
            output.write_text("previous", encoding="utf-8")
            run = subprocess.run(command + ["--output", str(output)], capture_output=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(output.read_text(), "previous")
            with self.assertRaises(ValueError): analyze(source)


if __name__ == "__main__":
    unittest.main()
