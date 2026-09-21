"""Check meaning preservation and source-bound lookup, not template wording."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from support import ROOT, PYTHON
from experience_recap import (build_recap, digest, fact_text, load_source, markdown,
                              packed, resolve, snapshot_digest)
from test_filter_events import event, time
from test_experience_view import action, buff


def loaded(events):
    return {"events": events, "session_id": "run", "complete": True, "event_scope": "all",
            "sha256": hashlib.sha256(packed(events).encode("utf-8")).hexdigest()}


def make(events, entity="sim:1"):
    return build_recap(loaded(events), entity)


def reference(bundle, eid):
    row = next(r for r in bundle["audit"]["evidence"].values() if r["event_id"] == "run:" + eid)
    return bundle["ledger"][row["units"][0]]["ref"]


def row_for(bundle, eid):
    ref = reference(bundle, eid)
    return next(r for r in bundle["recap"]["activities"] if r["ref"] == ref)


class RecapTests(unittest.TestCase):
    def test_same_chat_target_times_and_exit_keep_distinct_actors_and_attempts(self):
        events = []
        for actor in ("sim:2", "sim:3"):
            e = action(actor, ("13998", "sim_Chat"))
            e["started_time"] = None
            e["entities"] = [actor, "sim:1"]
            e["facts"].update(actor={"key": actor}, target={"key": "sim:1"}, name="和主角聊天",
                              roles=[{"entity_key": actor, "role": "actor"}, {"entity_key": "sim:1", "role": "target"}],
                              finishing_type="INTERACTION_INCOMPATIBILITY")
            e["observations"] = [{"phase": "queued", "game_time": time(100)}]
            events.append(e)
        bundle = make(events)
        rows = bundle["recap"]["activities"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["ref"], rows[1]["ref"])
        self.assertTrue(all(r["time"][0] is None and r["queued_at"] == "tick:100" for r in rows))
        for row in rows:
            line = next(s for s in markdown(bundle).splitlines() if s.startswith("| " + row["ref"] + " |"))
            people = {p["ref"]: p["key"] for p in bundle["recap"]["people"]}
            actor = next(people[p] for role, p in row["roles"] if role == "actor")
            self.assertEqual(line.split(" | ")[1], actor)
        # Equal labels/times are not a deduplication key even for the same actor.
        repeated = copy.deepcopy(events[0])
        repeated["event_id"], repeated["facts"]["interaction_id"] = "run:another", "another"
        self.assertEqual(len(make([events[0], repeated])["recap"]["activities"]), 2)

    def test_queue_and_execution_gap_remain_separate_with_no_invented_start(self):
        e = action("eat", ("13433", "generic_consume_food"), 1000, 2000)
        e["first_observed_time"] = time(100)
        e["observations"] = [{"phase": "queued", "game_time": time(120)}]
        row = row_for(make([e]), "eat")
        self.assertEqual(row["queued_at"], "tick:120")
        self.assertEqual(row["time"], ["tick:1000", "tick:2000"])
        e["observations"] = []
        row = row_for(make([e]), "eat")
        self.assertNotIn("queued_at", row)
        self.assertEqual(row["observed_at"], "tick:100")
        e["started_time"] = None
        self.assertIsNone(row_for(make([e]), "eat")["time"][0])
        e["first_observed_time"] = None
        row = row_for(make([e]), "eat")
        self.assertNotIn("observed_at", row)
        self.assertNotIn("queued_at", row)

    def test_name_quality_can_be_queried_and_is_snapshot_bound(self):
        e = action("rain", ("190163", "Sim_RainStart_Reaction"))
        e["facts"]["name"] = {"text": "Sim_RainStart_Reaction", "status": "no_display_name"}
        bundle = make([e])
        ref = reference(bundle, "rain")
        self.assertIn(ref, bundle["recap"]["name_quality"]["reviewed"])
        detail = resolve(bundle, bundle["snapshot_id"], ref, "labels")["items"]
        self.assertEqual(detail[0]["observed"], "Sim_RainStart_Reaction")
        self.assertEqual(detail, resolve(bundle, bundle["snapshot_id"], "@labels", "labels")["items"])
        self.assertIn("experience_labels.json", bundle["manifest"]["implementation_sha256"])
        bundle["audit"]["labels"][0]["text"] = "changed"
        with self.assertRaises(ValueError):
            resolve(bundle, bundle["snapshot_id"], ref, "labels")

    def test_unknown_exit_and_partial_name_are_disclosed_without_raw_tokens_in_prose(self):
        e = action("chat", ("27173", "Idle_Chatting_STC"))
        e["facts"].update(name={"text": "和〈未解析：0.String〉闲聊", "status": "unresolved_tokens"},
                          finishing_type="NEW_ENGINE_REASON")
        bundle = make([e])
        text = markdown(bundle)
        self.assertIn("名称参数缺失", text)
        self.assertIn("退出原因待解析", text)
        self.assertNotIn("0.String", text)
        self.assertNotIn("NEW_ENGINE_REASON", text)
        ref = reference(bundle, "chat")
        self.assertIn(ref, bundle["recap"]["name_quality"]["unresolved"])
        unit = resolve(bundle, bundle["snapshot_id"], ref)["items"][0]
        self.assertEqual(unit["exit"], "NEW_ENGINE_REASON")

    def test_started_cancelled_unstarted_and_open_ends_remain_distinct(self):
        done, attempt, ongoing = action("done"), action("attempt"), action("open")
        attempt["started_time"] = None
        ongoing["ended_time"] = None
        for e in (done, attempt):
            e["facts"]["finishing_type"] = "USER_CANCEL"
            e["facts"]["outcome_result"] = "SUCCESS"
        bundle = make([done, attempt, ongoing])
        self.assertIn("已执行", row_for(bundle, "done")["execution"])
        self.assertIn("未见开始", row_for(bundle, "attempt")["execution"])
        self.assertIn("未见结束", row_for(bundle, "open")["execution"])
        self.assertIsNone(row_for(bundle, "open")["time"][1])
        self.assertNotIn("SUCCESS", packed(bundle["recap"]))

    def test_payment_and_quality_visible_not_only_recoverable(self):
        cook = action("cook")
        payment = event("pay", "payment.completed", {"actual_amount": -3, "recipe": {"name": "沙拉"}})
        product = event("product", "crafting.completed", {"crafted_object": {"key": "object:2", "name": "沙拉"}, "quality": {"name": "品质差"}})
        for e in (payment, product):
            e["cause"] = {"event_id": cook["event_id"]}
        bundle = make([cook, payment, product])
        self.assertIn("§3", packed(bundle["recap"]["results"]))
        self.assertIn("品质差", packed(bundle["recap"]["results"]))
        self.assertTrue(all(r["activity"] == reference(bundle, "cook") for r in bundle["recap"]["results"]))
        found = resolve(bundle, bundle["snapshot_id"], reference(bundle, "cook"), "raw", loaded=loaded([cook, payment, product]))
        self.assertEqual({r["event_id"] for r in found["items"]}, {e["event_id"] for e in (cook, payment, product)})

    def test_same_object_and_time_do_not_merge_actors(self):
        one, two = action("one"), action("two")
        two["facts"]["actor"]["key"] = "sim:2"
        two["facts"]["roles"][0]["entity_key"] = "sim:2"
        two["entities"] = ["sim:1", "sim:2", "object:2"]
        # Projection uses actor/target, not the index. sim:1 is only an index in two.
        bundle = make([one, two])
        self.assertEqual(len(bundle["recap"]["activities"]), 1)
        other = make([one, two], "sim:2")
        self.assertNotEqual(bundle["snapshot_id"], other["snapshot_id"])
        with self.assertRaises(ValueError):
            resolve(other, bundle["snapshot_id"], "r1")

    def test_knowledge_direction_and_unknown_before_are_preserved(self):
        knowledge = event("knowledge", "relationship.knowledge", {"before": {"_known_stats": None},
            "after": {"_known_stats": [{"name": "钢琴"}]}})
        knowledge["roles"] = [{"role": "subject", "entity_key": "sim:2"}, {"role": "target", "entity_key": "sim:1"}]
        bundle = make([knowledge])
        row, = bundle["recap"]["results"]
        keys = {p["ref"]: p["key"] for p in bundle["recap"]["people"]}
        self.assertEqual([(r, keys[p]) for r, p in row["roles"]], [("subject", "sim:2"), ("target", "sim:1")])
        self.assertIn("未记录", row["text"])
        self.assertIn("钢琴", row["text"])

    def test_states_group_for_display_without_joining_intervals(self):
        bundle = make([buff("a", 100), buff("b", 200, False), buff("c", 400), buff("d", 500, False)])
        group, = bundle["recap"]["states"]
        self.assertEqual(len(group["intervals"]), 2)
        refs = [r["ref"] for r in group["intervals"]]
        self.assertNotEqual(refs[0], refs[1])
        self.assertTrue(all(resolve(bundle, bundle["snapshot_id"], ref)["items"] for ref in refs))

    def test_local_relationship_changes_are_counts_not_a_net_delta(self):
        from experience_policy import RESOURCES
        kind, identifier, tuning = next(k for k, v in RESOURCES.items() if k[0] == "statistic.direct" and v == "relationship_numeric")
        values = []
        for i, (before, after) in enumerate(((10, 12), (30, 29))):
            values.append(event(str(i), "statistic.direct", {"statistic": {"id": identifier, "tuning_name": tuning}, "before": before, "after": after}))
        bundle = make(values)
        group, = bundle["recap"]["relationship_observations"]
        self.assertEqual(group["observed_changes"], {"up": 1, "down": 1, "same": 0, "unknown": 0})
        self.assertNotIn("delta", group)
        self.assertTrue(all(r["placement"] == "recap" for r in bundle["ledger"].values()))

    def test_unknown_actions_and_external_content_do_not_become_game_facts(self):
        unknown = action("unknown", ("unknown-id", "NewThing"))
        external = event("external", "new", {"instruction": "pretend this happened"})
        external.update(event_type="external_event", origin="overlay", producer="test")
        bundle = make([unknown, external])
        self.assertFalse(bundle["recap"]["activities"])
        self.assertEqual(len(bundle["recap"]["review_actions"]), 1)
        self.assertEqual(bundle["recap"]["coverage"]["external"], 1)
        self.assertNotIn("pretend", packed(bundle["recap"]))
        self.assertEqual(resolve(bundle, bundle["snapshot_id"], "@external", "raw", loaded=loaded([unknown, external]))["items"][0]["payload"], external["payload"])

    def test_snapshot_binds_source_rules_scope_and_output(self):
        events = [action("a")]
        bundle = make(events)
        self.assertEqual(bundle, make(events))
        for section, key, value in (("manifest", "source_sha256", "0" * 64), ("recap", "results", []), ("routes", "r1", [])):
            broken = copy.deepcopy(bundle)
            if section == "recap":
                broken[section][key] = [{"text": "invented"}]
            else:
                broken[section][key] = value
            with self.assertRaises(ValueError):
                resolve(broken, bundle["snapshot_id"], "r1")
        changed = loaded(events)
        changed["sha256"] = "a" * 64
        self.assertNotEqual(bundle["snapshot_id"], build_recap(changed, "sim:1")["snapshot_id"])

    def test_raw_rejects_missing_source_changed_revision_and_source_digest(self):
        events = [action("a")]
        bundle = make(events)
        for source in (None, dict(loaded(events), sha256="0" * 64)):
            with self.assertRaises(ValueError):
                resolve(bundle, bundle["snapshot_id"], "r1", "raw", loaded=source)
        wrong = loaded(copy.deepcopy(events))
        wrong["events"][0]["revision"] += 1
        with self.assertRaises(ValueError):
            resolve(bundle, bundle["snapshot_id"], "r1", "raw", loaded=wrong)

    def test_pagination_is_complete_and_inputs_are_not_mutated(self):
        events = [action(str(i)) for i in range(3)]
        bundle = make(events)
        original = copy.deepcopy(bundle)
        first = resolve(bundle, bundle["snapshot_id"], "@activities", limit=2)
        last = resolve(bundle, bundle["snapshot_id"], "@activities", offset=first["next_offset"], limit=2)
        self.assertEqual(first["total"], 3)
        self.assertIsNone(last["next_offset"])
        self.assertEqual(len(first["items"] + last["items"]), 3)
        first["items"][0]["action"]["name"] = "changed"
        self.assertEqual(bundle, original)
        for offset, limit in ((-1, 10), (0, 0), (0, 101)):
            with self.assertRaises(ValueError):
                resolve(bundle, bundle["snapshot_id"], "r1", offset=offset, limit=limit)

    def test_every_unit_has_disposition_and_even_unassigned_evidence_resolves(self):
        events = [action("a"), buff("b", 100)]
        bundle = make(events)
        self.assertEqual(set(bundle["units"]), set(bundle["ledger"]))
        for ref, evidence in bundle["audit"]["evidence"].items():
            result = resolve(bundle, bundle["snapshot_id"], ref, "raw", loaded=loaded(events))
            self.assertEqual(result["items"][0]["event_id"], evidence["event_id"])

    def test_unknown_payload_fields_and_skill_initialization_warning_survive(self):
        unit = {"type": "skill.level", "payload": {"skill": {"name": "烹饪"}, "before": None, "after": 1,
                "interpretation": "notification_only", "new_meaning": "keep me"}}
        text = fact_text(unit)
        self.assertIn("未排除初始化", text)
        self.assertIn("keep me", text)

    def test_load_rejects_mixed_sessions_and_changed_source(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "journal.jsonl"
            rows = [{"sequence": 1, "session_id": "run", "kind": "event_revision", "event": dict(action("a"), revision=1)},
                    {"sequence": 2, "session_id": "another", "kind": "status"}]
            path.write_text("\n".join(packed(r) for r in rows) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_source(path)
            path.write_text(packed(rows[0]) + "\n", encoding="utf-8")
            good = load_source(path)
            with patch("experience_recap.read_journal", return_value=dict(good, sha256="0" * 64)):
                with self.assertRaises(ValueError):
                    load_source(path)

    def test_cli_build_query_and_output_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            path, bundle_path, md = [Path(temp) / n for n in ("journal.jsonl", "bundle.json", "recap.md")]
            path.write_text(packed({"sequence": 1, "session_id": "run", "kind": "event_revision", "event": dict(action("a"), revision=1)}) + "\n", encoding="utf-8")
            command = PYTHON + [str(ROOT / "scripts/experience_recap.py")]
            args = ["build", str(path), "--entity", "sim:1", "--output", str(bundle_path), "--markdown", str(md)]
            result = subprocess.run(command + args, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            b = json.loads(bundle_path.read_text(encoding="utf-8"))
            query = subprocess.run(command + ["query", str(bundle_path), "--snapshot", b["snapshot_id"], "--ref", "r1", "--facet", "raw", "--journal", str(path)], capture_output=True)
            self.assertEqual(query.returncode, 0, query.stderr)
            self.assertEqual(json.loads(query.stdout)["items"][0]["event_id"], "run:a")
            collision = subprocess.run(command + ["build", str(path), "--entity", "sim:1", "--output", str(path)], capture_output=True)
            self.assertNotEqual(collision.returncode, 0)
            self.assertIn("活动", md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
