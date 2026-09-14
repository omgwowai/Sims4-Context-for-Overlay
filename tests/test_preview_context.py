"""Checks the claims made by the offline preview, without game imports or user data."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from preview_context import build_preview, game_milliseconds, load_events, write_packet


def event(eid, sim="1", kind="did", **fields):
    result = {"schema": 1, "event_id": eid, "sim_id": sim, "kind": kind,
              "verb": "sim_Chat", "ts_game": "12:00:00.000 day:0 week:0",
              "ts_real": 100.0, "status": "Complete"}
    result.update(fields)
    return result


def preview(rows, kind="sim", identity="1", **kwargs):
    return build_preview(rows, kind, identity, "synthetic-test-only", **kwargs)


class PreviewTests(unittest.TestCase):
    def test_actor_and_received_are_one_record_with_both_evidence_rows(self):
        rows = [event("a", target={"id": 2, "tuning_name": "object_sim"}),
                event("b", sim="2", kind="received", parent_id="a", by=1)]
        for sid in ("1", "2"):
            packet = preview(rows, identity=sid)
            self.assertEqual(packet["history"]["returned"], 1)
            self.assertEqual(set(packet["history"]["items"][0]["evidence_ids"]), {"a", "b"})
        self.assertIn("Sim 2 是记录中的参与方", preview(rows, identity="2")["history"]["items"][0]["rendering"]["text"])

    def test_disagreement_between_views_is_exposed_in_text_and_issues(self):
        rows = [event("a"), event("b", sim="2", kind="received", parent_id="a", by=1, status="user_cancel")]
        item = preview(rows)["history"]["items"][0]
        self.assertIn("view_status_disagreement", item["issues"])
        self.assertIn("状态记录有差异", item["rendering"]["text"])

    def test_generic_parent_link_does_not_collapse_distinct_interactions(self):
        packet = preview([event("a"), event("b", parent_id="a")])
        self.assertEqual(packet["history"]["returned"], 2)

    def test_missing_parent_keeps_received_view_without_inventing_actor_record(self):
        packet = preview([event("b", kind="received", parent_id="missing", by=2)])
        item = packet["history"]["items"][0]
        self.assertEqual(item["record_id"], "b")
        self.assertIn("unresolved_actor_record", item["issues"])

    def test_identical_ids_deduplicate_but_conflicting_payloads_fail(self):
        packet = preview([event("a"), event("a")])
        self.assertEqual(packet["history"]["returned"], 1)
        self.assertEqual(packet["coverage"]["identical_duplicates_removed"], 1)
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            preview([event("a"), event("a", status="user_cancel")])

    def test_object_instances_and_reference_roles_do_not_mix(self):
        obj = {"id": 22, "tuning_name": "object_chess_table"}
        rows = [event("direct", target=obj), event("other_instance", target=dict(obj, id=23)),
                event("co_present", co_present=[obj]), event("via", via_object=obj),
                event("untyped", target={"id": 22}),
                event("sim", target={"id": 22, "tuning_name": "object_sim"})]
        packet = preview(rows, "object", "22")
        self.assertEqual([i["record_id"] for i in packet["history"]["items"]], ["direct"])

    def test_cancel_and_unknown_status_are_never_rendered_as_completed(self):
        for status in ("user_cancel", "Exited", "surprise_status"):
            with self.subTest(status=status):
                rendering = preview([event("a", status=status)])["history"]["items"][0]["rendering"]
                self.assertNotEqual(rendering["outcome"], "completed")
                self.assertNotIn("交互完成", rendering["text"])

    def test_unmapped_verb_is_visible_and_traceable(self):
        packet = preview([event("a", verb="custom_mod_UnmappedVerb")])
        item = packet["history"]["items"][0]
        self.assertEqual(item["rendering"]["label_source"], "raw_fallback")
        self.assertIn("custom_mod_UnmappedVerb", item["rendering"]["text"])
        self.assertIn("a", packet["evidence"])

    def test_week_boundary_and_limit_select_the_latest_game_records(self):
        rows = [event("old", ts_game="23:59:00.000 day:6 week:0", ts_real=105)]
        rows += [event("n{}".format(n), ts_game="00:00:0{}.000 day:0 week:1".format(n), ts_real=100 + n)
                 for n in range(6)]
        packet = preview(rows)
        self.assertEqual([i["record_id"] for i in packet["history"]["items"]], ["n5", "n4", "n3", "n2", "n1"])
        self.assertTrue(packet["history"]["has_more"])
        self.assertEqual(game_milliseconds("00:00:00.000 day:0 week:1"), 7 * 86400000)

    def test_missing_game_time_changes_ordering_basis_explicitly(self):
        packet = preview([event("a", ts_real=101), event("b", ts_game=None, ts_real=102)])
        self.assertEqual(packet["history"]["ordering"], "recorded_time_fallback")
        self.assertEqual(packet["history"]["items"][0]["record_id"], "b")

    def test_large_ids_survive_json_round_trip_exactly(self):
        sid = 620677770788405884
        rows = [event("a", sim=sid, participants=[sid + 1],
                      target={"id": sid + 2, "guid64": 75396, "tuning_name": "object_Food_GrilledSteak"})]
        packet = json.loads(json.dumps(preview(rows, identity=str(sid))))
        raw = packet["evidence"]["a"]["event"]
        self.assertEqual(raw["sim_id"], str(sid))
        self.assertEqual(raw["participants"], [str(sid + 1)])
        self.assertEqual(raw["target"]["id"], str(sid + 2))
        self.assertEqual(raw["target"]["guid64"], "75396")

    def test_empty_history_is_not_a_live_snapshot_or_complete_history(self):
        packet = preview([])
        self.assertEqual(packet["history"]["returned"], 0)
        self.assertEqual(packet["snapshot"]["status"], "unavailable")
        self.assertEqual(packet["coverage"]["completeness"], "unknown")

    def test_file_sink_and_source_locations_and_malformed_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "1.jsonl").write_text(json.dumps(event("a")) + "\n", encoding="utf-8")
            rows, locations = load_events(root)
            packet = preview(rows, locations=locations)
            output = root / "out" / "context.json"
            write_packet(packet, output)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), packet)
            self.assertEqual(packet["evidence"]["a"]["locations"], [{"file": "1.jsonl", "line": 1}])
            (root / "1.jsonl").write_text('{"incomplete":', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid JSON"):
                load_events(root)


if __name__ == "__main__":
    unittest.main()
