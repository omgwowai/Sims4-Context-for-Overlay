"""Real Runtime/Journal lifecycle with small EA service doubles; no game launch."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from support import Adapter, facts
from context_overlay import api, game_runtime
from context_overlay.api_probe import run, verify, inspect_target
from context_overlay.autonomy_capture import AutonomyCapture
from context_overlay.event_sources import EventSources
from context_overlay.history import HistoryError


class TravelHistoryChecks(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.zone = "101"
        self.now = 100
        self.manager = SimpleNamespace(is_traveling=False)
        self.game_services = ModuleType("game_services")
        self.game_services.service_manager = self.manager
        self.events = SimpleNamespace(register_single_event=Mock(), unregister=Mock())
        adapter = Adapter()
        adapter.scope = lambda: {"kind": "active_lot_instantiated", "zone_id": self.zone, "lot_id": self.zone, "off_lot": "excluded"}
        adapter.clock = lambda: {"ticks": str(self.now), "display": "test time"}
        adapter.live_objects = lambda: []
        adapter.in_scope = lambda obj: False
        self.adapter = adapter

        def module(name, **values):
            result = ModuleType(name)
            result.__dict__.update(values)
            return result

        interaction = type("Interaction", (), {"on_added_to_queue": lambda self: None, "_exited_pipeline": lambda self: None})
        state = type("State", (), {"_trigger_on_state_changed": lambda self: None})
        modules = {
            "game_services": self.game_services,
            "services": module("services", get_event_manager=lambda: self.events),
            "sims4.common": module("sims4.common", get_available_packs=lambda: []),
            "alarms": module("alarms", add_alarm_real_time=lambda *args, **kwargs: object(), cancel_alarm=Mock()),
            "clock": module("clock", interval_in_real_seconds=lambda seconds: seconds),
            "event_testing.test_events": module("event_testing.test_events", TestEvent=SimpleNamespace(**{
                name: i for i, name in enumerate(("InteractionStart", "InteractionExitedPipeline", "BuffBeganEvent",
                                                  "BuffEndedEvent", "AddRelationshipBit", "RemoveRelationshipBit"))})),
            "interactions.base.interaction": module("interactions.base.interaction", Interaction=interaction),
            "objects.components.state": module("objects.components.state", StateComponent=state),
        }
        config = dict(game_runtime.DEFAULTS, inspector_enabled=False, disk_reserve_mb=1, history_capacity=100)
        for context in (
            patch.dict(sys.modules, modules), patch.object(game_runtime, "_runtime", None),
            patch.object(game_runtime, "_startup_error", None), patch.object(game_runtime, "_retired", []),
            patch.object(game_runtime, "data_root", return_value=self.directory), patch.object(game_runtime, "log"),
            patch.object(game_runtime, "load_config", return_value=config),
            patch.object(game_runtime, "EAAdapter", return_value=adapter),
            patch.object(game_runtime.pkgutil, "get_data", return_value=b"{}"),
            patch.object(EventSources, "install"), patch.object(AutonomyCapture, "install"),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.addCleanup(self.close_runtime)
        self.assertTrue(game_runtime.start())
        self.assertEqual(game_runtime._runtime.recorder.status()["state"], "recording", game_runtime._runtime.recorder.error)

    def close_runtime(self):
        if game_runtime._runtime:
            game_runtime._runtime.stop("test_cleanup")

    def travel(self, zone="202"):
        previous = game_runtime._runtime
        self.manager.is_traveling = True
        game_runtime.stop()
        self.assertTrue(previous.history_suspended)
        self.assertFalse(api.get_status()["ready"])
        with self.assertRaises(api.APIError) as caught:
            api.append_event("test", {}, expected_session_id=previous.session_id)
        self.assertEqual(caught.exception.code, "session_closed")
        # EA stops zone services during travel, but keeps its game manager.
        game_runtime.stop_game_services()
        self.assertTrue(previous.writer._thread.is_alive())
        self.zone, self.now = zone, self.now + 100
        # The travel flag may have cleared before the loading-screen callback.
        self.manager.is_traveling = False
        self.assertTrue(game_runtime.start(resume_travel=True))
        return previous, game_runtime._runtime

    def test_travel_preserves_history_checkpoint_dedup_disk_and_scope(self):
        old = game_runtime._runtime
        target = facts()["actor"]
        old.recorder.enter(target, self.adapter.clock())
        action = old.recorder.interaction("started", facts(1), self.adapter.clock(), "native")
        path = self.directory / "probe.json"
        self.assertTrue(run(path)["passed"])
        old.writer.flush()
        self.assertTrue(verify(path)["passed"])
        sid, writer, recorder = old.session_id, old.writer, old.recorder
        frozen = api.query_history(None, None, page_size=1, order="asc", expected_session_id=sid)["history"]
        previous, new = self.travel()
        self.assertIs(new.writer, writer)
        self.assertIs(new.recorder, recorder)
        self.assertFalse(previous.history_suspended)
        self.assertEqual(new.session_id, sid)
        result = verify(path)
        self.assertEqual(result["check"], "travel_history")
        self.assertTrue(result["passed"], result)
        self.assertEqual(inspect_target(path), target)
        self.assertFalse(recorder.history(target["key"])["target_observation"]["currently_observed"])
        self.assertEqual(recorder.status()["zone_visit"], 2)
        self.assertEqual(api.get_history_page(frozen["next_cursor"], expected_session_id=sid)["history"]["offset"], 1)
        api.close_history(frozen["cursor"], expected_session_id=sid)
        new_action = recorder.interaction("started", facts(1), self.adapter.clock(), "native")
        self.assertNotEqual(action["event_id"], new_action["event_id"])
        self.assertEqual(recorder.events[action["event_id"]]["revision"], 1)
        self.assertEqual(new_action["observation_scope"]["zone_id"], "202")
        self.assertEqual(action["observation_scope"]["zone_id"], "101")
        with patch.object(self.adapter, "resolve", side_effect=ValueError("entity on previous lot")):
            page = api.query_history("sim", target["id"], expected_session_id=sid)["history"]
            self.assertEqual(page["total_matches"], 3)  # Two game actions and one linked external event.
            api.close_history(page["cursor"], expected_session_id=sid)
            with self.assertRaises(api.APIError) as caught:
                api.get_context("sim", target["id"], expected_session_id=sid)
            self.assertEqual(caught.exception.code, "target_unavailable")
        self.travel("101")
        self.assertTrue(verify(path)["passed"])
        current = game_runtime._runtime
        current.stop("finished")
        self.assertFalse(writer._thread.is_alive())
        records = [json.loads(line) for line in writer.path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["sequence"] for r in records], list(range(1, len(records) + 1)))
        self.assertEqual(len({r["session_id"] for r in records}), 1)
        self.assertEqual([r["category"] for r in records if r.get("category") in ("session_start", "zone_entry", "zone_exit", "session_end")],
                         ["session_start", "zone_exit", "zone_entry", "zone_exit", "zone_entry", "session_end"])

    def test_reload_even_same_lot_starts_fresh_and_rejects_old_checkpoint(self):
        path = self.directory / "probe.json"
        run(path)
        old = game_runtime._runtime
        game_runtime.stop()
        self.assertFalse(old.writer._thread.is_alive())
        self.assertTrue(game_runtime.start(resume_travel=True))
        self.assertNotEqual(game_runtime._runtime.session_id, old.session_id)
        self.assertTrue(verify(path)["passed"])
        self.assertEqual(game_runtime._runtime.recorder.status()["retained_events"], 0)

    def test_replaced_game_manager_cannot_resume_abandoned_travel(self):
        old = game_runtime._runtime
        self.manager.is_traveling = True
        game_runtime.stop()
        self.game_services.service_manager = SimpleNamespace(is_traveling=False)
        self.assertTrue(game_runtime.start(resume_travel=True))
        self.assertNotEqual(game_runtime._runtime.session_id, old.session_id)
        self.assertFalse(old.writer._thread.is_alive())

    def test_manual_restart_discards_even_a_suspended_session(self):
        old = game_runtime._runtime
        self.manager.is_traveling = True
        game_runtime.stop()
        self.assertTrue(game_runtime.start())
        self.assertNotEqual(game_runtime._runtime.session_id, old.session_id)
        self.assertFalse(old.writer._thread.is_alive())

    def test_game_service_shutdown_closes_abandoned_travel(self):
        old = game_runtime._runtime
        self.manager.is_traveling = True
        game_runtime.stop()
        self.manager.is_traveling = False
        game_runtime.stop_game_services()
        self.assertFalse(old.history_suspended)
        self.assertFalse(old.writer._thread.is_alive())

    def test_query_can_be_released_during_travel(self):
        old = game_runtime._runtime
        page = api.query_history(None, None, expected_session_id=old.session_id)["history"]
        self.manager.is_traveling = True
        game_runtime.stop()
        self.assertTrue(api.close_history(page["cursor"], expected_session_id=old.session_id)["released"])
        self.assertEqual(old.recorder.index.status()["snapshots"], 0)

    def test_resume_install_failure_drains_shared_writer(self):
        old = game_runtime._runtime
        self.manager.is_traveling = True
        game_runtime.stop()
        with patch.object(game_runtime.Runtime, "install", side_effect=ValueError("cannot register")):
            self.assertFalse(game_runtime.start(resume_travel=True))
        self.assertFalse(old.writer._thread.is_alive())
        self.assertFalse(old.history_suspended)
        self.assertEqual(api.get_status()["state"], "startup_failed")

    def test_travel_cleanup_failure_closes_writer_and_prevents_resume(self):
        old = game_runtime._runtime
        self.manager.is_traveling = True
        self.events.unregister.side_effect = ValueError("cannot unregister")
        game_runtime.stop()
        self.assertFalse(old.history_suspended)
        self.assertFalse(old.writer._thread.is_alive())
        self.assertIn("cannot unregister", old.recorder.error)

    def test_travel_preserves_fifo_loss_detection_and_snapshot_ttl(self):
        rec = game_runtime._runtime.recorder
        clock = [0]
        rec.index._clock = lambda: clock[0]
        checkpoint = api.read_event_changes(start="now", expected_session_id=rec.session_id)["history"]
        api.close_history(checkpoint["cursor"], expected_session_id=rec.session_id)
        rec.index.capacity = 2
        for i in range(3):
            rec.interaction("started", facts(i), self.adapter.clock(), "native")
        page = api.query_history(None, None, expected_session_id=rec.session_id)["history"]
        self.travel()
        self.assertEqual(rec.status()["evicted_events"], 1)
        with self.assertRaises(api.APIError) as caught:
            api.read_event_changes(checkpoint["checkpoint"], expected_session_id=rec.session_id)
        self.assertEqual(caught.exception.code, "history_gap")
        clock[0] = 121
        with self.assertRaises(HistoryError) as caught:
            rec.history_page(page["cursor"])
        self.assertEqual(caught.exception.code, "cursor_expired")


if __name__ == "__main__":
    unittest.main()
