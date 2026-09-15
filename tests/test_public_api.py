"""Public contract exercised against real Collector/Recorder, no EA imports."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from context_overlay import api, game_runtime
from context_overlay.collector import Collector
from context_overlay.model import entity, field
from context_overlay.recorder import Recorder
from test_core import MemoryJournal, facts

spec = importlib.util.spec_from_file_location("vendored_client", str(ROOT / "sdk/context_overlay_client.py"))
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)


class ContractChecks(unittest.TestCase):
    def setUp(self):
        class Adapter:
            reads = 0
            resolutions = []
            shared = {"value": 42}
            def resolve(self, kind, identifier):
                self.resolutions.append((kind, identifier))
                if kind == "sim":
                    return facts()["actor"] if identifier == "active" else entity("sim", identifier, "阿明")
                return entity("object", identifier, "食物")
            def scope(self):
                return {"kind": "active_lot_instantiated", "off_lot": "excluded"}
            def clock(self):
                return {"ticks": "100", "display": "test time"}
            def read(self, target, name):
                self.reads += 1
                return field(target if name == "identity" else self.shared)
        self.adapter = Adapter()
        self.adapter.resolutions = []
        self.journal = MemoryJournal()
        self.now = [0]
        self.recorder = Recorder(self.journal, session_id="run-a", clock=lambda: self.now[0])
        self.runtime = SimpleNamespace(adapter=self.adapter, recorder=self.recorder,
            collector=Collector(self.adapter, self.recorder), provenance={"source": "test"},
            session_id="run-a", simulation_thread_id=threading.get_ident(), api_ready=True, closed=False)
        self.runtime_patch = patch.object(game_runtime, "_runtime", self.runtime)
        self.runtime_patch.start()
        self.addCleanup(self.runtime_patch.stop)
        error_patch = patch.object(game_runtime, "_startup_error", None)
        error_patch.start()
        self.addCleanup(error_patch.stop)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(api.APIError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        json.dumps(caught.exception.to_dict())

    def add_events(self, count=3):
        for index in range(count):
            self.recorder.interaction("started", facts(index + 1), index + 10, "test")

    def test_retained_identity_and_sdk_grouped_effects(self):
        action = self.recorder.interaction("started", facts(1), 10, "test")
        self.recorder.fact("skill.level", [facts()["actor"]], {"before": 1, "after": 2}, 11, "test",
                           cause={"event_id": action["event_id"], "basis": "resolver.interaction"})
        client = sdk.Client()
        with patch.object(self.adapter, "resolve", side_effect=AssertionError("Historical identity must not resolve live")):
            packet = client.query_history(identifier=facts()["actor"]["id"], group_effects=True)
            self.assertEqual(packet["history"]["total_matches"], 1)
            self.assertEqual(packet["history"]["events"][0]["effects"][0]["category"], "skill.level")
            client.close_history(packet["history"]["cursor"], expected_session_id=packet["session_id"])
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)

    def test_import_and_metadata_do_not_initialize_runtime_or_game(self):
        script = "import sys; sys.path[:0] = {0!r}; from context_overlay import api; import context_overlay_client as s; s.Client(); api.get_api_info(); assert 'services' not in sys.modules; assert 'context_overlay.game_runtime' not in sys.modules".format(
            [str(ROOT / "src"), str(ROOT / "sdk")])
        subprocess.check_call([sys.executable, "-c", script])

    def test_readiness_loading_failed_closed_and_ready(self):
        with patch.object(game_runtime, "_runtime", None):
            self.assertEqual(api.get_status()["state"], "waiting_for_zone")
            self.error("not_ready", api.get_context)
        self.runtime.api_ready = False
        self.assertEqual(api.get_status()["state"], "starting")
        self.error("not_ready", api.get_context)
        with patch.object(game_runtime, "_startup_error", "example failure"):
            self.assertEqual(api.get_status()["state"], "startup_failed")
        self.runtime.closed = True
        self.error("session_closed", api.get_context)
        self.runtime.closed, self.runtime.api_ready = False, True
        self.assertTrue(api.get_status()["ready"])

    def test_worker_rejected_before_game_reads_and_query_mutations(self):
        result = []
        def worker():
            self.assertEqual(api.get_api_info()["api_version"], "1.1.0")
            for function in (api.get_status, api.get_context, api.query_history):
                try:
                    function()
                except api.APIError as exc:
                    result.append(exc.code)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(result, ["wrong_thread"] * 3)
        self.assertEqual(self.adapter.reads, 0)
        self.assertEqual(self.adapter.resolutions, [])
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)

    def test_context_defaults_active_pin_detachment_and_no_file_write(self):
        self.add_events()
        packet = api.get_context()
        self.assertEqual(packet["api_version"], "1.1.0")
        self.assertEqual(len(packet["history"]["events"]), 3)
        self.assertEqual(self.adapter.resolutions[:2], [("sim", "active"), ("sim", facts()["actor"]["id"])])
        self.assertEqual(packet["target"]["id"], "18446744073709550001")
        packet["snapshot"]["needs"]["value"]["value"] = 900
        packet["history"]["events"][0]["facts"]["actor"]["name"] = "changed"
        self.assertEqual(self.adapter.shared["value"], 42)
        self.assertNotEqual(next(iter(self.recorder.events.values()))["facts"]["actor"]["name"], "changed")
        self.assertEqual(len(self.journal.records), 3)
        obj = api.get_context("object", "123", include_history=False, representation="raw")
        self.assertEqual(obj["requested_fields"], ["identity", "time", "location", "object_states"])
        self.assertNotIn("rendered", obj)
        self.assertEqual(obj["history"]["status"], "not_requested")
        json.dumps(packet, allow_nan=False)

    def test_strict_validation_and_session_guard_precede_reads(self):
        for options in ({"fields": "needs"}, {"fields": ["needs", "needs"]}, {"fields": [1]},
                        {"history_limit": True}, {"include_history": 1}, {"representation": "unknown"},
                        {"typo_option": True}):
            self.error("invalid_request", api.get_context, **options)
        for identifier in (1.1, True, 0, "-1", "18446744073709551616", object()):
            self.error("invalid_request", api.get_context, "sim", identifier)
        self.error("session_changed", api.get_context, expected_session_id="other-run")
        self.assertEqual(self.adapter.reads, 0)
        self.error("invalid_query", api.query_history, page_size=True)
        self.error("invalid_query", api.query_history, from_ticks=5, to_ticks=4)
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)

    def test_disabled_or_failed_modules_remain_explicit(self):
        self.recorder.enabled = False
        self.runtime.collector.semantic_enabled = False
        packet = api.get_context()
        self.assertEqual(packet["status"], "partial")
        self.assertEqual(packet["history"]["status"], "disabled")
        self.assertEqual(packet["rendered"]["status"], "disabled")
        self.runtime.collector.enabled = False
        self.error("collector_disabled", api.get_context)
        history = api.query_history()
        self.assertEqual(history["status"], "partial")
        api.close_history(history["history"]["cursor"], expected_session_id=history["session_id"])
        self.recorder.enabled = True
        self.recorder.fail("test overload")
        self.assertEqual(api.get_status()["recorder"]["state"], "failed")

    def test_out_of_scope_and_unknown_entity_history_are_not_empty_success_claims(self):
        with patch.object(self.adapter, "read", return_value=field(status="out_of_scope", reason="not on lot")):
            packet = api.get_context("sim", "44", fields=["needs"])
        self.assertEqual(packet["status"], "partial")
        self.assertEqual(packet["history"]["target_observation"]["status"], "not_observed")
        with patch.object(self.adapter, "resolve", side_effect=ValueError("No active Sim")):
            self.error("target_unavailable", api.get_context)

    def test_history_filters_fixed_versions_repeat_page_and_release(self):
        self.add_events()
        packet = api.query_history(page_size=1, from_ticks="11", to_ticks="13", event_types=["interaction"])
        page = packet["history"]
        self.assertEqual(page["total_matches"], 2)
        self.assertEqual(page["events"][0]["facts"]["interaction_id"], "3")
        cursor = page["next_cursor"]
        self.recorder.interaction("exited", dict(facts(2), finishing_type="USER_CANCEL"), 30, "test")
        following = api.get_history_page(cursor, expected_session_id=packet["session_id"])
        self.assertEqual(following["history"]["events"][0]["stage"], "running")
        self.assertEqual(following["history"], api.get_history_page(cursor, expected_session_id="run-a")["history"])
        self.assertEqual(len(following["rendered"]["history"]), 1)
        self.assertTrue(api.close_history(page["cursor"], expected_session_id="run-a")["released"])
        self.assertFalse(api.close_history(page["cursor"], expected_session_id="run-a")["released"])
        self.error("cursor_expired", api.get_history_page, cursor, expected_session_id="run-a")

    def test_expiry_and_session_switch_never_reuse_old_queries(self):
        packet = api.query_history()
        cursor = packet["history"]["cursor"]
        self.now[0] = 121
        self.error("cursor_expired", api.get_history_page, cursor, expected_session_id="run-a")
        self.runtime.session_id = "run-b"
        self.error("session_changed", api.get_history_page, cursor, expected_session_id="run-a")
        self.assertEqual(api.close_history(cursor, expected_session_id="run-a")["reason"], "session_changed")

    def test_budget_failure_and_render_failure_do_not_leak_new_query(self):
        self.recorder.index.snapshot_limit = 1
        packet = api.query_history()
        self.error("query_limit", api.query_history)
        api.close_history(packet["history"]["cursor"], expected_session_id="run-a")
        with patch.object(api, "render", side_effect=RuntimeError("test renderer fault")):
            self.error("internal_error", api.query_history)
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)

    def test_sdk_manages_queries_without_caching_runtime_or_trusting_mutated_page(self):
        self.add_events()
        client = sdk.Client()
        with client.history(page_size=1) as query:
            query.page["history"]["next_cursor"] = "invalid mutation"
            self.assertEqual(len(query.next_page()["history"]["events"]), 1)
            self.assertTrue(query.has_more)
            query.next_page()
            self.assertIsNone(query.next_page())
        self.assertTrue(query.closed)
        self.assertFalse(query.close()["released"])
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)
        self.runtime.session_id = self.recorder.session_id = "run-b"
        self.assertEqual(client.get_context(include_history=False)["session_id"], "run-b")

    def test_sdk_reports_missing_dependency_incompatible_api_and_provider_errors(self):
        with patch.object(sdk.importlib, "import_module", side_effect=ModuleNotFoundError("missing", name="context_overlay")):
            client = sdk.Client()  # Constructor is safe even without the dependency.
            with self.assertRaises(sdk.ContextOverlayError) as caught:
                client.get_api_info()
            self.assertEqual(caught.exception.code, "dependency_missing")
        with patch.object(api, "get_api_info", return_value={"api_version": "2.0.0", "schema_version": "1"}):
            with self.assertRaises(sdk.ContextOverlayError) as caught:
                sdk.Client(api).get_context()
            self.assertEqual(caught.exception.code, "incompatible_api")
        with self.assertRaises(sdk.ContextOverlayError) as caught:
            sdk.Client(api).get_context(history_limit=True)
        self.assertEqual(caught.exception.code, "invalid_request")
        json.dumps(caught.exception.to_dict())

    def test_sdk_cleans_up_on_consumer_exception_and_expires_with_clear_error(self):
        with self.assertRaisesRegex(ValueError, "consumer failed"):
            with sdk.Client(api).history():
                raise ValueError("consumer failed")
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)
        self.add_events()
        query = sdk.Client(api).history(page_size=1)
        self.now[0] = 121
        with self.assertRaises(sdk.ContextOverlayError) as caught:
            query.next_page()
        self.assertEqual(caught.exception.code, "cursor_expired")
        query.close()
        self.assertTrue(query.closed)

    def test_sdk_accepts_compatible_minor_and_reacquires_replaced_runtime(self):
        provider = SimpleNamespace(APIError=api.APIError, get_context=api.get_context,
            get_api_info=lambda: {"api_version": "1.9.0", "module_version": "9.0.0", "schema_version": "1"})
        client = sdk.Client(provider)
        self.assertEqual(client.get_context(include_history=False)["session_id"], "run-a")
        replacement = SimpleNamespace(**vars(self.runtime))
        replacement.session_id = "replacement-run"
        replacement.recorder = Recorder(MemoryJournal(), session_id=replacement.session_id)
        replacement.collector = Collector(self.adapter, replacement.recorder)
        with patch.object(game_runtime, "_runtime", replacement):
            self.assertEqual(client.get_context(include_history=False)["session_id"], "replacement-run")
        with patch.object(sdk.importlib, "import_module", side_effect=ModuleNotFoundError("old provider", name="context_overlay.api")):
            with self.assertRaises(sdk.ContextOverlayError) as caught:
                sdk.Client().get_api_info()
            self.assertEqual(caught.exception.code, "incompatible_api")
