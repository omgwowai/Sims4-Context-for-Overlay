"""Check the documented consumer against real Collector/Recorder contracts."""

import importlib.util
import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from context_overlay import game_runtime
from context_overlay.collector import Collector
from context_overlay.model import entity, field
from context_overlay.recorder import Recorder
from test_core import MemoryJournal, facts

spec = importlib.util.spec_from_file_location("example_consumer", str(ROOT / "examples/mod_consumer.py"))
consumer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consumer)


class ExampleChecks(unittest.TestCase):
    def setUp(self):
        class Adapter:
            def resolve(self, kind, identifier):
                return facts()["actor"] if kind == "sim" else entity("object", identifier)
            def scope(self):
                return {"kind": "active_lot_instantiated", "off_lot": "excluded"}
            def clock(self):
                return {"ticks": "100", "display": "test"}
            def read(self, target, name):
                return field(target if name == "identity" else None)
        recorder = Recorder(MemoryJournal(), session_id="example-run")
        adapter = Adapter()
        self.runtime = SimpleNamespace(closed=False, session_id="example-run", adapter=adapter,
                                       recorder=recorder, collector=Collector(adapter, recorder),
                                       provenance={}, api_ready=True, simulation_thread_id=threading.get_ident())
        self.runtime_patch = patch.object(game_runtime, "_runtime", self.runtime)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)
        error_patch = patch.object(game_runtime, "_startup_error", None)
        error_patch.start()
        self.addCleanup(error_patch.stop)

    def test_capture_pagination_and_release(self):
        for index in range(3):
            self.runtime.recorder.interaction("started", facts(index + 1), index + 10, "test")
        packet = consumer.capture_context()
        self.assertEqual(len(packet["history"]["events"]), 3)
        self.assertEqual(packet["schema_version"], "1")
        json.dumps(packet)
        handle = consumer.open_history(page_size=2, event_types=["interaction"])
        page = handle["page"]
        self.assertEqual(len(page["events"]), 2)
        self.assertTrue(page["has_more"])
        final = consumer.next_history(handle["session_id"], page["next_cursor"])
        self.assertEqual(len(final["events"]), 1)
        consumer.close_history(handle["session_id"], final["cursor"])
        self.assertEqual(self.runtime.recorder.index.status()["snapshots"], 0)

    def test_changed_or_unready_runtime_rejects_stale_requests(self):
        handle = consumer.open_history()
        self.runtime.session_id = "new-run"
        with self.assertRaisesRegex(RuntimeError, "session_changed"):
            consumer.next_history(handle["session_id"], handle["page"]["cursor"])
        self.runtime.closed = True
        with self.assertRaisesRegex(RuntimeError, "not ready"):
            consumer.capture_context()
