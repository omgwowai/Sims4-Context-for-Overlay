"""Zone-wide observation with the real adapter, capture and Context entry points."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from support import runtime_fixture
from context_overlay import api
from context_overlay.ea_adapter import EAAdapter
from context_overlay.model import entity
from test_balloons import SlottedRequest


class WorldScopeChecks(unittest.TestCase):
    def setUp(self):
        self.zone = SimpleNamespace(id=42, is_zone_running=True, lot=SimpleNamespace(lot_id=84))
        self.objects = {}
        self.manager = SimpleNamespace(get=self.objects.get, valid_objects=lambda: list(self.objects.values()))
        self.adapter = EAAdapter.__new__(EAAdapter)
        self.adapter.services = SimpleNamespace(current_zone=lambda: self.zone, object_manager=lambda: self.manager)
        self.adapter.clock = lambda: {"ticks": "100"}
        self.adapter.sim_minutes_to_ticks = lambda minutes: int(minutes * 1500)
        self.adapter.reference = lambda obj: entity("sim" if obj.is_sim else "object", obj.id, "Entity " + str(obj.id))
        self.adapter.event_reference = self.adapter.reference
        self.runtime = runtime_fixture(self, self.adapter)
        self.runtime.recorder.begin_zone(self.adapter.scope())
        self.adapter.balloon_capture = self.runtime.balloons
        self.runtime.balloons.coverage["send"] = {"state": "installed"}
        self.runtime.balloons.type_names = {7: "THOUGHT"}
        self.addCleanup(self.runtime.balloons.close)

    def add(self, identifier, sim=True):
        obj = SimpleNamespace(id=identifier, is_sim=sim, zone_id=42, parent=None,
            _hidden_flags=0, is_in_inventory=lambda: False, is_on_active_lot=lambda: False,
            position=SimpleNamespace(x=10, y=0, z=20))
        obj.sim_info = SimpleNamespace(sim_id=identifier, get_sim_instance=lambda: obj)
        self.objects[identifier] = obj
        return obj

    def test_offlot_world_sims_and_objects_do_not_consult_lot_boundary(self):
        sim, obj = self.add(1), self.add(2, sim=False)
        with patch.object(sim, "is_on_active_lot", side_effect=AssertionError("scope must not test the lot")), \
                patch.object(obj, "is_on_active_lot", side_effect=AssertionError("scope must not test the lot")):
            self.assertTrue(self.adapter.in_scope(sim))
            self.assertTrue(self.adapter.in_scope(obj))
            self.assertEqual(self.adapter.local_sims(), [sim])
        self.assertEqual(self.adapter.scope()["off_lot"], "included")
        self.assertEqual(api.get_api_info()["scope"], "zone_instantiated")
        self.assertIn("context.zone_scope", api.get_api_info()["capabilities"])
        self.assertIn("events.zone_scope", api.get_api_info()["capabilities"])

    def test_rejects_hidden_unloaded_foreign_and_recycled_instances(self):
        sim = self.add(1)
        for attribute, invalid in (("_hidden_flags", 1), ("zone_id", 99)):
            before = getattr(sim, attribute)
            setattr(sim, attribute, invalid)
            self.assertFalse(self.adapter.in_scope(sim))
            setattr(sim, attribute, before)
        sim.sim_info.get_sim_instance = lambda: None
        self.assertFalse(self.adapter.in_scope(sim))
        sim.sim_info.get_sim_instance = lambda: sim
        self.add(1)  # same ID, different instance
        self.assertFalse(self.adapter.in_scope(sim))
        obj = self.add(2, sim=False)
        self.objects.pop(2)
        self.assertFalse(self.adapter.in_scope(obj))
        self.objects[2] = obj
        self.zone.is_zone_running = False
        self.assertFalse(self.adapter.in_scope(obj))
        self.zone = None
        self.assertFalse(self.adapter.in_scope(obj))

    def test_part_proxy_uses_live_owner_and_inventory_parent_is_excluded(self):
        owner = self.add(1, sim=False)
        part = SimpleNamespace(id=1, zone_id=42, is_sim=False, is_part=True, part_owner=owner,
            _hidden_flags=0, is_in_inventory=lambda: False, parent=owner)
        self.assertTrue(self.adapter.in_scope(part))
        bag = self.add(2, sim=False)
        bag.is_in_inventory = lambda: True
        owner.parent = bag
        self.assertFalse(self.adapter.in_scope(owner))
        self.assertFalse(self.adapter.in_scope(part))
        owner.parent = None
        self.objects.pop(1)
        self.assertFalse(self.adapter.in_scope(part))

    def test_context_reads_offlot_then_marks_despawn_without_writing_history(self):
        sim = self.add(1)
        self.adapter.resolve = lambda kind, identifier: self.adapter.reference(sim)
        self.adapter.object_for = lambda target: self.objects.get(int(target["id"]))
        packet = api.get_context(identifier="1", fields=["identity", "location", "balloons"], include_history=False)
        self.assertEqual(packet["status"], "complete")
        self.assertEqual(packet["scope"]["kind"], "zone_instantiated")
        self.assertEqual(packet["snapshot"]["location"]["value"]["position"]["x"], 10)
        self.objects.pop(1)
        packet = api.get_context(identifier="1", fields=["identity", "balloons"], include_history=False)
        self.assertEqual(packet["snapshot"]["identity"]["status"], "out_of_scope")
        self.assertEqual(packet["status"], "partial")
        self.assertEqual(self.runtime.recorder.journal.records, [])

    def test_offlot_balloon_and_event_source_share_scope_and_travel_isolation(self):
        sim = self.add(1)
        rec = self.runtime.recorder
        self.runtime.sources.emit("test.offlot", [sim], {}, "scope_test")
        self.runtime.balloons.on_distribute((SlottedRequest(sim),), {}, True)
        self.assertEqual(len(rec.events), 2)
        for event in rec.events.values():
            self.assertEqual(event["observation_scope"]["kind"], "zone_instantiated")
            self.assertEqual(event["observation_scope"]["off_lot"], "included")
        self.assertEqual(len(self.runtime.balloons.read(entity("sim", 1))["value"]["events"]), 1)
        self.zone.id = 99
        self.runtime.sources.emit("test.offlot", [sim], {}, "scope_test")
        self.runtime.balloons.on_distribute((SlottedRequest(sim),), {}, True)
        self.assertEqual(len(rec.events), 2)
        self.runtime.balloons.close()
        rec.begin_zone({"zone_id": "99"})
        self.assertEqual(self.runtime.balloons.read(entity("sim", 1))["value"]["events"], [])

    def test_context_window_api_returns_event_identity_and_history_pagination_filters(self):
        sim = self.add(1)
        self.adapter.resolve = lambda kind, identifier: self.adapter.reference(sim)
        self.adapter.object_for = lambda target: self.objects.get(int(target["id"]))
        for _ in range(3):
            self.runtime.balloons.on_distribute((SlottedRequest(sim),), {}, True)
        packet = api.get_context(identifier="1", fields=["balloons"], include_history=False,
                                 balloon_window={"from_ticks": 100, "to_ticks": 101, "limit": 1})
        value = packet["snapshot"]["balloons"]["value"]
        self.assertEqual(packet["status"], "partial")
        self.assertEqual(value["total_matches"], 3)
        self.assertTrue(value["has_more"])
        self.assertEqual(self.runtime.recorder.index.status()["snapshots"], 0)
        page = api.query_history(**value["history_query"])
        self.assertEqual(page["history"]["events"], value["events"])
        api.close_history(page["history"]["cursor"], expected_session_id=page["session_id"])
        result = api.get_context(identifier="1", fields=["balloons"], include_history=False,
                                 balloon_window={"past_sim_minutes": 1})
        self.assertEqual(result["snapshot"]["balloons"]["value"]["total_matches"], 3)
        self.assertEqual(result["status"], "complete")

    def test_runtime_captures_offlot_interaction_without_reset_at_lot_crossing(self):
        from support import facts
        sim = self.add(1)
        runtime = self.runtime
        data = facts()
        data.update(actor=entity("sim", 1), parent_interaction_id=None, decision_event_id=None)
        self.adapter.interaction = lambda value: data
        interaction = SimpleNamespace(id=10, sim=sim)
        runtime.capture("started", interaction, "scope_test")
        sim.is_on_active_lot = lambda: True
        runtime.capture("exited", interaction, "scope_test")
        self.assertEqual(len(runtime.recorder.events), 1)
        event = next(iter(runtime.recorder.events.values()))
        self.assertEqual([o["phase"] for o in event["observations"]], ["started", "exited"])
        self.assertEqual(event["observation_scope"]["kind"], "zone_instantiated")


if __name__ == "__main__":
    unittest.main()
