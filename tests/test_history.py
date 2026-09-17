"""Index/query invariants and resource limits, without loading the game."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import MemoryJournal, facts
from context_overlay.collector import Collector
from context_overlay.history import HistoryError, MIB
from context_overlay.model import entity
from context_overlay.recorder import Recorder
from context_overlay.storage import Journal, StorageError
from context_overlay.game_runtime import Runtime
from context_overlay import game_runtime


ACTOR = facts()["actor"]["key"]


class IndexedHistoryChecks(unittest.TestCase):
    def setUp(self):
        self.journal = MemoryJournal()
        self.recorder = Recorder(self.journal, "test-run")

    def add(self, identifier, when, **extra):
        return self.recorder.interaction("started", dict(facts(identifier), **extra), when, "native")

    def test_shared_body_new_target_and_legacy_update_order(self):
        first = self.add(1, 10)
        second = self.add(2, 20)
        updated = self.recorder.interaction("exited", dict(facts(1, 456), finishing_type="NATURAL"), 30, "exit")
        self.assertEqual(len(self.recorder.events), 2)
        self.assertEqual(len(self.recorder.index.entities[ACTOR].by_id), 2)
        self.assertEqual(self.recorder.history(ACTOR)["events"][0]["event_id"], first["event_id"])
        for target in ("object:123", "object:456"):
            matches = self.recorder.history(target)["events"]
            self.assertEqual(matches[0]["event_id"], updated["event_id"])
            self.assertEqual(matches[0]["revision"], 2)
        ordered = self.recorder.query_history(ACTOR, order="asc")["events"]
        self.assertEqual([e["event_id"] for e in ordered], [first["event_id"], second["event_id"]])

    def test_query_skips_unrelated_global_events(self):
        for i in range(30):
            self.add(i + 1, i)
        other = entity("sim", 99)
        self.recorder.change([other], "buffs", None, {"id": "8"}, 17, "native")
        result = self.recorder.query_history(other["key"])
        self.assertEqual(result["candidates_examined"], 1)
        self.assertEqual(len(result["events"]), 1)

    def test_time_boundaries_out_of_order_and_ties(self):
        a, b, c, d = [self.add(i, t) for i, t in ((1, 20), (2, 10), (3, 20), (4, 30))]
        self.recorder.link_decision(a["event_id"], "decision")
        for order, expected in (("asc", [b, a, c]), ("desc", [c, a, b])):
            q = self.recorder.query_history(ACTOR, from_ticks="10", to_ticks="30", order=order)
            self.assertEqual([e["event_id"] for e in q["events"]], [e["event_id"] for e in expected])
            self.assertEqual(q["candidates_examined"], 3)

    def test_end_time_does_not_prune_early_start_and_unknown_start_is_excluded(self):
        early = self.add(1, 1)
        self.recorder.interaction("exited", dict(facts(1), finishing_type="NATURAL"), 50, "exit")
        end_only = self.recorder.interaction("exited", dict(facts(2), finishing_type="USER_CANCEL"), 55, "exit")
        later = self.add(3, 10)
        self.recorder.interaction("exited", dict(facts(3), finishing_type="NATURAL"), 50, "exit")
        self.recorder.link_decision(early["event_id"], "decision")
        for time_field, bounds in (("started", (1, 11)), ("ended", (50, 60))):
            for order, expected in (("asc", [early, later]), ("desc", [later, early])):
                page = self.recorder.query_history(ACTOR, time_field=time_field, from_ticks=bounds[0], to_ticks=bounds[1],
                                                   outcomes=["completed"], order=order)
                self.assertEqual([e["event_id"] for e in page["events"]], [e["event_id"] for e in expected])
        started = self.recorder.query_history(ACTOR, time_field="started")
        self.assertNotIn(end_only["event_id"], [e["event_id"] for e in started["events"]])

    def test_type_field_tuning_and_internal_filters(self):
        main = self.add(1, 10)
        internal = self.add(2, 11, tier="internal")
        change = self.recorder.change([facts()["actor"]], "buffs", None, {"id": "42"}, 20, "TestEvent.BuffBeganEvent")
        q = self.recorder.query_history(ACTOR, event_types=["state_change"], fields=["buffs"], from_ticks=20, to_ticks=21)
        self.assertEqual([e["event_id"] for e in q["events"]], [change["event_id"]])
        interactions = self.recorder.query_history(ACTOR, event_types=["interaction"], tuning_ids=["321"])
        self.assertEqual([e["event_id"] for e in interactions["events"]], [main["event_id"]])
        all_interactions = self.recorder.query_history(ACTOR, event_types=["interaction"], include_internal=True)
        self.assertEqual(len(all_interactions["events"]), 2)
        self.assertIn(internal["event_id"], [e["event_id"] for e in all_interactions["events"]])

    def test_pages_freeze_membership_revisions_metadata_and_allow_retry(self):
        events = [self.add(i, i) for i in range(1, 5)]
        first = self.recorder.query_history(ACTOR, page_size=2, order="asc", outcomes=["unknown"])
        cursor = first["next_cursor"]
        before = self.recorder.history_page(cursor)
        self.recorder.interaction("exited", dict(facts(3), finishing_type="NATURAL"), 50, "exit")
        self.add(5, 60)
        self.recorder.leave(facts()["actor"], 61)
        after = self.recorder.history_page(cursor)
        for key in ("events", "total_matches", "coverage", "target_observation", "as_of_sequence"):
            self.assertEqual(before[key], after[key])
        self.assertEqual(after["events"][0]["revision"], 1)
        self.assertEqual(after["total_matches"], 4)
        self.assertIsNone(after["next_cursor"])
        first["events"][0]["facts"]["actor"]["name"] = "consumer mutation"
        self.assertNotEqual(self.recorder.events[events[0]["event_id"]]["facts"]["actor"]["name"], "consumer mutation")
        current = self.recorder.query_history(ACTOR, outcomes=["completed"])
        self.assertEqual(current["events"][0]["revision"], 2)

    def test_pagination_matches_one_query_without_duplicates(self):
        for i in range(17):
            self.add(i + 1, i // 3)
        first = self.recorder.query_history(ACTOR, page_size=4)
        result, page = [], first
        while True:
            result.extend(e["event_id"] for e in page["events"])
            if not page["next_cursor"]:
                break
            page = self.recorder.history_page(page["next_cursor"])
        full = self.recorder.query_history(ACTOR, page_size=50)
        self.assertEqual(result, [e["event_id"] for e in full["events"]])
        self.assertEqual(len(set(result)), 17)

    def test_query_expiry_release_and_session_change(self):
        now = [1.0]
        recorder = Recorder(MemoryJournal(), "old", clock=lambda: now[0], snapshot_ttl=10)
        recorder.interaction("started", facts(), 1, "native")
        q = recorder.query_history(ACTOR)
        new = Recorder(MemoryJournal(), "new")
        with self.assertRaisesRegex(HistoryError, "session_changed"):
            new.history_page(q["cursor"])
        now[0] = 11
        with self.assertRaisesRegex(HistoryError, "cursor_expired"):
            recorder.history_page(q["cursor"])
        self.assertEqual(recorder.index.status()["snapshot_references"], 0)
        q = recorder.query_history(ACTOR)
        recorder.close_query(q["cursor"])
        self.assertEqual(recorder.index.status()["snapshot_charged_bytes"], 0)
        q = recorder.query_history(ACTOR)
        recorder.close_queries()
        with self.assertRaisesRegex(HistoryError, "session_closed"):
            recorder.history_page(q["cursor"])

    def test_query_budgets_do_not_stop_recording_or_leak_partial_snapshot(self):
        recorder = Recorder(MemoryJournal(), "budget", snapshot_refs=1, snapshot_limit=1)
        for i in range(2):
            recorder.interaction("started", facts(i + 1), i, "native")
        with self.assertRaisesRegex(HistoryError, "query_budget"):
            recorder.query_history(ACTOR)
        self.assertEqual(recorder.index.status()["snapshots"], 0)
        q = recorder.query_history(ACTOR, to_ticks=1)
        with self.assertRaisesRegex(HistoryError, "query_limit"):
            recorder.query_history(ACTOR, to_ticks=1)
        recorder.close_query(q["cursor"])
        self.assertIsNotNone(recorder.interaction("started", facts(3), 3, "native"))
        self.assertEqual(recorder.status()["state"], "recording")
        recorder.index.snapshot_byte_limit = 1
        with self.assertRaisesRegex(HistoryError, "query_budget"):
            recorder.query_history("sim:999")

    def test_recording_memory_budget_rejects_before_event_or_index_publication(self):
        recorder = Recorder(MemoryJournal(), "small", memory_bytes=1)
        self.assertIsNone(recorder.interaction("started", facts(), 1, "native"))
        self.assertEqual(recorder.status()["state"], "failed")
        self.assertFalse(recorder.events)
        self.assertFalse(recorder.index.entities)

    def test_journal_rejection_has_no_ghost_references(self):
        def reject(_):
            raise StorageError("rejected")
        self.journal.append = reject
        self.assertIsNone(self.add(1, 1))
        self.assertFalse(self.recorder.index.entities)
        self.assertFalse(self.recorder.events)



class ResourceAndRuntimeChecks(unittest.TestCase):
    def test_console_registration_and_filter_dispatch_without_game(self):
        registered, output, calls = {}, [], []
        commands = ModuleType("sims4.commands")
        commands.CommandType = SimpleNamespace(Live=1)
        def command(name, **kwargs):
            def decorate(function):
                registered[name] = function
                return function
            return decorate
        commands.Command = command
        commands.CheatOutput = lambda connection: output.append
        sims4 = ModuleType("sims4")
        sims4.commands = commands
        services = ModuleType("services")
        services.current_zone = lambda: None
        zone = ModuleType("zone")
        class Zone:
            def on_loading_screen_animation_finished(self):
                pass
            def on_teardown(self):
                pass
        zone.Zone = Zone
        def query(kind, identifier, **filters):
            calls.append((kind, identifier, filters))
            return {"ok": True}
        runtime = SimpleNamespace(history_query=query, history_next=lambda cursor: {"cursor": cursor},
                                  history_close=lambda cursor: {"closed": True}, inspector_error=None,
                                  inspector=SimpleNamespace(open=lambda kind, identifier: calls.append((kind, identifier))))
        with patch.dict(sys.modules, {"services": services, "sims4": sims4, "sims4.commands": commands, "zone": zone}), \
                patch.object(game_runtime, "log"), patch.object(game_runtime, "_runtime", runtime), \
                patch.object(game_runtime, "_lifecycle_hooks", None):
            game_runtime.initialize()
            registered["co.history_query"]("sim", "42", 25, False, "ended", "100", "200", "interaction", "all", "completed", "13433", "asc")
            self.assertEqual(calls[0][2]["event_types"], ["interaction"])
            self.assertEqual(calls[0][2]["from_ticks"], "100")
            self.assertEqual(calls[0][2]["fields"], None)
            registered["co.history_next"]("test-cursor")
            registered["co.history_close"]("test-cursor")
            self.assertTrue(json.loads(output[-1])["closed"])
            registered["co.inspect"]("object", "123")
            self.assertEqual(calls[-1], ("object", "123"))
            self.assertIn("inspector opened", output[-1])
            game_runtime._lifecycle_hooks.remove()

    def test_run_output_limit_retains_failed_write_and_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory, max_bytes=1)
            try:
                with self.assertRaisesRegex(StorageError, "output byte budget"):
                    journal.append({"data": "event"})
                self.assertEqual(journal.status()["accepted_sequence"], 0)
                self.assertTrue(journal.status()["rejected_retained"])
            finally:
                journal.close(wait=True)

    def test_queue_byte_limit_is_separate_from_item_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory, capacity=100, queue_bytes=1)
            try:
                with self.assertRaisesRegex(StorageError, "queue byte budget"):
                    journal.append({"data": "event"})
                self.assertEqual(journal.status()["accepted_sequence"], 0)
            finally:
                journal.close(wait=True)

    def test_disk_reserve_failure_never_acknowledges_durability(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("context_overlay.storage.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
                journal = Journal(directory, reserve_bytes=MIB)
                journal.append({"data": "event"})
                journal._thread.join(2)
                self.assertIn("Disk reserve", journal.status()["error"])
                self.assertEqual(journal.status()["durable_sequence"], 0)
                journal.close(wait=True)

    def test_runtime_exports_retryable_snapshot_pages_and_closes_them(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Runtime.__new__(Runtime)
            runtime.writer = Journal(directory)
            runtime.recorder = Recorder(runtime.writer, "runtime")
            runtime.closed = False
            runtime.session_id = "runtime"
            runtime.provenance = {"test": True}
            runtime.config = {"semanticizer_enabled": True}
            runtime.adapter = SimpleNamespace(resolve=lambda *args: facts()["actor"])
            runtime.collector = Collector(runtime.adapter, runtime.recorder, provenance=runtime.provenance)
            try:
                for i in range(3):
                    runtime.recorder.interaction("started", facts(i + 1), i, "native")
                first = runtime.history_query(page_size=2)
                final = runtime.history_next(first["next_cursor"])
                runtime.writer.flush()
                packet = json.loads(Path(final["path"]).read_text(encoding="utf-8"))
                self.assertEqual(len(packet["history"]["events"]), 1)
                self.assertEqual(packet["history"]["total_matches"], 3)
                self.assertEqual(packet["target"], facts()["actor"])
                self.assertEqual(len(packet["rendered"]["history"]), 1)
                runtime.history_close(first["cursor"])
                self.assertEqual(runtime.recorder.index.status()["snapshots"], 0)
                status = runtime.writer.status()
                self.assertEqual(status["pending_bytes"], 0)
                self.assertEqual((status["pending_exports"], status["written_exports"]), (0, 2))
                self.assertEqual(status["accepted_output_bytes"], status["written_output_bytes"])
            finally:
                runtime.writer.close(wait=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
