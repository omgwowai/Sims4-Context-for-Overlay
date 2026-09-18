"""External writes and incremental reads exercised through real public endpoints."""

import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from support import ROOT, MemoryJournal, facts, runtime_fixture
from context_overlay import api
from context_overlay.external import LIMITS
from context_overlay.recorder import Recorder
from context_overlay.storage import Journal, StorageBusy
from context_overlay.semanticizer import explain_event, resource_details
from context_overlay.inspector import InspectorSession, event_label
from offline import NameCatalog, read_packet
from translate import markdown_report
import sys
sys.path.insert(0, str(ROOT / "sdk"))
from context_overlay_client import Client, ContextOverlayError


class ExternalChecks(unittest.TestCase):
    def setUp(self):
        self.clock = [0]
        self.rec = Recorder(MemoryJournal(), "external-run", clock=lambda: self.clock[0])
        self.runtime = runtime_fixture(self, recorder=self.rec)
        self.sid = self.runtime.session_id
        self.entity = facts()["actor"]["key"]
        self.rec.external.clock = lambda: self.clock[0]
        self.rec.external.updated = 0

    def write(self, payload=None, producer="test.overlay", **options):
        return api.append_event(producer, payload, expected_session_id=self.sid, **options)

    def error(self, code, function, *args, **kwargs):
        with self.assertRaises(api.APIError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def query(self, **options):
        packet = api.query_history(None, None, expected_session_id=self.sid, **options)
        api.close_history(packet["history"]["cursor"], expected_session_id=self.sid)
        return packet

    def changes(self, checkpoint=None, **options):
        packet = api.read_event_changes(checkpoint, expected_session_id=self.sid, **options)
        api.close_history(packet["history"]["cursor"], expected_session_id=self.sid)
        return packet["history"]

    def test_json_roundtrip_detachment_and_system_fields(self):
        for payload in [None, True, 42, 2.25, "中文\n台词", [1, None, {"b": "话"}],
                        {"status": "error", "event_id": "override", "origin": "game", "producer": "spoof"}]:
            original = copy.deepcopy(payload)
            receipt = self.write(payload)
            if isinstance(payload, dict):
                payload["changed"] = True
            result = self.query()["history"]["events"][0]
            self.assertEqual(result["payload"], original)
            self.assertEqual(result["event_id"], receipt["event_id"])
            self.assertEqual(result["origin"], "external")
            self.assertEqual(result["producer"], "test.overlay")
            result["payload"] = "changed"
            self.assertEqual(self.query()["history"]["events"][0]["payload"], original)

    def test_mixed_sources_context_global_and_producer_filters(self):
        self.rec.interaction("started", facts(), 10, "game_hook")
        self.write("linked", entities=[self.entity])
        self.write("global", producer="another.overlay")
        self.assertEqual(self.query()["history"]["total_matches"], 3)
        self.assertEqual(self.query(origins=["game"])["history"]["total_matches"], 1)
        self.assertEqual(self.query(producers=["another.overlay"])["history"]["events"][0]["payload"], "global")
        self.assertEqual(self.query(origins=["game"], producers=["test.overlay"])["history"]["events"], [])
        self.assertEqual(self.query(origins=["external"], event_types=["external_event"])["history"]["total_matches"], 2)
        self.assertEqual(len(api.get_context()["history"]["events"]), 2)
        self.assertEqual(len(api.get_context(origins=["game"])["history"]["events"]), 1)
        self.error("invalid_query", api.get_context, origins=[])
        self.error("invalid_query", self.query, producers=["test.overlay", "test.overlay"])
        self.error("invalid_request", api.query_history, None, "active")

    def test_entity_associations_not_observation_or_identity_overwrite(self):
        self.rec.enter(facts()["actor"], 10)
        original = copy.deepcopy(self.rec.references[self.entity])
        receipt = self.write({}, entities=[self.entity, "object:00077", "object:77", "sim:987"])
        result = self.rec.events[receipt["event_id"]]
        self.assertEqual(len(result["entities"]), 3)
        self.assertEqual(self.rec.references[self.entity], original)
        self.assertNotIn("sim:987", self.rec.references)
        self.assertNotIn("sim:987", self.rec._scopes)
        with patch.object(self.runtime.adapter, "resolve", side_effect=AssertionError("No live resolution")):
            page = api.query_history("sim", "987")
        self.assertEqual(page["target"]["identity_status"], "unverified")
        self.assertEqual(page["history"]["target_observation"]["status"], "not_observed")
        api.close_history(page["history"]["cursor"], expected_session_id=self.sid)

    def test_retry_conflict_fifo_and_namespace_isolation(self):
        self.rec.index.capacity = 1
        first = self.write({"a": 1, "b": 2}, entities=["sim:02", "sim:1"], idempotency_key="key")
        same = self.write({"b": 2, "a": 1}, entities=["sim:1", "sim:2"], idempotency_key="key")
        self.assertEqual(first["event_id"], same["event_id"])
        self.assertTrue(same["duplicate"])
        self.assertEqual(len(self.rec.journal.records), 1)
        self.error("idempotency_conflict", self.write, {"a": 3}, idempotency_key="key")
        other = self.write({}, producer="another.overlay", idempotency_key="key")
        self.assertNotEqual(first["event_id"], other["event_id"])
        retry = self.write({"a": 1, "b": 2}, entities=["sim:1", "sim:2"], idempotency_key="key")
        self.assertFalse(retry["retained"])
        self.assertEqual(len(self.rec.journal.records), 2)
        self.assertNotIn(first["event_id"], self.rec.events)

    def test_invalid_payloads_are_recoverable(self):
        loop = []; loop.append(loop)
        deep = None
        for _ in range(18):
            deep = [deep]
        for payload in [float("nan"), float("inf"), {1: "key"}, object(), (1, 2), loop, "\ud800"]:
            self.error("invalid_request", self.write, payload)
        for payload in [deep, "a" * 65537, "汉" * 30000, [0] * 32769]:
            self.error("payload_limit", self.write, payload)
        self.error("invalid_request", self.write, {}, entities=["sim:18446744073709551616"])
        self.error("invalid_request", self.write, {}, producer="bad producer")
        self.assertEqual(self.rec.status()["state"], "recording")
        self.assertEqual(self.rec.journal.records, [])
        self.assertFalse(self.write({})["duplicate"])

    def test_rate_and_dedup_limits_preserve_game_capture(self):
        with patch.dict(LIMITS, dedup_keys=1):
            self.write({}, idempotency_key="one")
            self.error("dedup_capacity", self.write, {}, idempotency_key="two")
            self.assertTrue(self.write({}, idempotency_key="one")["duplicate"])
        self.rec.external.tokens = 0
        exc = self.error("rate_limited", self.write, {})
        self.assertGreater(exc.details["retry_after_seconds"], 0)
        self.assertIsNotNone(self.rec.interaction("started", facts(), 10, "game_hook"))
        self.clock[0] += 1
        self.assertFalse(self.write({})["duplicate"])

    def test_memory_rejection_and_journal_busy_do_not_publish_or_pause(self):
        budget = self.rec.index.memory_limit
        self.rec.index.memory_limit = 1
        self.error("write_busy", self.write, {})
        self.rec.index.memory_limit = budget
        with patch.object(self.rec.journal, "append", side_effect=StorageBusy("busy")):
            self.error("write_busy", self.write, {})
        self.assertEqual(self.rec.status()["state"], "recording")
        self.assertEqual(len(self.rec.index.global_index.by_id), 0)
        self.assertEqual(len(self.rec.external.keys), 0)
        self.assertFalse(self.write({}, idempotency_key="ok")["duplicate"])

    def test_thread_session_and_disabled_guards(self):
        self.error("session_changed", api.append_event, "test", {}, expected_session_id="old")
        self.error("invalid_request", api.append_event, "test", {})
        codes = []
        def worker():
            for call in [lambda: self.write({}), lambda: self.changes()]:
                try:
                    call()
                except api.APIError as exc:
                    codes.append(exc.code)
        thread = threading.Thread(target=worker); thread.start(); thread.join()
        self.assertEqual(codes, ["wrong_thread", "wrong_thread"])
        self.rec.enabled = False
        self.error("recorder_disabled", self.write, {})
        self.error("recorder_disabled", self.changes)
        self.assertEqual(len(self.rec.journal.records), 0)

    def test_retained_snapshot_to_delta_and_inflight_revisions(self):
        first = self.rec.interaction("queued", facts(1), 10, "hook")
        self.rec.interaction("started", facts(1), 11, "hook")
        self.write("first")
        client = Client()
        with client.changes(start="retained", page_size=1, expected_session_id=self.sid) as batch:
            self.assertIsNone(batch.checkpoint)
            self.assertEqual(batch.page["history"]["events"][0]["revision"], 2)
            self.rec.interaction("exited", dict(facts(1), finishing_type="NATURAL"), 12, "hook")
            self.write("later")
            batch.next_page()
            self.assertEqual(batch.page["history"]["events"][0]["payload"], "first")
            checkpoint = batch.checkpoint
            self.assertIsNotNone(checkpoint)
            batch.page["history"]["checkpoint"] = "tampered"
            self.assertEqual(batch.checkpoint, checkpoint)
        delta = self.changes(checkpoint)
        self.assertEqual(len(delta["events"]), 2)
        self.assertEqual(delta["events"][0]["event_id"], first["event_id"])
        self.assertEqual(delta["events"][0]["revision"], 3)
        self.assertEqual(self.changes(delta["checkpoint"])["events"], [])
        self.assertEqual(self.rec.index.status()["snapshots"], 0)

    def test_filtered_empty_progress_and_checkpoint_validation(self):
        initial = self.changes(start="now", producers=["test.overlay"])
        self.write("other", producer="another.overlay")
        empty = self.changes(initial["checkpoint"])
        self.assertEqual(empty["events"], [])
        self.assertGreater(empty["change_range"]["through_sequence"], initial["change_range"]["through_sequence"])
        self.write("match")
        self.assertEqual(len(self.changes(empty["checkpoint"])["events"]), 1)
        self.error("invalid_request", self.changes, empty["checkpoint"], producers=["test.overlay"])
        self.error("invalid_checkpoint", self.changes, "invalid")
        self.error("invalid_checkpoint", self.changes, "a.b")
        checkpoint = empty["checkpoint"]
        self.error("invalid_checkpoint", self.changes, checkpoint[:-1] + ("0" if checkpoint[-1] != "0" else "1"))
        self.rec.index.session_id = "changed"
        self.error("session_changed", self.changes, checkpoint)

    def test_updated_old_event_eviction_reports_gap_and_indexes_cleanup(self):
        self.rec.index.capacity = 2
        self.rec.interaction("queued", facts(1), 10, "hook")
        self.write({}, producer="will.disappear")
        initial = self.changes(start="now")
        self.rec.interaction("started", facts(1), 11, "hook")
        self.write({}, producer="keep")
        self.error("history_gap", self.changes, initial["checkpoint"])
        self.write({}, producer="keep")
        self.assertNotIn("will.disappear", self.rec.index.producers)
        self.assertEqual(len(self.rec.index.global_index.by_id), 2)
        reset = self.changes(start="retained")
        self.assertEqual(len(reset["events"]), 2)

    def test_page_retry_expiry_and_budget_failure(self):
        for value in range(3):
            self.write(value)
        packet = api.read_event_changes(start="retained", page_size=1, expected_session_id=self.sid)
        cursor = packet["history"]["next_cursor"]
        a = api.get_history_page(cursor, expected_session_id=self.sid)["history"]
        self.write("concurrent")
        b = api.get_history_page(cursor, expected_session_id=self.sid)["history"]
        self.assertEqual(a["events"], b["events"])
        self.assertIsNone(a["checkpoint"])
        self.clock[0] = 121
        self.error("cursor_expired", api.get_history_page, cursor, expected_session_id=self.sid)
        self.rec.index.snapshot_ref_limit = 1
        self.error("query_budget", self.changes, start="retained")
        self.assertEqual(self.rec.index.status()["snapshots"], 0)

    def test_external_payload_is_opaque_in_rendering_offline_and_window(self):
        payload = {"tuning_id": "1", "tuning_name": "fake", "status": "error",
                   "description": {"text": "private", "status": "resolved", "source": {"token_binding": 7}},
                   "reference_semantics": {"fields": "not game data"}}
        self.write(payload, entities=[self.entity])
        packet = self.query()
        record = packet["history"]["events"][0]
        self.assertEqual(resource_details(packet)["items"], [])
        fake_catalog = NameCatalog.__new__(NameCatalog)
        with patch.object(fake_catalog, "resolve", side_effect=AssertionError("Must not interpret payload")):
            self.assertEqual(fake_catalog.enrich(packet)["history"]["events"][0]["payload"], payload)
        self.assertEqual(json.loads(explain_event(record)["payload_json"]), payload)
        self.assertIn("test.overlay", event_label(record))
        session = InspectorSession.__new__(InspectorSession)
        captured = []
        session.text_page = lambda title, text, back: captured.append(text)
        session.event_details(record, None)
        self.assertIn('"tuning_name": "fake"', captured[0])
        output = markdown_report(packet, Path("synthetic.json"))
        self.assertIn("外部 JSON 内容", output)
        self.assertIn("private", output)

    def test_real_journal_durability_and_nonfatal_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(directory, max_bytes=1)
            rec = Recorder(journal, "disk-run")
            runtime_fixture(self, recorder=rec)
            try:
                self.error("write_busy", api.append_event, "test", {}, expected_session_id="disk-run")
                self.assertIsNone(journal.status()["error"])
                journal._max_bytes = 1024 * 1024
                with journal._lock:
                    receipt = api.append_event("test", ["中文", None], expected_session_id="disk-run", idempotency_key="one")
                    self.assertEqual(receipt["persistence"], "accepted")
                journal.flush()
                retry = api.append_event("test", ["中文", None], expected_session_id="disk-run", idempotency_key="one")
                self.assertEqual(retry["persistence"], "written")
            finally:
                journal.close(wait=True)
            packet, _ = read_packet(Path(directory) / "journal.jsonl")
            self.assertEqual(packet["schema_version"], "2")
            self.assertEqual(packet["history"]["events"][0]["payload"], ["中文", None])

    def test_user_probe_and_after_session_verification(self):
        from context_overlay.api_probe import run, verify
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.json"
            result = run(path)
            self.assertTrue(result["passed"], result)
            self.assertEqual(result["checks"], 9)
            self.assertEqual(self.rec.index.status()["snapshots"], 0)
            self.assertTrue(verify(path)["passed"])
            replacement = Recorder(MemoryJournal(), "new-session")
            runtime_fixture(self, recorder=replacement)
            result = verify(path)
            self.assertTrue(result["passed"], result)
            self.assertEqual(replacement.journal.records, [])

    def test_example_reader_retries_handler_without_committing_and_closes(self):
        import importlib.util
        import types
        import context_overlay_client
        aliases = {"my_overlay_mod": types.ModuleType("my_overlay_mod"),
                   "my_overlay_mod.vendor": types.ModuleType("my_overlay_mod.vendor"),
                   "my_overlay_mod.vendor.context_overlay_client": context_overlay_client}
        spec = importlib.util.spec_from_file_location("sample_overlay", str(ROOT / "sdk/examples/overlay_events.py"))
        sample = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, aliases):
            spec.loader.exec_module(sample)
        self.write({"first": True})
        reader = sample.IncrementalReader(self.sid, producers=["test.overlay"])
        handled = []
        def fail_once(events):
            raise ValueError("consumer failure")
        with self.assertRaisesRegex(ValueError, "consumer failure"):
            reader.step(fail_once)
        self.assertIsNone(reader.checkpoint)
        self.assertTrue(reader.step(lambda events: handled.extend(events)))
        saved_checkpoint = reader.checkpoint
        self.write({"later": True})
        self.assertTrue(reader.step(lambda events: handled.extend(events)))
        self.assertEqual(len(handled), 2)
        self.assertNotEqual(reader.checkpoint, saved_checkpoint)
        self.assertEqual(self.rec.index.status()["snapshots"], 0)
        reader.reset("now")
        self.assertTrue(reader.step(lambda events: self.assertEqual(events, [])))
        reader.close()

    def test_updated_associations_and_dedup_byte_budget(self):
        action = self.rec.interaction("queued", facts(1, target_id=1), 10, "hook")
        initial = self.changes(start="now", kind="object", identifier="2")
        self.rec.interaction("started", facts(1, target_id=2), 11, "hook")
        delta = self.changes(initial["checkpoint"])
        self.assertEqual([e["event_id"] for e in delta["events"]], [action["event_id"]])
        with patch.dict(LIMITS, dedup_bytes=1):
            self.error("dedup_capacity", self.write, {}, idempotency_key="new")
        self.assertEqual(self.rec.status()["state"], "recording")

    def test_sdk_rejects_v1_and_checks_write_capability(self):
        from types import SimpleNamespace
        for major, schema, expected in [("1.1.0", "1", "incompatible_api"), ("2.0.0", "2", "capability_unavailable")]:
            provider = SimpleNamespace(get_api_info=lambda: {"api_version": major, "schema_version": schema, "capabilities": []})
            with self.assertRaises(ContextOverlayError) as caught:
                Client(provider).append_event("test", {}, expected_session_id=self.sid)
            self.assertEqual(caught.exception.code, expected)


if __name__ == "__main__":
    unittest.main()
