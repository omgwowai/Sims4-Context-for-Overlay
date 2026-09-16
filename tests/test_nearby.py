"""Nearby contracts through real API/Collector/EAAdapter, with fake game services."""

import json
from pathlib import Path
import sys
import threading
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "sdk")]
from context_overlay import VERSION, api, game_runtime, nearby
from context_overlay.collector import Collector
from context_overlay.ea_adapter import EAAdapter
from context_overlay.model import entity
from context_overlay.recorder import Recorder
from context_overlay.storage import Journal
from context_overlay_client import Client, ContextOverlayError
from test_core import MemoryJournal


def node(identifier, x=0, y=0, z=0, kind="sim", level=0, room=10, inventory=False, on_lot=True):
    obj = SimpleNamespace(id=identifier, is_sim=kind == "sim", zone_id=42,
        position=SimpleNamespace(x=x, y=y, z=z), level=level, room=room, parent=None,
        routing_surface=SimpleNamespace(primary_id=42, secondary_id=level, type="WORLD"),
        _hidden_flags=0, is_in_inventory=lambda: inventory, is_on_active_lot=lambda: on_lot)
    obj.sim_info = SimpleNamespace(sim_id=identifier, get_sim_instance=lambda: obj,
                                  first_name="Sim", last_name=str(identifier))
    return obj


class NearbyChecks(unittest.TestCase):
    def setUp(self):
        self.origin = node(100)
        self.objects = [self.origin]
        self.rooms = {}
        self.room_calls = []
        self.manager = SimpleNamespace(get_valid_objects_gen=lambda: iter(self.objects),
                                       get=lambda key: next((obj for obj in self.objects if obj.id == key), None))
        self.active_calls = 0
        def active():
            self.active_calls += 1
            return self.origin
        zone = SimpleNamespace(id=42, lot=SimpleNamespace(lot_id=84), is_zone_running=True)
        info_manager = SimpleNamespace(get=lambda key: next(
            (obj.sim_info for obj in self.objects if obj.is_sim and obj.id == key), None))
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.services = SimpleNamespace(object_manager=lambda: self.manager, get_active_sim=active,
            current_zone=lambda: zone, sim_info_manager=lambda: info_manager,
            time_service=lambda: SimpleNamespace(sim_now=SimpleNamespace(absolute_ticks=lambda: 1000)))
        adapter.reference = lambda obj: entity("sim" if obj.is_sim else "object", obj.id,
                                               "Entity " + str(obj.id), None if obj.is_sim else 77)
        self.adapter = adapter
        def room_id(zone_id, position, level):
            self.room_calls.append((zone_id, position.x, position.y, position.z, level))
            return self.rooms.get((position.x, position.y, position.z, level), 10)
        build_buy = ModuleType("build_buy")
        build_buy.get_room_id = room_id
        sims4, math_module = ModuleType("sims4"), ModuleType("sims4.math")
        math_module.Vector3 = lambda x, y, z: SimpleNamespace(x=x, y=y, z=z)
        sims4.math = math_module
        modules = patch.dict(sys.modules, {"sims4": sims4, "sims4.math": math_module, "build_buy": build_buy})
        modules.start()
        self.addCleanup(modules.stop)
        self.journal = MemoryJournal()
        recorder = Recorder(self.journal, session_id="nearby-run")
        self.runtime = SimpleNamespace(adapter=adapter, recorder=recorder,
            collector=Collector(adapter, recorder, provenance={"source": "fake_game"}),
            session_id="nearby-run", simulation_thread_id=threading.get_ident(), api_ready=True, closed=False)
        for context in (patch.object(game_runtime, "_runtime", self.runtime),
                        patch.object(game_runtime, "_startup_error", None)):
            context.start()
            self.addCleanup(context.stop)

    def add(self, identifier, **kwargs):
        obj = node(identifier, **kwargs)
        self.objects.append(obj)
        self.rooms[(obj.position.x, obj.position.y, obj.position.z, obj.level)] = obj.room
        return obj

    def query(self, **kwargs):
        kwargs.setdefault("radius", 8)
        return api.get_nearby_entities(**kwargs)

    def error(self, code, **kwargs):
        with self.assertRaises(api.APIError) as caught:
            self.query(**kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_exact_radius_sorting_types_and_large_numeric_ids(self):
        self.add(9007199254740993, x=3, z=4)
        self.add(9007199254740992, x=3, z=4)
        self.add(2, x=3, z=4, kind="object")
        self.add(3, x=5.00001)
        packet = self.query(radius=5, kinds=["sim", "object"])
        self.assertEqual([row["entity"]["id"] for row in packet["results"]],
                         ["2", "9007199254740992", "9007199254740993"])
        self.assertEqual(packet["matched_count"], 3)
        self.assertTrue(packet["matched_count_exact"])
        self.assertEqual(packet["status"], "complete")
        self.assertEqual(packet["results"][0]["entity"]["definition_id"], "77")
        json.dumps(packet, allow_nan=False)

    def test_euclidean_metric_changes_filter_and_order(self):
        self.add(2, x=1, y=6)
        self.add(3, x=3)
        self.assertEqual(self.query()["results"][0]["entity"]["id"], "2")
        packet = self.query(metric="euclidean", radius=5)
        self.assertEqual([row["entity"]["id"] for row in packet["results"]], ["3"])

    def test_level_is_not_height_or_routing_surface(self):
        self.add(2, x=1, level=1)
        pool = self.add(3, x=2)
        pool.routing_surface.secondary_id = 1
        pool.routing_surface.type = "POOL"
        packet = self.query()
        self.assertEqual([row["entity"]["id"] for row in packet["results"]], ["3"])
        self.assertTrue(packet["results"][0]["relative"]["same_level"]["value"])
        self.assertFalse(packet["results"][0]["relative"]["same_routing_surface"]["value"])
        self.assertEqual(self.query(same_level=False)["count"], 2)

    def test_zero_radius_and_include_self(self):
        self.add(2)
        self.add(3, x=0.001)
        self.assertEqual(self.query(radius=0)["count"], 1)
        packet = self.query(radius=0, include_self=True)
        self.assertEqual(packet["count"], 2)
        self.assertEqual(packet["results"][1]["entity"]["id"], "100")

    def test_room_only_and_room_intersection_and_zone_identity(self):
        self.add(2, x=30)
        self.add(3, x=1, room=20)
        self.add(4, x=2, level=1)
        other_zone = self.add(5, x=3)
        other_zone.zone_id = 99
        packet = self.query(radius=None, same_room=True, same_level=False)
        self.assertEqual([row["entity"]["id"] for row in packet["results"]], ["2"])
        self.assertEqual(self.query(radius=8, same_room=True)["count"], 0)

    def test_inventory_hidden_offlot_and_uninstantiated_excluded(self):
        self.add(2, x=1, kind="object", inventory=True)
        child = self.add(3, x=2, kind="object")
        child.parent = self.objects[1]
        self.add(4, x=3, on_lot=False)
        self.add(5, x=4)._hidden_flags = 1
        absent = self.add(6, x=5)
        absent.sim_info.get_sim_instance = lambda: None
        self.add(7, x=6, kind="object")
        packet = self.query(kinds=["sim", "object"])
        self.assertEqual([row["entity"]["id"] for row in packet["results"]], ["7"])
        self.assertTrue(packet["coverage"]["complete"])

    def test_room_unknown_is_not_false_and_never_silently_drops_filter(self):
        self.add(2, x=2, room=0)
        packet = self.query()
        row = packet["results"][0]
        self.assertEqual(row["spatial"]["room"]["raw_id"], "0")
        self.assertEqual(row["relative"]["same_room"]["status"], "unsupported")
        self.assertIsNone(row["relative"]["same_room"]["value"])
        self.assertEqual(packet["status"], "partial")
        self.assertTrue(packet["coverage"]["complete"])
        packet = self.query(same_room=True)
        self.assertEqual(packet["count"], 0)
        self.assertFalse(packet["matched_count_exact"])
        self.rooms[(0, 0, 0, 0)] = 0
        self.error("spatial_unavailable", same_room=True)
        self.assertEqual(self.query()["count"], 1)

    def test_native_room_errors_and_missing_level_preserve_evidence(self):
        self.add(2, x=1)
        with patch.object(sys.modules["build_buy"], "get_room_id", side_effect=RuntimeError("native fault")):
            self.error("spatial_unavailable", same_room=True)
            packet = self.query()
            self.assertEqual(packet["origin"]["room"]["reason"], "room_read_failed")
            self.assertEqual(packet["count"], 1)
        self.origin.level = None
        self.error("spatial_unavailable")
        self.assertEqual(self.query(same_level=False)["count"], 1)

    def test_read_failure_is_partial_not_a_false_empty_success(self):
        broken = self.add(2, x=float("nan"))
        packet = self.query()
        self.assertEqual(packet["count"], 0)
        self.assertEqual(packet["coverage"]["reasons"], {"position_unavailable": 1})
        self.assertEqual(packet["status"], "partial")
        broken.position.x = 1
        broken.level = None
        self.assertEqual(self.query()["coverage"]["reasons"], {"level_unavailable": 1})
        self.assertEqual(self.query(same_level=False)["count"], 1)
        broken.parent = broken
        self.assertEqual(self.query()["coverage"]["reasons"], {"candidate_read_failed": 1})

    def test_topk_scans_all_candidates_before_selecting_nearest(self):
        for index in range(10, 0, -1):
            self.add(index, x=index)
        packet = self.query(radius=100, limit=2)
        self.assertEqual([row["entity"]["id"] for row in packet["results"]], ["1", "2"])
        self.assertEqual(packet["matched_count"], 10)
        self.assertTrue(packet["truncated"])
        self.assertTrue(packet["coverage"]["complete"])
        self.assertEqual(len(self.room_calls), 3)  # center + returned entries only
        self.assertNotIn("cursor", packet)

    def test_scan_limit_and_enumeration_error_are_exposed(self):
        for index in range(1, 5):
            self.add(index, x=index)
        with patch.object(nearby, "MAX_SCANNED", 3):
            packet = self.query()
            self.assertEqual(packet["coverage"]["scanned_count"], 3)
            self.assertFalse(packet["coverage"]["enumeration_complete"])
            self.assertFalse(packet["matched_count_exact"])
        def broken_iteration():
            yield self.objects[1]
            raise RuntimeError("manager invalidated")
        self.manager.get_valid_objects_gen = broken_iteration
        packet = self.query()
        self.assertEqual(packet["count"], 1)
        self.assertEqual(packet["coverage"]["reasons"], {"enumeration_failed": 1})

    def test_duplicate_candidates_do_not_consume_limit(self):
        other = self.add(2, x=1)
        self.objects.extend([other, other])
        self.assertEqual(self.query()["matched_count"], 1)

    def test_invalid_input_before_any_game_read(self):
        for options in ({"radius": None}, {"radius": -1}, {"radius": True}, {"radius": float("inf")},
                        {"radius": float("nan")}, {"radius": 10 ** 1000}, {"radius": "8"},
                        {"radius": 1000001}, {"kinds": []}, {"kinds": "sim"},
                        {"kinds": ["sim", "sim"]}, {"kinds": ["npc"]}, {"limit": True},
                        {"limit": 65}, {"same_room": 1}, {"same_level": None},
                        {"include_self": 0}, {"metric": "path"}, {"identifier": 0}, {"bogus": 1}):
            with self.subTest(options=options):
                self.error("invalid_request", **options)
        self.assertEqual(self.active_calls, 0)
        self.assertEqual(self.room_calls, [])

    def test_center_scope_existence_and_active_resolved_once(self):
        self.error("target_unavailable", identifier="999")
        self.origin.is_on_active_lot = lambda: False
        self.error("target_out_of_scope")
        self.origin.is_on_active_lot = lambda: True
        self.active_calls = 0
        self.query()
        self.assertEqual(self.active_calls, 1)
        self.origin.position.x = float("inf")
        self.error("spatial_unavailable")

    def test_thread_session_and_collector_guards_before_reads(self):
        self.error("session_changed", expected_session_id="old")
        self.runtime.collector.enabled = False
        self.error("collector_disabled")
        self.runtime.collector.enabled = True
        self.runtime.api_ready = False
        self.error("not_ready")
        self.runtime.api_ready = True
        errors = []
        def worker():
            try:
                self.query()
            except api.APIError as exc:
                errors.append(exc.code)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(errors, ["wrong_thread"])
        self.assertEqual(self.active_calls, 0)

    def test_no_history_or_storage_writes_and_detached_result(self):
        other = self.add(2, x=2)
        self.runtime.recorder.enabled = False
        self.runtime.collector.semantic_enabled = False
        before = self.runtime.recorder.status()
        packet = Client().get_nearby_entities(radius=8)
        self.assertEqual(packet["api_version"], "1.1.0")
        self.assertEqual(packet["module_version"], VERSION)
        self.assertEqual(packet["status"], "complete")
        packet["results"][0]["spatial"]["position"]["value"]["x"] = 900
        self.assertEqual(other.position.x, 2)
        self.assertEqual(self.journal.records, [])
        self.assertEqual(self.runtime.recorder.status(), before)
        self.assertNotIn("history", packet)

    def test_sdk_old_provider_keeps_existing_calls_and_rejects_missing_capability(self):
        provider = SimpleNamespace(get_api_info=lambda: {"api_version": "1.0.0", "schema_version": "1",
                                                          "capabilities": ["context.read"]},
                                   get_context=lambda *args, **kwargs: {"old": True})
        client = Client(provider)
        self.assertEqual(client.get_context(), {"old": True})
        with self.assertRaises(ContextOverlayError) as caught:
            client.get_nearby_entities(radius=8)
        self.assertEqual(caught.exception.code, "capability_unavailable")

    def test_console_export_writes_a_nearby_packet_without_history_events(self):
        self.add(2, x=1, kind="object")
        with tempfile.TemporaryDirectory() as directory:
            self.runtime.writer = Journal(directory)
            try:
                result = game_runtime.Runtime.export_nearby(self.runtime, radius="room", kinds="all")
            finally:
                self.runtime.writer.close(wait=True)
            packet = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(packet["kind"], "nearby_entities")
            self.assertEqual(packet["count"], 1)
            self.assertIsNone(packet["query"]["radius"])
            self.assertTrue(packet["query"]["same_room"])
            self.assertEqual(Path(directory, "journal.jsonl").read_text(encoding="utf-8"), "")
        self.assertEqual(self.journal.records, [])

    def test_identity_failure_preserves_nearest_entity_and_marks_partial(self):
        self.add(2, x=1, kind="object")
        original = self.adapter.reference
        def reference(obj):
            if not obj.is_sim:
                raise ValueError("name failure")
            return original(obj)
        self.adapter.reference = reference
        packet = self.query(kinds=["object"])
        self.assertEqual(packet["results"][0]["entity"]["key"], "object:2")
        self.assertEqual(packet["status"], "partial")
        self.assertTrue(packet["coverage"]["complete"])


if __name__ == "__main__":
    unittest.main()
