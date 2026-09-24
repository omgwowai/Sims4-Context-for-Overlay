"""Frustum geometry, native-adapter boundaries, freshness and public API contracts."""

import json
import math
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "sdk")]
from context_overlay import api, camera_state, camera_view
from context_overlay.ea_adapter import EAAdapter
from context_overlay.hooks import Hooks
from context_overlay.model import entity
from context_overlay.recorder import Recorder
from context_overlay_client import Client, ContextOverlayError
from support import MemoryJournal, runtime_fixture


def v(x=0, y=0, z=0):
    return SimpleNamespace(x=x, y=y, z=z)


class CameraChecks(unittest.TestCase):
    def setUp(self):
        self.camera = ModuleType("camera")
        self.camera._camera_position, self.camera._target_position = v(), v(z=1)
        self.camera._zone_id, self.camera._follow_mode = 42, False
        self.objects = []
        self.zone = SimpleNamespace(id=42, lot=SimpleNamespace(lot_id=84), is_zone_running=True)
        self.adapter = EAAdapter.__new__(EAAdapter)
        self.manager = SimpleNamespace(values=lambda: iter(self.objects),
            get_valid_objects_gen=lambda: (obj for obj in self.objects if not obj._hidden_flags))
        self.adapter.services = SimpleNamespace(current_zone=lambda: self.zone,
            object_manager=lambda: self.manager,
            time_service=lambda: SimpleNamespace(sim_now=SimpleNamespace(absolute_ticks=lambda: 100)))
        self.adapter.reference = lambda obj: entity("sim" if obj.is_sim else "object", obj.id,
                                                   "Entity " + str(obj.id), None if obj.is_sim else 77)
        self.journal = MemoryJournal()
        self.runtime = runtime_fixture(self, self.adapter, Recorder(self.journal, session_id="view-run"))
        context = patch.dict(sys.modules, {"camera": self.camera})
        context.start()
        self.addCleanup(context.stop)
        camera_state.SYNC.reset()
        self.addCleanup(camera_state.SYNC.reset)

    def add(self, identifier, x=0, y=0, z=10, kind="object", on_lot=True, level=0):
        obj = SimpleNamespace(id=identifier, is_sim=kind == "sim", zone_id=42, position=v(x,y,z),
            level=level, parent=None, _hidden_flags=0, scale=1.0,
            routing_surface=SimpleNamespace(primary_id=42, secondary_id=level, type="WORLD"),
            is_in_inventory=lambda: False, is_on_active_lot=lambda: on_lot)
        obj.sim_info = SimpleNamespace(sim_id=identifier, get_sim_instance=lambda: obj)
        self.objects.append(obj)
        return obj

    def query(self, **kwargs):
        kwargs.setdefault("vertical_fov", 90)
        kwargs.setdefault("aspect_ratio", 1)
        return api.get_camera_view(**kwargs)

    def ids(self, **kwargs):
        return [row["entity"]["id"] for row in self.query(**kwargs)["results"]]

    def error(self, code, **kwargs):
        with self.assertRaises(api.APIError) as caught:
            self.query(**kwargs)
        self.assertEqual(caught.exception.code, code)

    def test_default_parameters_and_no_distance_or_result_cutoff(self):
        for i in range(1, 130):
            self.add(i, z=i*1000)
        packet = api.get_camera_view()
        self.assertEqual(packet["count"], 129)
        self.assertEqual(packet["query"]["vertical_fov"], 45)
        self.assertEqual(packet["query"]["aspect_ratio"], 16/9)
        self.assertIsNone(packet["query"]["far"])
        self.assertFalse(packet["truncated"])
        self.assertTrue(packet["coverage"]["complete"])
        self.assertEqual(packet["scope"]["kind"], "zone_instantiated")
        self.assertEqual(packet["camera"]["freshness"]["status"], "unknown")
        self.assertEqual(packet["api_version"], api.API_VERSION)
        json.dumps(packet, allow_nan=False)

    def test_six_planes_and_boundaries(self):
        for i, point in enumerate([(0,0,10), (10,0,10), (-10,0,10), (0,10,10), (0,-10,10),
                                   (10.01,0,10), (-10.01,0,10), (0,10.01,10), (0,-10.01,10),
                                   (0,0,-0.01), (0,0,10.01)], 1):
            self.add(i, *point)
        self.assertEqual(set(self.ids(far=10)), {"1", "2", "3", "4", "5"})

    def test_sphere_radius_is_normal_distance_to_plane(self):
        obj = self.add(1, x=11.3, z=10, kind="sim")
        obj.object_radius = 1
        self.assertEqual(self.ids(), ["1"])
        self.assertEqual(self.query()["results"][0]["containment"], "intersects")
        obj.position.x = 11.5
        self.assertEqual(self.ids(), [])
        obj.position = v(z=10.9)
        self.assertEqual(self.ids(far=10), ["1"])
        obj.position = v(z=11.1)
        self.assertEqual(self.ids(far=10), [])
        obj.position = v(z=-0.5)
        self.assertEqual(self.ids(), ["1"])
        obj.position = v(z=-1.01)
        self.assertEqual(self.ids(), [])

    def test_aspect_fov_and_far_are_independent_filters(self):
        self.add(1, x=15)
        self.add(2, y=15)
        self.assertEqual(self.ids(), [])
        self.assertEqual(self.ids(aspect_ratio=2), ["1"])
        self.assertEqual(set(self.ids(vertical_fov=120)), {"1", "2"})
        # Far is forward depth, not Euclidean distance.
        self.assertEqual(self.ids(aspect_ratio=2, far=10), ["1"])

    def test_rotation_translation_zoom_and_vertical_direction(self):
        self.add(1, x=100, z=0)
        self.add(2, x=50, y=100, z=0)
        self.camera._camera_position, self.camera._target_position = v(x=50), v(x=100)
        self.assertEqual(self.ids(), ["1"])
        self.camera._target_position = v(x=50, y=100)
        self.assertEqual(self.ids(), ["2"])
        self.assertEqual(self.query()["camera"]["orientation_basis"], "z_up_fallback")
        self.camera._camera_position = v(x=50, y=110)
        self.camera._target_position = v(x=50, y=100)
        self.assertIn("2", self.ids())

    def test_offlot_levels_and_kinds_inventory_hidden_parenting(self):
        self.add(1, kind="sim", on_lot=False, level=3)
        self.add(2, kind="object", on_lot=False, level=-1)
        self.add(3)._hidden_flags = 1
        bag = self.add(4)
        bag.is_in_inventory = lambda: True
        self.add(5).parent = bag
        self.add(6, kind="sim").sim_info.get_sim_instance = lambda: None
        self.add(7).zone_id = 99
        self.assertEqual(set(self.ids()), {"1", "2"})
        self.assertEqual(self.ids(kinds=["sim"]), ["1"])
        row = self.query(kinds=["object"])["results"][0]
        self.assertFalse(row["same_lot"]["value"])
        self.assertEqual(row["context_scope"], "active_lot_instantiated")
        self.assertFalse(self.adapter.in_scope(self.objects[0]))

    def test_offset_footprint_scale_and_radius_fallbacks(self):
        obj = self.add(1, x=13)
        obj.get_fooptrint_polygon_bounds = lambda: (v(x=1), v(x=2))
        obj.scale = 2
        packet = self.query()
        self.assertEqual(packet["results"][0]["bounds"]["radius"], 4)
        self.assertEqual(packet["results"][0]["bounds"]["method"], "footprint_proxy_sphere")
        obj.scale = float("nan")
        self.assertEqual(self.ids(), [])
        obj.object_radius, obj._routing_context = 3, object()
        self.assertEqual(self.ids(), ["1"])
        obj.object_radius = -1
        self.assertEqual(self.adapter.camera_bounds(obj)["method"], "position")
        obj.object_radius, obj._routing_context = 100, None
        self.assertEqual(self.adapter.camera_bounds(obj)["method"], "position")

    def test_valid_empty_is_distinct_from_invalid_camera(self):
        self.assertEqual(self.query()["status"], "complete")
        self.camera._target_position = v()
        self.error("camera_unavailable")
        self.camera._target_position = v(z=1)
        self.camera._camera_position.x = float("nan")
        self.error("camera_unavailable")
        self.camera._camera_position = None
        self.error("camera_unavailable")
        self.camera._zone_id = 99
        self.error("camera_zone_mismatch")
        self.camera._zone_id = None
        self.error("camera_unavailable")

    def test_bad_parameters_rejected_before_game_reads(self):
        for kwargs in ({"kinds": []}, {"kinds": "sim"}, {"kinds": ["sim","sim"]}, {"kinds": ["bad"]},
                       {"vertical_fov": 0}, {"vertical_fov": 180}, {"vertical_fov": True},
                       {"vertical_fov": float("nan")}, {"aspect_ratio": 0}, {"aspect_ratio": False},
                       {"aspect_ratio": float("inf")}, {"aspect_ratio": 10**1000},
                       {"far": -1}, {"far": 0}, {"far": float("inf")}, {"far": "2"}, {"limit": 3}):
            with self.subTest(kwargs=kwargs), patch.object(self.adapter, "camera_scope", side_effect=AssertionError("read")):
                self.error("invalid_request", **kwargs)

    def test_guards_before_camera_reads_and_not_ready_zone(self):
        with patch.object(self.adapter, "camera_snapshot", side_effect=AssertionError("read")):
            self.error("session_changed", expected_session_id="another")
            self.runtime.collector.enabled = False
            self.error("collector_disabled")
            self.runtime.collector.enabled = True
            self.runtime.closed = True
            self.error("session_closed")
            self.runtime.closed = False
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
        self.zone.is_zone_running = False
        self.error("not_ready")

    def test_scan_budget_and_read_gaps_preserve_known_matches(self):
        self.add(1)
        self.add(2, x=float("nan"))
        self.add(3)
        with patch.object(camera_view, "MAX_SCANNED", 2):
            packet = self.query()
        self.assertEqual(packet["count"], 1)
        self.assertEqual(packet["coverage"]["reasons"], {"position_unavailable":1, "scan_limit":1})
        self.assertFalse(packet["coverage"]["complete"])
        self.assertFalse(packet["matched_count_exact"])
        self.assertFalse(packet["truncated"])
        self.assertEqual(packet["status"], "partial")
        self.objects[1].position = v(z=10)
        self.objects[1].parent = self.objects[1]
        self.assertEqual(self.query()["coverage"]["reasons"], {"candidate_read_failed": 1})

    def test_exact_scan_boundary_and_enumeration_exception(self):
        self.add(1)
        with patch.object(camera_view, "MAX_SCANNED", 1):
            self.assertTrue(self.query()["coverage"]["complete"])
        def broken():
            yield self.objects[0]
            raise RuntimeError("iterator fault")
        self.manager.values = broken
        packet = self.query()
        self.assertEqual(packet["count"], 1)
        self.assertEqual(packet["coverage"]["reasons"], {"enumeration_failed":1})

    def test_hidden_objects_count_toward_scan_budget(self):
        self.add(1)._hidden_flags = 1
        self.add(2)._hidden_flags = 1
        self.add(3)
        with patch.object(camera_view, "MAX_SCANNED", 2):
            packet = self.query()
        self.assertEqual(packet["count"], 0)
        self.assertEqual(packet["coverage"]["scanned_count"], 2)
        self.assertFalse(packet["coverage"]["complete"])
        self.assertEqual(packet["coverage"]["reasons"], {"scan_limit": 1})
        with patch.object(camera_view, "MAX_SCANNED", 3):
            packet = self.query()
        self.assertEqual([row["entity"]["id"] for row in packet["results"]], ["3"])
        self.assertEqual(packet["coverage"]["scanned_count"], 3)
        self.assertTrue(packet["coverage"]["complete"])

    def test_optional_fields_and_name_failures_do_not_erase_geometric_matches(self):
        self.add(1).level = None
        with patch.object(self.adapter, "reference", side_effect=ValueError("bad label")):
            packet = self.query()
        self.assertEqual(packet["count"], 1)
        self.assertTrue(packet["coverage"]["complete"])
        self.assertEqual(packet["status"], "partial")
        self.assertEqual(packet["results"][0]["entity"]["id"], "1")

    def test_numeric_id_order_dedup_and_detached_data_without_recording(self):
        self.add(9007199254740993, kind="sim")
        self.add(9007199254740992, kind="sim")
        self.add(10)
        self.objects.append(self.objects[0])
        packet = Client(api).get_camera_view()
        self.assertEqual([row["entity"]["id"] for row in packet["results"]],
                         ["10", "9007199254740992", "9007199254740993"])
        packet["camera"]["position"]["x"] = 300
        packet["results"][0]["spatial"]["position"]["value"]["z"] = 0
        self.assertEqual(self.camera._camera_position.x, 0)
        self.assertEqual(self.objects[2].position.z, 10)
        self.assertEqual(self.journal.records, [])
        self.assertEqual(self.runtime.recorder.index.status()["snapshots"], 0)
        self.assertNotIn("cursor", packet)

    def test_sdk_capability_guard_and_error_translation(self):
        provider = SimpleNamespace(get_api_info=lambda: {"api_version":"2.2.0", "schema_version":"2"})
        with self.assertRaises(ContextOverlayError) as caught:
            Client(provider).get_camera_view()
        self.assertEqual(caught.exception.code, "capability_unavailable")
        self.camera._zone_id = 99
        with self.assertRaises(ContextOverlayError) as caught:
            Client(api).get_camera_view()
        self.assertEqual(caught.exception.code, "camera_zone_mismatch")
        self.assertIn("context.camera_view", api.get_api_info()["capabilities"])

    def test_camera_observation_reset_and_hook_ownership(self):
        class Zone:
            def on_teardown(self):
                return "closed"
        self.camera.update = lambda: "updated"
        self.camera.deserialize = lambda: "restored"
        hooks = Hooks(lambda message: self.fail(message))
        camera_state.install(hooks, self.camera, Zone)
        self.addCleanup(hooks.remove)
        self.assertEqual(self.camera.update(), "updated")
        freshness = self.query()["camera"]["freshness"]
        self.assertEqual(freshness["status"], "observed")
        self.assertGreaterEqual(freshness["age_seconds"], 0)
        # External mutation must not reuse a timestamp for different data.
        self.camera._camera_position.x = 0.1
        self.assertEqual(self.query()["camera"]["freshness"]["status"], "unknown")
        self.camera.update()
        self.camera.deserialize()
        self.assertIsNone(camera_state.SYNC.updated_at)
        self.camera.update()
        Zone().on_teardown()
        self.assertIsNone(camera_state.SYNC.updated_at)
        hooks.remove()
        self.camera.update()
        self.assertIsNone(camera_state.SYNC.updated_at)


if __name__ == "__main__":
    unittest.main()
