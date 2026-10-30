"""Active-lot contract against the real adapter/API and fake EA services."""

import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import test_nearby as fixtures
from context_overlay import api, scene_entities
from context_overlay_client import Client, ContextOverlayError


class SceneEntitiesTests(unittest.TestCase):
    def setUp(self):
        fixtures.NearbyChecks.setUp(self)
        self.manager.values = lambda: iter(self.objects)

    add = fixtures.NearbyChecks.add

    def test_no_live_anchor_all_rooms_levels_and_more_than_64_results(self):
        self.objects.clear()
        self.origin = None
        for identifier in range(1, 82):
            self.add(identifier, x=identifier * 20000, level=identifier % 4, room=identifier,
                     kind="sim" if identifier % 2 else "object")
        packet = api.get_scene_entities("999999999999999999")
        self.assertEqual(packet["count"], 81)
        self.assertEqual(self.active_calls, 0)
        self.assertTrue(packet["coverage"]["complete"])
        self.assertFalse(packet["truncated"])
        self.assertEqual(packet["scope"]["kind"], "active_lot_instantiated")
        self.assertEqual(packet["scope"]["off_lot"], "excluded")
        self.assertEqual(packet["provider_scope"]["off_lot"], "included")
        self.assertEqual(packet["scope"]["lot_id"], "84")
        self.assertEqual(packet["scope"]["zone_id"], "42")

    def test_exact_ids_sort_dedup_and_kinds(self):
        self.add(9007199254740993, kind="object")
        self.add(9007199254740992, kind="object")
        self.add(2, kind="object")
        self.objects.append(self.objects[-1])
        packet = Client().get_scene_entities(kinds=["object"])
        self.assertEqual([r["entity"]["id"] for r in packet["results"]],
                         ["2", "9007199254740992", "9007199254740993"])
        self.assertEqual(packet["coverage"]["duplicate_count"], 1)
        self.assertTrue(packet["matched_count_exact"])
        json.dumps(packet, allow_nan=False)

    def test_world_scope_exclusions_and_unknown_membership(self):
        self.add(2, on_lot=False)
        self.add(3, kind="object", inventory=True)
        child = self.add(4, kind="object")
        child.parent = self.objects[-2]
        self.add(5)._hidden_flags = 1
        self.add(6).sim_info.get_sim_instance = lambda: None
        self.add(7).zone_id = 999
        packet = api.get_scene_entities()
        self.assertEqual([r["entity"]["id"] for r in packet["results"]], ["100"])
        self.assertTrue(packet["coverage"]["complete"])
        self.add(8).is_on_active_lot = lambda: None
        packet = api.get_scene_entities()
        self.assertFalse(packet["matched_count_exact"])
        self.assertEqual(packet["coverage"]["reasons"]["lot_membership_unavailable"], 1)
        self.assertEqual(packet["coverage"]["errors"][0]["entity"]["id"], "8")

    def test_label_and_spatial_failure_keep_instance_identity(self):
        obj = self.add(2, kind="object", x=float("nan"))
        original = self.adapter.reference
        def reference(value):
            if value is obj:
                raise ValueError("label failed")
            return original(value)
        self.adapter.reference = reference
        packet = api.get_scene_entities()
        result = packet["results"][0]
        self.assertEqual(result["entity"]["id"], "2")
        self.assertEqual(result["identity_status"]["status"], "error")
        self.assertEqual(result["spatial"]["position"]["reason"], "position_read_failed")
        self.assertTrue(packet["matched_count_exact"])
        self.assertFalse(packet["coverage"]["data_complete"])
        self.assertEqual(packet["status"], "partial")

    def test_identity_corruption_cannot_replace_the_instance_id(self):
        self.adapter.reference = lambda obj: {"id": "22", "kind": "object", "key": "object:22"}
        packet = api.get_scene_entities()
        self.assertEqual(packet["results"][0]["entity"]["key"], "sim:100")
        self.assertEqual(packet["status"], "partial")

    def test_scan_limit_counts_hidden_entries_and_exact_boundary(self):
        self.add(2)._hidden_flags = 1
        with patch.object(scene_entities, "MAX_SCANNED", 2):
            self.assertFalse(api.get_scene_entities()["truncated"])
            self.add(3)
            packet = api.get_scene_entities()
        self.assertEqual(packet["coverage"]["scanned_count"], 2)
        self.assertEqual(packet["coverage"]["reasons"]["scan_limit"], 1)
        self.assertTrue(packet["truncated"])
        self.assertFalse(packet["matched_count_exact"])

    def test_enumeration_error_preserves_prefix_and_error_details(self):
        def broken():
            yield self.origin
            raise RuntimeError("manager invalidated")
        self.manager.values = broken
        packet = api.get_scene_entities()
        self.assertEqual(packet["count"], 1)
        self.assertTrue(packet["truncated"])
        self.assertEqual(packet["coverage"]["errors"][0]["message"], "manager invalidated")

    def test_diagnostic_cap_counts_omitted_errors(self):
        for identifier in range(1, 5):
            self.add(identifier).is_on_active_lot = lambda: "unknown"
        with patch.object(scene_entities, "MAX_ERRORS", 2):
            packet = api.get_scene_entities()
        self.assertEqual(packet["coverage"]["unresolved_count"], 4)
        self.assertEqual(len(packet["coverage"]["errors"]), 2)
        self.assertEqual(packet["coverage"]["errors_omitted_count"], 2)

    def test_read_clocks_and_detachment_no_persistence(self):
        packet = api.get_scene_entities()
        self.assertEqual(packet["read_started"]["ticks"], "1000")
        self.assertGreaterEqual(packet["read_finished_unix_ms"], packet["read_started_unix_ms"])
        for result in packet["results"]:
            self.assertIn("read_started", result)
            self.assertIn("read_finished_unix_ms", result)
        packet["results"][0]["spatial"]["position"]["value"]["x"] = 42
        self.assertEqual(self.origin.position.x, 0)
        self.assertEqual(self.journal.records, [])

    def test_scope_change_is_incomplete(self):
        scope = self.adapter.scope()
        with patch.object(self.adapter, "scope", side_effect=[scope, dict(scope, zone_id="99")]):
            packet = api.get_scene_entities()
        self.assertFalse(packet["coverage"]["complete"])
        self.assertIn("scope_changed", packet["coverage"]["reasons"])

    def test_invalid_input_and_session_collector_guards_precede_enumeration(self):
        with patch.object(self.adapter, "scene_objects", side_effect=AssertionError("must not read")):
            for options, code in [({"kinds": []}, "invalid_request"),
                                  ({"identifier": 1.1}, "invalid_request"),
                                  ({"expected_session_id": "stale"}, "session_changed")]:
                with self.assertRaises(api.APIError) as caught:
                    api.get_scene_entities(**options)
                self.assertEqual(caught.exception.code, code)
            self.runtime.collector.enabled = False
            with self.assertRaises(api.APIError) as caught:
                api.get_scene_entities()
            self.assertEqual(caught.exception.code, "collector_disabled")

    def test_wrong_thread_and_missing_capability_have_no_nearby_fallback(self):
        errors = []
        def worker():
            try:
                api.get_scene_entities()
            except api.APIError as exc:
                errors.append(exc.code)
        thread = threading.Thread(target=worker)
        thread.start(); thread.join()
        self.assertEqual(errors, ["wrong_thread"])
        provider = SimpleNamespace(get_api_info=lambda: {"api_version": "2.6.0", "schema_version": "2",
                                                          "capabilities": ["context.nearby_entities"]})
        with self.assertRaises(ContextOverlayError) as caught:
            Client(provider).get_scene_entities()
        self.assertEqual(caught.exception.code, "capability_unavailable")
        self.assertEqual(api.get_api_info()["local_extensions"]["get_scene_entities"], "scene_archive/2")


if __name__ == "__main__":
    unittest.main()
