"""Scope polling stays separate from current-value reads and event recording."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_core import MemoryJournal
from context_overlay import game_runtime
from context_overlay.collector import Collector
from context_overlay.ea_adapter import EAAdapter, NEEDS, RELATIONSHIP_TRACKS
from context_overlay.recorder import Recorder


class RuntimeObservationChecks(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.values = {name: 50 for name in NEEDS.values()}
        self.values.update({name: 10 for name in RELATIONSHIP_TRACKS.values()})

        def statistic(name, add):
            self.assertFalse(add)
            return SimpleNamespace(get_value=lambda: self.values[name])

        self.need_reads = Mock(side_effect=statistic)
        self.relationship_reads = Mock(side_effect=lambda other, track, add: statistic(track, add))
        tracker = SimpleNamespace(has_relationship=lambda other: other == 2,
                                  get_relationship_track=self.relationship_reads, get_all_bits=lambda other: [])
        self.sims = [SimpleNamespace(is_sim=True, id=sid, si_state=[], queue=[],
            sim_info=SimpleNamespace(sim_id=sid, first_name="Sim", last_name=str(sid),
                commodity_tracker=SimpleNamespace(get_statistic=self.need_reads), relationship_tracker=tracker))
            for sid in (1, 2)]
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.config = dict(game_runtime.DEFAULTS)
        adapter.statistics = {name: name for name in self.values}
        adapter.clock = lambda: {"ticks": str(self.now)}
        adapter.scope = lambda: {"kind": "active_lot_instantiated"}
        adapter.live_objects = lambda: self.sims
        adapter.in_scope = lambda obj: obj is not None
        adapter.object_for = lambda target: next(sim for sim in self.sims if str(sim.id) == target["id"])
        adapter.resource = lambda value, **kwargs: {"id": str(value.guid64)} if hasattr(value, "guid64") else {"name": value}
        self.journal = MemoryJournal()
        self.runtime = game_runtime.Runtime.__new__(game_runtime.Runtime)
        self.runtime.adapter = adapter
        self.runtime.config = adapter.config
        self.runtime.recorder = Recorder(self.journal, session_id="events-only")
        self.runtime.collector = Collector(adapter, self.runtime.recorder)
        self.runtime.closed = False
        self.runtime.driver = None
        self.runtime.known = {}
        self.runtime.poll_count = 0
        self.runtime.poll_max_ms = 0

    def test_background_ticks_leave_values_unread_and_history_unwritten(self):
        with patch.object(game_runtime, "_retired", []):
            self.runtime.poll(None)
            self.assertEqual([record["category"] for record in self.journal.records], ["scope_entry", "scope_entry"])
            for tick in (1, 100000, 1000000000):
                self.now = tick
                self.values = {name: value - 5 for name, value in self.values.items()}
                self.runtime.poll(None)
        self.assertEqual(self.runtime.recorder.status()["state"], "recording")
        self.need_reads.assert_not_called()
        self.relationship_reads.assert_not_called()
        self.assertEqual(len(self.journal.records), 2)
        self.assertEqual(self.runtime.recorder.history("sim:1")["events"], [])

    def test_context_reads_live_needs_and_relationships_without_writing_history(self):
        first = self.runtime.collector.collect("sim", "1", fields=["needs", "relationships"])
        self.values[NEEDS["hunger"]] = 30
        self.values[RELATIONSHIP_TRACKS["friendship"]] = 25
        self.values[RELATIONSHIP_TRACKS["romance"]] = -5
        self.now = 100000
        second = self.runtime.collector.collect("sim", "1", fields=["needs", "relationships"])
        self.assertEqual(first["snapshot"]["needs"]["value"]["hunger"]["value"]["value"], 50)
        self.assertEqual(second["snapshot"]["needs"]["value"]["hunger"]["value"]["value"], 30)
        tracks = second["snapshot"]["relationships"]["value"][0]["tracks"]
        self.assertEqual((tracks["friendship"]["value"], tracks["romance"]["value"]), (25, -5))
        self.assertEqual(second["status"], "complete")
        self.assertEqual(second["history"]["events"], [])
        self.assertEqual(self.journal.records, [])

    def test_object_state_callback_records_observed_before_and_after(self):
        owner = SimpleNamespace(is_sim=False, id=33, definition=SimpleNamespace(id=333, name="Fridge"))
        state = SimpleNamespace(guid64=15079, __name__="BrokenState")
        old, new = SimpleNamespace(guid64=15081), SimpleNamespace(guid64=15080)
        self.now = 42
        self.runtime.state_changed((SimpleNamespace(owner=owner), state, old, new), {}, None)
        events = self.runtime.recorder.history("object:33")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0]["before"], events[0]["after"]), ({"id": "15081"}, {"id": "15080"}))
        self.assertEqual(events[0]["evidence_type"], "notification")
        self.assertEqual(events[0]["last_observed_time"], {"ticks": "42"})
        self.runtime.state_changed((SimpleNamespace(owner=owner), state, new, new), {}, None)
        self.assertEqual(len(self.journal.records), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
