"""Cross-layer integrity, lifecycle and SDK checks against real durable logs."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import ROOT, runtime_fixture
from test_experience_view import action
from test_filter_events import event
from context_overlay import api
from context_overlay.event_views import ViewStore
from context_overlay.recorder import Recorder
from context_overlay.storage import Journal
from context_overlay.view_source import ViewError, read_prefix, packed


class EventViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "journal.jsonl"
        self.rows = []
        self.add_record(kind="observation", category="session_start", data={})
        queued = action("eat", ("13433", "generic_consume_food"), 1000, 2000)
        queued.update(revision=1, started_time=None, ended_time=None, observations=[{"phase": "queued", "game_time": {"ticks": "100"}}])
        self.add_record(kind="event_revision", event=queued)
        started = copy.deepcopy(queued)
        started.update(revision=2, started_time={"ticks": "1000"})
        self.add_record(kind="event_revision", event=started)
        finished = copy.deepcopy(started)
        finished.update(revision=3, ended_time={"ticks": "2000"})
        self.add_record(kind="event_revision", event=finished)
        goal = event("internal", "aspiration.goal_completed", {"aspiration_type": "FULL_ASPIRATION",
                     "aspiration": {"id": "33188", "tuning_name": "aspiration_Utility_AllChannelsStations"}})
        self.add_record(kind="event_revision", event=goal, evicted_event_ids=["run:eat"])
        self.store = self.new_store()

    def add_record(self, **values):
        self.rows.append(dict(values, session_id="run", sequence=len(self.rows) + 1))
        self.path.write_bytes(b"".join(packed(row) + b"\n" for row in self.rows))

    def head(self):
        return {"durable_sequence": len(self.rows), "durable_byte_offset": self.path.stat().st_size,
                "accepted_sequence": len(self.rows), "error": None}

    def new_store(self, **options):
        store = ViewStore(self.path, "run", "1.126.73.1030", **options)
        self.addCleanup(lambda: store.shutdown(wait=True))
        return store

    def ready(self, request, store=None):
        store = store or self.store
        deadline = time.monotonic() + 10
        while request["state"] == "building" and time.monotonic() < deadline:
            time.sleep(0.005)
            request = store.status(request["request_id"])
        self.assertEqual(request["state"], "ready", request)
        return request

    def items(self, request, store=None):
        store = store or self.store
        status = self.ready(request, store)
        cursor, items = status["cursor"], []
        while cursor:
            page = store.page(cursor)
            items.extend(page["items"])
            cursor = page["next_cursor"]
        self.assertEqual(len(items), status["total_matches"])
        return status, items

    def test_four_layers_conserve_sources_and_expose_revision_chain(self):
        records, raw = self.items(self.store.query("records", "sim:1", self.head(), page_size=2))
        self.assertEqual(len(raw), 5)  # Four revisions plus one shared session boundary.
        source = records["source_snapshot_id"]
        latest, events = self.items(self.store.query("events", "sim:1", self.head(), source))
        self.assertEqual(len(events), 2)  # FIFO eviction is deliberately not applied.
        self.assertEqual(events[0]["event"]["revision"], 3)
        organized, units = self.items(self.store.query("organized", "sim:1", self.head(), source))
        self.assertEqual([row["kind"] for row in units], ["unit", "standalone"])
        self.assertEqual(units[1]["event_id"], "run:internal")
        recap, rows = self.items(self.store.query("recap", "sim:1", self.head(), source))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["value"]["queued_at"], "tick:100")
        self.assertEqual(row["value"]["time"], ["tick:1000", "tick:2000"])
        revisions, chain = self.items(self.store.explain(recap["snapshot_id"], row["item_id"], "revisions"))
        self.assertEqual([r["event"]["revision"] for r in chain], [1, 2, 3])
        _, lineage = self.items(self.store.explain(organized["snapshot_id"], units[1]["item_id"], "lineage"))
        self.assertEqual(lineage[0]["event_id"], "run:internal")
        with self.assertRaisesRegex(ViewError, "does not belong"):
            self.store.explain(recap["snapshot_id"], units[1]["item_id"])

    def test_append_and_source_file_removal_do_not_change_ready_pages(self):
        request, before = self.items(self.store.query("events", "sim:1", self.head(), page_size=1))
        cursor = request["cursor"]
        next_event = copy.deepcopy(self.rows[3]["event"])
        next_event["revision"] = 4
        self.add_record(kind="event_revision", event=next_event)
        self.assertEqual(self.store.page(cursor)["items"], before[:1])
        old, old_rows = self.items(self.store.query("records", "sim:1", self.head(), request["source_snapshot_id"]))
        self.assertEqual(len(old_rows), 5)
        new, new_rows = self.items(self.store.query("events", "sim:1", self.head()))
        self.assertEqual(new_rows[0]["event"]["revision"], 4)
        self.assertNotEqual(new["snapshot_id"], request["snapshot_id"])
        self.path.unlink()
        self.assertEqual(self.store.page(cursor)["items"], before[:1])
        before[0]["event"]["revision"] = 999
        self.assertEqual(self.store.page(cursor)["items"][0]["event"]["revision"], 3)

    def test_corruption_and_conflicting_retries_are_rejected(self):
        valid = copy.deepcopy(self.rows)
        cases = []
        for field, value in (("sequence", 99), ("session_id", "other")):
            changed = copy.deepcopy(valid)
            changed[2][field] = value
            cases.append(changed)
        changed = copy.deepcopy(valid)
        changed[2]["event"]["revision"] = 5
        cases.append(changed)
        changed = copy.deepcopy(valid)
        changed.insert(2, dict(changed[1], event=dict(changed[1]["event"], revision=55)))
        cases.append(changed)
        for rows in cases:
            self.path.write_bytes(b"".join(packed(row) + b"\n" for row in rows))
            with self.assertRaises(ViewError):
                read_prefix(self.path, "run", 5, self.path.stat().st_size, lambda: None, 1024 ** 2)
        valid.insert(2, valid[1])
        self.path.write_bytes(b"".join(packed(row) + b"\n" for row in valid))
        result = read_prefix(self.path, "run", 5, self.path.stat().st_size, lambda: None, 1024 ** 2)
        self.assertEqual(len(result["records"]), 5)
        self.path.write_bytes(self.path.read_bytes()[:-1])
        with self.assertRaises(ViewError):
            read_prefix(self.path, "run", 5, self.path.stat().st_size, lambda: None, 1024 ** 2)

    def test_budget_cancel_expiry_and_invalid_cursor(self):
        tiny = self.new_store(memory_bytes=20)
        request = tiny.query("events", "sim:1", self.head())
        deadline = time.monotonic() + 3
        while request["state"] == "building" and time.monotonic() < deadline:
            time.sleep(0.005)
            request = tiny.status(request["request_id"])
        self.assertEqual(request["error"]["code"], "view_budget")
        status = self.ready(self.store.query("events", "sim:1", self.head(), page_size=2))
        with self.assertRaises(ViewError):
            self.store.page(status["request_id"] + ":1")
        self.store.close(status["request_id"])
        with self.assertRaises(ViewError):
            self.store.status(status["request_id"])
        expiry = self.new_store(ttl=0.03)
        request = expiry.query("events", "sim:1", self.head())
        time.sleep(0.06)
        with self.assertRaises(ViewError):
            expiry.status(request["request_id"])

    def test_cancelled_shared_build_does_not_break_other_request_and_releases_source(self):
        entered, resume = threading.Event(), threading.Event()
        from context_overlay import event_views
        original = event_views.read_prefix
        def delayed(*args, **kwargs):
            entered.set()
            self.assertTrue(resume.wait(3))
            return original(*args, **kwargs)
        with patch.object(event_views, "read_prefix", side_effect=delayed):
            first = self.store.query("records", "sim:1", self.head())
            self.assertTrue(entered.wait(3))
            second = self.store.query("events", "sim:1", self.head(), first["source_snapshot_id"])
            self.store.close(first["request_id"])
            resume.set()
            status, rows = self.items(second)
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.store.metrics()["sources"], 1)
        self.store.close(status["request_id"])
        with self.assertRaises(ViewError):
            self.store.query("events", "sim:1", self.head(), status["source_snapshot_id"])
        deadline = time.monotonic() + 3
        while self.store.metrics()["sources"] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.store.metrics()["estimated_bytes"], 0)

    def test_unsupported_modes_and_source_item_policy(self):
        for values in ({"view": "unknown"}, {"profile": "unimplemented"}, {"page_size": True}, {"page_size": 101}):
            options = dict(view="events", entity_key="sim:1", head=self.head())
            options.update(values)
            with self.assertRaises(ViewError):
                self.store.query(**options)
        status, rows = self.items(self.store.query("events", "sim:1", self.head()))
        _, policy = self.items(self.store.explain(status["snapshot_id"], "run:eat", "policy"))
        self.assertEqual(policy[0]["placement"], "recap")
        with self.assertRaises(ViewError):
            self.store.explain("0" * 64, "run:eat")

    def test_records_keep_scope_observations_without_logical_events(self):
        self.add_record(kind="observation", category="scope_entry", data={"target": {"key": "sim:3"}})
        status, rows = self.items(self.store.query("records", "sim:3", self.head()))
        self.assertEqual([row["record"]["category"] for row in rows], ["session_start", "scope_entry"])
        for row in rows:
            _, evidence = self.items(self.store.explain(status["snapshot_id"], row["item_id"], "revisions"))
            self.assertEqual(evidence, [row["record"]])
        for facet in ("lineage", "events", "units", "labels", "policy"):
            _, evidence = self.items(self.store.explain(status["snapshot_id"], rows[1]["item_id"], facet))
            self.assertEqual(evidence, [])  # Scope observations do not invent logical events or rules.

    def test_opt_in_frame_probe_is_bounded_and_restores_hook(self):
        from context_overlay.test_driver import Driver
        class Zone:
            def update(self, ticks):
                return ticks
        original = Zone.update
        driver = Driver.__new__(Driver)
        driver.probe = None
        now = [100.0]
        with patch.dict("sys.modules", {"zone": SimpleNamespace(Zone=Zone)}), patch("context_overlay.test_driver.time.perf_counter", side_effect=lambda: now[0]):
            with self.assertRaises(ValueError):
                driver.frame_probe(61)
            driver.frame_probe(1)
            instance = Zone()
            self.assertEqual(instance.update(1), 1)
            now[0] += .02
            instance.update(2)
            now[0] += 2
            result = driver.frame_probe()
            self.assertEqual(result["samples"], 1)
            self.assertEqual(result["interval_ms"]["max"], 20)
            self.assertEqual(result["state"], "complete")
            self.assertIs(Zone.update, original)

    def test_api_sdk_use_historical_id_without_resolving_game_objects(self):
        journal = Journal(Path(self.temp.name) / "live", reserve_bytes=0)
        self.addCleanup(lambda: journal.close(wait=True))
        for row in self.rows:
            journal.append({k: v for k, v in row.items() if k != "sequence"})
        journal.flush()
        self.assertEqual(journal.status()["durable_byte_offset"], journal.path.stat().st_size)
        recorder = Recorder(journal, session_id="run")
        runtime = runtime_fixture(self, recorder=recorder, provenance={"build_game_version": "1.126.73.1030"})
        runtime.writer = journal
        self.addCleanup(lambda: runtime.event_views.shutdown(wait=True) if runtime.event_views else None)
        runtime.event_views = None
        spec = importlib.util.spec_from_file_location("view_test_sdk", str(ROOT / "sdk/context_overlay_client.py"))
        sdk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sdk)
        client = sdk.Client()
        with patch.object(runtime.adapter, "resolve", side_effect=AssertionError("No live lookup for historical IDs")):
            request = client.query_event_view("events", identifier="1", expected_session_id="run")
        while request["state"] == "building":
            time.sleep(0.005)
            request = client.get_event_view_status(request["request_id"], expected_session_id="run")
        self.assertEqual(request["state"], "ready", request)
        self.assertEqual(len(client.get_event_view_page(request["cursor"], expected_session_id="run")["items"]), 2)
        with self.assertRaises(sdk.ContextOverlayError) as caught:
            client.get_event_view_page(request["cursor"], expected_session_id="other")
        self.assertEqual(caught.exception.code, "session_changed")
        errors = []
        def worker():
            try:
                client.get_event_view_status(request["request_id"], expected_session_id="run")
            except sdk.ContextOverlayError as exc:
                errors.append(exc.code)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        self.assertEqual(errors, ["wrong_thread"])
        runtime.closed, runtime.history_suspended = True, True
        self.assertTrue(client.close_event_view(request["request_id"], expected_session_id="run")["released"])
