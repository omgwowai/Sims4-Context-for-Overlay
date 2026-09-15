"""Behavioral checks for the new implementation, independent of EA imports."""

import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from context_overlay.collector import Collector
from context_overlay.hooks import Hooks
from context_overlay.model import entity, field, outcome
from context_overlay.recorder import Recorder
from context_overlay.semanticizer import translate, display
from context_overlay.storage import Journal, StorageError, replay
from context_overlay.test_driver import Driver
from context_overlay import game_runtime
from context_overlay.profiles import resource_name


class MemoryJournal:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(copy.deepcopy(record))
        return len(self.records)

    def status(self):
        return {"error": None, "durable_sequence": len(self.records)}


def facts(interaction_id=10, target_id=123, main=True):
    return {"actor": entity("sim", 18446744073709550001, "阿明"),
            "target": entity("object", target_id, "食物", 888),
            "interaction_id": str(interaction_id), "tuning_id": "321",
            "name": "吃饭", "tier": "main" if main else "internal",
            "trigger": {"name": "SCRIPT_WITH_USER_INTENT", "value": 10}}


class RecorderChecks(unittest.TestCase):
    def setUp(self):
        self.journal = MemoryJournal()
        self.recorder = Recorder(self.journal, session_id="run-a")

    def test_duplicate_delivery_and_distinct_actions(self):
        first = self.recorder.interaction("started", facts(), 100, "native")
        self.recorder.interaction("started", facts(), 100, "native")
        second = self.recorder.interaction("started", facts(11), 101, "native")
        self.assertEqual(len(self.journal.records), 2)
        self.assertNotEqual(first["event_id"], second["event_id"])

    def test_completion_requires_exit_and_natural_finisher(self):
        self.assertEqual(outcome("NATURAL", False), "unknown")
        started = self.recorder.interaction("started", facts(), 100, "native")
        cancelled = dict(facts(), finishing_type="USER_CANCEL")
        end = self.recorder.interaction("exited", cancelled, 120, "native")
        self.assertEqual(end["outcome"], "cancelled")
        self.assertEqual(end["started_time"], 100)
        self.assertEqual(started["revision"], 1)
        self.assertEqual(end["revision"], 2)

    def test_end_only_does_not_fabricate_start(self):
        end = self.recorder.interaction("exited", dict(facts(), finishing_type="NATURAL"), 120, "native")
        self.assertIsNone(end["started_time"])
        self.assertEqual(end["outcome"], "completed")

    def test_late_nonterminal_observation_preserves_terminal_evidence(self):
        self.recorder.interaction("exited", dict(facts(), finishing_type="NATURAL"), 120, "native")
        late = self.recorder.interaction("started", facts(), 121, "late_source")
        self.assertEqual(late["stage"], "ended")
        self.assertEqual(late["outcome"], "completed")
        self.assertEqual(late["facts"]["finishing_type"], "NATURAL")
        self.assertIsNone(late["started_time"])

    def test_same_definition_different_instances_and_internal_visibility(self):
        self.recorder.interaction("started", facts(1, 111), 10, "native")
        self.recorder.interaction("started", facts(2, 222), 10, "native")
        self.recorder.interaction("started", facts(3, 111, False), 10, "native")
        self.assertEqual(len(self.recorder.history("object:111")["events"]), 1)
        self.assertEqual(len(self.recorder.history("object:111", include_internal=True)["events"]), 2)
        self.assertEqual(len(self.recorder.history("object:222")["events"]), 1)

    def test_capacity_evicts_oldest_and_continues_recording(self):
        recorder = Recorder(self.journal, capacity=1)
        oldest = recorder.interaction("started", facts(1), 1, "native")
        newest = recorder.interaction("started", facts(2), 2, "native")
        self.assertIsNotNone(newest)
        self.assertEqual(recorder.status()["state"], "recording")
        self.assertEqual(recorder.status()["evicted_events"], 1)
        self.assertNotIn(oldest["event_id"], recorder.events)
        self.assertIsNone(recorder.interaction("exited", facts(1), 3, "native"))
        self.assertEqual(len(recorder.events), 1)

    def test_event_values_are_queryable_from_both_participants(self):
        actor = facts()["actor"]
        other = entity("sim", 22, "阿青")
        event = self.recorder.change([actor, other], "relationships.friendship", 10, 20, 20, "test_notification")
        a = self.recorder.history(actor["key"])["events"]
        b = self.recorder.history(other["key"])["events"]
        self.assertEqual(a[0]["event_id"], b[0]["event_id"])
        self.assertEqual(a[0]["field"], "relationships.friendship")
        self.assertEqual((event["before"], event["after"]), (10, 20))
        self.assertEqual(event["evidence_type"], "notification")
        self.assertEqual(event["last_observed_time"], 20)
        self.assertIsNone(self.recorder.change([actor, other], "relationships.friendship", 20, 20, 21, "test_notification"))
        self.assertEqual(len(self.recorder.events), 1)

    def test_scope_reentry_keeps_observation_boundaries_without_creating_events(self):
        other = entity("sim", 22, "阿青")
        self.recorder.enter(other, 9)
        self.recorder.leave(other, 11)
        self.assertFalse(self.recorder.history(other["key"])["target_observation"]["currently_observed"])
        self.recorder.enter(other, 19)
        self.assertEqual(len(self.recorder.events), 0)
        history = self.recorder.history(other["key"])
        self.assertEqual(history["target_observation"]["last_exit"], 11)
        self.assertEqual(history["target_observation"]["entry_count"], 2)

    def test_sessions_do_not_merge(self):
        a = self.recorder.interaction("started", facts(), 1, "native")
        other = Recorder(MemoryJournal(), session_id="run-b")
        b = other.interaction("started", facts(), 1, "native")
        self.assertNotEqual(a["event_id"], b["event_id"])
        self.assertEqual(a["facts"]["actor"]["id"], "18446744073709550001")

    def test_immediate_picker_is_triggered_with_unconfirmed_result(self):
        event = self.recorder.interaction("started", dict(facts(), immediate=True), 1, "native")
        self.assertEqual((event["stage"], event["outcome"]), ("triggered", "unknown"))
        packet = {"history": self.recorder.history(facts()["actor"]["key"])}
        self.assertIn("后续结果未确认", translate(packet)["rendered"]["history"][0]["text"])

    def test_bidirectional_bit_notifications_share_event_but_transitions_do_not(self):
        a, b, bit = entity("sim", 1), entity("sim", 2), {"id": "15797"}
        first = self.recorder.relationship_bit(a, b, bit, True, 1, "native", True)
        duplicate = self.recorder.relationship_bit(b, a, bit, True, 1, "native", True)
        self.assertEqual(first["event_id"], duplicate["event_id"])
        removed = self.recorder.relationship_bit(a, b, bit, False, 2, "native", True)
        again = self.recorder.relationship_bit(b, a, bit, True, 3, "native", True)
        self.assertEqual(len({x["event_id"] for x in (first, removed, again)}), 3)
        self.assertEqual(self.recorder.history(a["key"])["events"], self.recorder.history(b["key"])["events"])
        self.recorder.leave(b, 4)
        after_gap = self.recorder.relationship_bit(a, b, bit, True, 5, "native", True)
        self.assertNotEqual(again["event_id"], after_gap["event_id"])

    def test_directional_bits_keep_their_owner(self):
        a, b, bit = entity("sim", 1), entity("sim", 2), {"id": "42"}
        first = self.recorder.relationship_bit(a, b, bit, True, 1, "native", False)
        reverse = self.recorder.relationship_bit(b, a, bit, True, 1, "native", False)
        self.assertNotEqual(first["event_id"], reverse["event_id"])
        self.assertEqual(first["metadata"]["reporting_actor"], a)


class PersistenceChecks(unittest.TestCase):
    def test_replay_atomic_export_and_duplicate_record(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            recorder = Recorder(journal, session_id="run-a")
            recorder.interaction("started", facts(), 1, "native")
            recorder.interaction("exited", dict(facts(), finishing_type="USER_CANCEL"), 2, "native")
            output = journal.export({"request_id": "a" * 32, "id": facts()["actor"]["id"]})
            journal.flush()
            journal.close(wait=True)
            replayed = replay(journal.path)
            self.assertTrue(replayed["complete"])
            self.assertEqual(len(replayed["events"]), 1)
            self.assertEqual(replayed["events"][0]["outcome"], "cancelled")
            self.assertEqual(json.loads(Path(output).read_text(encoding="utf-8"))["id"], facts()["actor"]["id"])
            lines = journal.path.read_text(encoding="utf-8").splitlines()
            with journal.path.open("a", encoding="utf-8") as stream:
                stream.write(lines[-1] + "\n")
            self.assertEqual(len(replay(journal.path)["events"]), 1)
            with journal.path.open("a", encoding="utf-8") as stream:
                stream.write('{"sequence":')
            damaged = replay(journal.path)
            self.assertFalse(damaged["complete"])
            self.assertEqual(damaged["errors"][0]["line"], 4)

    def test_write_failure_is_not_acknowledged(self):
        def fail_open(*args, **kwargs):
            raise OSError("simulated unavailable disk")
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory, opener=fail_open)
            journal._thread.join(2)
            with self.assertRaises(StorageError):
                journal.append({"test": True})
            self.assertEqual(journal.status()["durable_sequence"], 0)
            self.assertEqual(journal.status()["state"], "failed")
            journal.close(wait=True)

    def test_partial_utf8_tail_is_reported_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.jsonl"
            path.write_bytes(b'{"sequence":1,"session_id":"a","kind":"observation"}\n' + b'\xe4\xb8')
            result = replay(path)
            self.assertFalse(result["complete"])
            self.assertEqual(result["errors"][0]["line"], 2)

    def test_failed_sync_keeps_inflight_record_unacknowledged(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory)
            def fail_sync(stream):
                stream.flush()
                raise OSError("simulated fsync failure")
            journal._sync = fail_sync
            journal.append({"sequence": 1, "session_id": "a", "kind": "observation"})
            journal._thread.join(2)
            status = journal.status()
            self.assertEqual(status["accepted_sequence"], 1)
            self.assertEqual(status["durable_sequence"], 0)
            self.assertTrue(status["inflight"])
            self.assertEqual(status["state"], "failed")
            journal.close(wait=True)

    def test_queue_overload_retains_unwritten_records(self):
        entered, release = threading.Event(), threading.Event()
        def delayed_open(*args, **kwargs):
            entered.set()
            release.wait(2)
            return open(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory, capacity=1, opener=delayed_open)
            entered.wait(2)
            try:
                journal.append({"a": 1})
                with self.assertRaises(StorageError):
                    journal.append({"a": 2})
                self.assertTrue(journal.status()["rejected_retained"])
                self.assertEqual(journal.status()["queued"], 1)
                self.assertEqual(journal.status()["durable_sequence"], 0)
            finally:
                release.set()
                journal.close(wait=True)


class LifecycleChecks(unittest.TestCase):
    def test_cleanup_drains_writer_even_if_one_unsubscribe_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = game_runtime.Runtime.__new__(game_runtime.Runtime)
            runtime.writer = Journal(directory)
            runtime.recorder = Recorder(runtime.writer, session_id="cleanup")
            runtime.session_id = "cleanup"
            runtime.adapter = SimpleNamespace(clock=lambda: {"ticks": "7"})
            runtime.sources = SimpleNamespace(status=lambda: {"source": {"state": "installed"}},
                diagnostics=lambda: {"callbacks": {"source": 3}, "suppressed_statistics": {"timer": 2}})
            runtime.closed = False
            runtime.alarm = None
            closed_views = []
            runtime.inspector = SimpleNamespace(close=lambda: closed_views.append(True))
            class Owner:
                def call(self):
                    return 1
            original = Owner.call
            runtime.hooks = Hooks(runtime.fail)
            runtime.hooks.after(Owner, "call", lambda *args: None)
            attempted = []
            def unregister(handler, events):
                attempted.extend(events)
                if events == (1,):
                    raise RuntimeError("first subscription failed")
            runtime.manager = SimpleNamespace(unregister=unregister)
            runtime.events = [1, 2]
            runtime.recorder.note("test_before_shutdown", {})
            with patch.object(game_runtime, "log"), patch.object(game_runtime, "_retired", []):
                runtime.stop("test")
                runtime.stop("repeat")
            self.assertTrue(runtime.closed)
            self.assertEqual(attempted, [1, 2])
            self.assertEqual(closed_views, [True])
            self.assertIs(Owner.call, original)
            self.assertFalse(runtime.writer._thread.is_alive())
            self.assertEqual(runtime.writer.status()["durable_sequence"], 2)
            self.assertIn("first subscription failed", runtime.recorder.error)
            records = replay(runtime.writer.path)
            self.assertEqual(records["observations"][-1]["category"], "session_end")
            self.assertEqual(records["observations"][-1]["data"]["event_diagnostics"]["callbacks"]["source"], 3)
            self.assertEqual(records["observations"][-1]["data"]["event_coverage"]["source"]["state"], "installed")

    def test_poll_stops_immediately_when_driver_restarts_its_run(self):
        runtime = game_runtime.Runtime.__new__(game_runtime.Runtime)
        runtime.closed = False
        runtime.poll_count = 0
        runtime.poll_max_ms = 0
        runtime.driver = SimpleNamespace(poll=lambda: setattr(runtime, "closed", True))
        with patch.object(game_runtime, "_retired", []):
            runtime.poll(None)
        self.assertTrue(runtime.closed)
        self.assertEqual(runtime.poll_count, 1)

    def test_failed_install_is_cleaned_up_and_does_not_report_success(self):
        calls = []
        class FailedRuntime:
            closed = False
            def install(self):
                raise RuntimeError("native subscription unavailable")
            def fail(self, error):
                calls.append(error)
            def stop(self, reason):
                self.closed = True
                calls.append(reason)
        with patch.object(game_runtime, "Runtime", FailedRuntime), patch.object(game_runtime, "log"), \
                patch.object(game_runtime, "_runtime", None), patch.object(game_runtime, "_startup_error", None):
            self.assertFalse(game_runtime.start())
            self.assertTrue(game_runtime._runtime.closed)
            self.assertIn("native subscription unavailable", game_runtime._startup_error)
        self.assertEqual(calls[-1], "startup_failure")


class CompositionChecks(unittest.TestCase):
    def test_nested_absence_and_object_state_names_remain_distinguishable(self):
        self.assertIn("未实例化", display(field(status="not_present", reason="No track")))
        self.assertIn("读取失败", display(field(status="error", reason="Failed")))
        self.assertEqual(display({"state": {"name": "新鲜度"}, "value": {"name": "已变质"}}), "新鲜度：已变质")

    def test_resource_alias_requires_matching_id_and_name_and_preserves_raw(self):
        raw = {"text": "DirtyState_Clean", "status": "unmapped"}
        named = resource_name("15130", "DirtyState_Clean", raw)
        self.assertEqual(named["text"], "干净")
        self.assertEqual(named["raw_name"], raw)
        self.assertIs(resource_name("15130", "ChangedByUpdate", raw), raw)
        self.assertIs(resource_name("999", "DirtyState_Clean", raw), raw)
        resolved = {"text": "本地化原文", "status": "resolved"}
        self.assertIs(resource_name("15130", "DirtyState_Clean", resolved), resolved)

    def test_validation_catalog_uses_shared_ea_state_manager(self):
        resources = ModuleType("sims4.resources")
        resources.Types = SimpleNamespace(OBJECT_STATE=123, INTERACTION=456)
        sims4 = ModuleType("sims4")
        sims4.resources = resources
        resource = type("BrokenState_Unbroken", (), {})
        requested = []
        def manager(kind):
            requested.append(kind)
            return SimpleNamespace(types={1: resource})
        runtime = SimpleNamespace(adapter=SimpleNamespace(
            services=SimpleNamespace(get_instance_manager=manager),
            resource=lambda value: value.__name__))
        driver = Driver.__new__(Driver)
        driver.runtime = runtime
        with patch.dict(sys.modules, {"sims4": sims4, "sims4.resources": resources}):
            result = driver.execute({"operation": "catalog", "type": "OBJECT_STATE_VALUE", "match": "broken"})
            driver.execute({"operation": "catalog", "type": "INTERACTION"})
        self.assertEqual(result, ["BrokenState_Unbroken"])
        self.assertEqual(requested, [123, 456])

    def test_validation_operation_is_not_repeated_after_run_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            class Runtime:
                def __init__(self):
                    self.directory = Path(directory) / "runs" / "first"
                    self.session_id = "first"
                def status(self):
                    return {"state": "test"}
            runtime = Runtime()
            runtime.directory.mkdir(parents=True)
            calls = []
            class RestartDriver(Driver):
                def execute(self, request):
                    calls.append(request["request_id"])
                    restarted = RestartDriver(runtime)
                    restarted.poll()
                    return {"restarted": True}
            driver = RestartDriver(runtime)
            (driver.directory / "request.json").write_text(json.dumps({"request_id": "f" * 32, "operation": "restart"}), encoding="utf-8")
            driver.poll()
            self.assertEqual(calls, ["f" * 32])

    def test_pure_translation_preserves_evidence_and_unknown(self):
        recorder = Recorder(MemoryJournal())
        recorder.interaction("exited", dict(facts(), finishing_type="UNKNOWN"), 1, "native")
        packet = {"snapshot": {"needs": field(status="error", reason="read failed")},
                  "history": recorder.history(facts()["actor"]["key"])}
        original = copy.deepcopy(packet)
        rendered = translate(packet)
        self.assertEqual(packet, original)
        self.assertIn("结果未确认", rendered["rendered"]["history"][0]["text"])
        self.assertIn("read failed", rendered["rendered"]["current"][0]["text"])
        self.assertEqual(rendered, translate(packet))

    def test_collector_pins_target_and_survives_disabled_history(self):
        class Adapter:
            def resolve(self, kind, identifier):
                return entity(kind, identifier)
            def scope(self):
                return {"kind": "active_lot"}
            def clock(self):
                return 123
            def read(self, target, name):
                return field(target["id"], source="test")
        recorder = Recorder(MemoryJournal(), enabled=False)
        collector = Collector(Adapter(), recorder)
        packet = collector.collect("sim", 2, fields=["identity"])
        self.assertEqual(packet["snapshot"]["identity"]["value"], "2")
        self.assertEqual(packet["history"]["status"], "disabled")
        self.assertEqual(packet["status"], "partial")
        with self.assertRaises(ValueError):
            collector.collect("sim", 2, fields=["__dict__"])

    def test_hooks_preserve_return_exception_and_other_owners(self):
        errors = []
        class Owner:
            def call(self, value):
                if value < 0:
                    raise LookupError("EA error")
                return value * 2
        original = Owner.call
        hooks = Hooks(errors.append)
        def fail(args, kwargs, result):
            raise RuntimeError("collector error")
        hooks.after(Owner, "call", fail)
        self.assertEqual(Owner().call(4), 8)
        self.assertEqual(len(errors), 1)
        with self.assertRaisesRegex(LookupError, "EA error"):
            Owner().call(-1)
        wrapper = Owner.call
        def another_mod(self, value):
            return wrapper(self, value) + 1
        Owner.call = another_mod
        hooks.remove()
        self.assertIs(Owner.call, another_mod)
        self.assertEqual(Owner().call(4), 9)
        self.assertEqual(len(errors), 1)
        Owner.call = original

    def test_failed_error_logger_does_not_break_game_call(self):
        class Owner:
            def call(self):
                return "original-result"
        def fail(*args):
            raise OSError("diagnostic sink unavailable")
        hooks = Hooks(fail)
        hooks.after(Owner, "call", fail)
        self.assertEqual(Owner().call(), "original-result")
        hooks.remove()


if __name__ == "__main__":
    unittest.main(verbosity=2)
