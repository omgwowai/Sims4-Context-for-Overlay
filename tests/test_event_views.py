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
from context_overlay import api, game_runtime
from context_overlay.event_views import ViewStore
from context_overlay.recorder import Recorder
from context_overlay.storage import Journal
from context_overlay.view_source import ViewError, read_prefix, packed, LatestEvents


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

    def test_four_open_layers_share_raw_bytes_without_relaxing_memory_budget(self):
        self.rows = []
        self.add_record(kind="observation", category="session_start", data={})
        for i in range(6):
            row = action("large_" + str(i), ("13094", "bed_sleep"), 1000 + i, 2000 + i)
            row["facts"]["test_padding"] = "x" * 150000
            for revision in (1, 2, 3):
                self.add_record(kind="event_revision", event=dict(row, revision=revision))
        store = self.new_store(memory_bytes=6 * 1024 ** 2)
        source = None
        results = {}
        for layer in ("records", "events", "organized", "recap"):
            req, rows = self.items(store.query(layer, "sim:1", self.head(), source, page_size=2), store)
            source = req["source_snapshot_id"]
            results[layer] = (req, rows)
        self.assertEqual([len(results[layer][1]) for layer in ("records", "events", "organized", "recap")],
                         [19, 6, 6, 6])
        self.assertLess(store.metrics()["estimated_bytes"], store.metrics()["memory_budget_bytes"])
        self.path.unlink()
        results["events"][1][0]["event"]["facts"]["test_padding"] = "changed"
        page = store.page(results["events"][0]["cursor"])
        self.assertEqual(page["items"][0]["event"]["facts"]["test_padding"], "x" * 150000)
        raw = store.page(results["records"][0]["cursor"])["items"]
        self.assertEqual(raw, results["records"][1][:2])

    def test_long_revision_history_over_128_mib_queries_and_exports_by_default(self):
        from context_overlay.run_artifacts import RunArtifacts
        # Many large revisions can leave a small latest-event set. Source file
        # size alone should not prevent reading any of the four layers.
        latest = self.rows[3]["event"]
        facts = dict(latest["facts"], test_padding="x" * 300000)
        for revision in range(4, 464):
            self.rows.append(dict(session_id="run", sequence=len(self.rows) + 1,
                kind="event_revision", event=dict(latest, revision=revision, facts=facts)))
        self.rows.append(dict(session_id="run", sequence=len(self.rows) + 1,
            kind="event_revision", event=dict(latest, revision=464)))
        with self.path.open("wb") as stream:
            for row in self.rows:
                stream.write(packed(row) + b"\n")
        self.assertGreater(self.path.stat().st_size, 128 * 1024 ** 2)
        source, organized = None, None
        for view, count in (("records", len(self.rows)), ("events", 2), ("organized", 2), ("recap", 1)):
            status = self.ready(self.store.query(view, "sim:1", self.head(), source))
            source = status["source_snapshot_id"]
            cursor, received = status["cursor"], 0
            while cursor:
                page = self.store.page(cursor)
                received += len(page["items"])
                if view == "organized":
                    organized = page["items"]
                cursor = page["next_cursor"]
            self.assertEqual(received, count)
            self.assertEqual(status["total_matches"], count)
        exporter = RunArtifacts(self.path.parent, "run", "1.126.73.1030")
        result = exporter.finish(self.head(), {"sim:1": "Test"}, {"capture_complete": None})
        self.assertEqual(result["state"], "ready", result)
        snapshot = self.path.parent / "views" / result["directory"]
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf8"))
        self.assertEqual(manifest["source"]["records"], len(self.rows))
        self.assertEqual(manifest["source"]["byte_offset"], self.path.stat().st_size)
        exported = [json.loads(line) for line in (snapshot / "sim-1/organized.jsonl").read_text(encoding="utf8").splitlines()]
        self.assertEqual(exported, organized)

    def test_retired_source_config_is_ignored_without_ignoring_other_fields(self):
        root = self.path.parent
        config = root / "config.json"
        config.write_text(json.dumps({"event_view_source_mb": 1, "event_view_memory_mb": 512}), encoding="utf8")
        with patch.object(game_runtime, "data_root", return_value=root):
            loaded = game_runtime.load_config()
            self.assertNotIn("event_view_source_mb", loaded)
            self.assertEqual(loaded["event_view_memory_mb"], 512)
            config.write_text(json.dumps({"event_view_unknown": 1}), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "Unknown configuration field"):
                game_runtime.load_config()

    def test_source_backed_items_still_obey_encoded_page_budget(self):
        row = action("wide", ("13094", "bed_sleep"))
        row["facts"]["test_padding"] = "x" * 20000
        self.add_record(kind="event_revision", event=dict(row, revision=1))
        store = self.new_store(page_bytes=6000)
        req = store.query("records", "sim:1", self.head())
        deadline = time.monotonic() + 3
        while req["state"] == "building" and time.monotonic() < deadline:
            time.sleep(.005)
            req = store.status(req["request_id"])
        self.assertEqual(req["state"], "failed")
        self.assertEqual(req["error"]["code"], "view_budget")
        self.assertIn("One item exceeds", req["error"]["message"])

    def test_complete_page_encoding_including_envelope_obeys_budget(self):
        row = action("sleep", ("13094", "bed_sleep"), 3000, 4000)
        row["facts"]["test_padding"] = "中文内容" * 200
        self.add_record(kind="event_revision", event=dict(row, revision=1))
        for layer in ("records", "events", "organized", "recap", "revisions"):
            with self.subTest(layer=layer):
                def request(store):
                    if layer != "revisions":
                        return store.query(layer, "sim:1", self.head(), page_size=100)
                    # Prepare the parent independently of the explanation's budget.
                    with patch.object(store, "page_bytes", 512 * 1024):
                        parent = self.ready(store.query("events", "sim:1", self.head()), store)
                    return store.explain(parent["snapshot_id"], "run:eat", "revisions", page_size=100)
                baseline = self.new_store()
                status, expected = self.items(request(baseline), baseline)
                first = baseline.page(status["cursor"])
                self.assertGreater(len(first["items"]), 1)
                budget = len(packed(first)) - 1
                store = self.new_store(page_bytes=budget)
                status = self.ready(request(store), store)
                cursor, actual, pages = status["cursor"], [], 0
                while cursor:
                    page = store.page(cursor)
                    self.assertLessEqual(len(packed(page)), budget)
                    actual.extend(page["items"])
                    pages += 1
                    cursor = page["next_cursor"]
                self.assertGreater(pages, 1)
                self.assertEqual(actual, expected)

    def test_empty_and_single_item_pages_count_the_complete_header(self):
        for empty in (False, True):
            with self.subTest(empty=empty):
                def request(store):
                    if not empty:
                        return store.query("recap", "sim:1", self.head())
                    with patch.object(store, "page_bytes", 512 * 1024):
                        parent = self.ready(store.query("records", "sim:1", self.head()), store)
                    return store.explain(parent["snapshot_id"], "record:1", "units")
                baseline = self.new_store()
                status, expected = self.items(request(baseline), baseline)
                self.assertEqual(len(expected), 0 if empty else 1)
                budget = len(packed(baseline.page(status["cursor"])))
                exact = self.new_store(page_bytes=budget)
                exact_status, actual = self.items(request(exact), exact)
                self.assertEqual(actual, expected)
                self.assertEqual(len(packed(exact.page(exact_status["cursor"]))), budget)
                small = self.new_store(page_bytes=budget - 1)
                failed = request(small)
                deadline = time.monotonic() + 3
                while failed["state"] == "building" and time.monotonic() < deadline:
                    time.sleep(.005)
                    failed = small.status(failed["request_id"])
                self.assertEqual(failed["state"], "failed")
                self.assertEqual(failed["error"]["code"], "view_budget")

    def shared_people(self):
        for record in self.rows:
            if record['kind'] == 'event_revision':
                record['event']['entities'].append('sim:2')
                record['event'].setdefault('facts', {})['test_padding'] = 'x' * 100000
        self.path.write_bytes(b''.join(packed(row) + b'\n' for row in self.rows))

    def test_derivation_reuses_accounted_cache_across_people_and_releases_on_close(self):
        self.shared_people()
        calls = []
        original = LatestEvents.__getitem__
        def counted(source, key):
            calls.append(key)
            return original(source, key)
        with patch.object(LatestEvents, '__getitem__', counted):
            first, rows = self.items(self.store.query('organized', 'sim:1', self.head()))
            count = len(calls)
            self.assertEqual(count, 2)
            self.assertGreater(self.store.metrics()['decoded_cache_bytes'], 200000)
            second, _ = self.items(self.store.query('organized', 'sim:2', self.head(), first['source_snapshot_id']))
            self.assertEqual(len(calls), count)
        rows[0]['unit']['action_tuning'] = 'changed'
        warm, unchanged = self.items(self.store.query('organized', 'sim:1', self.head(), first['source_snapshot_id']))
        self.assertNotEqual(unchanged[0]['unit']['action_tuning'], 'changed')
        for request in (first, second, warm):
            self.store.close(request['request_id'])
        deadline = time.monotonic() + 3
        while self.store.metrics()['sources'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.store.metrics()['decoded_cache_bytes'], 0)
        self.assertEqual(self.store.metrics()['estimated_bytes'], 0)

    def test_memory_pressure_discards_decoded_cache_without_invalidating_pages(self):
        self.shared_people()
        first, expected = self.items(self.store.query('organized', 'sim:1', self.head()))
        data = self.store._sources[first['source_snapshot_id']]['data']
        metrics = self.store.metrics()
        self.store.memory_limit = metrics['estimated_bytes'] - metrics['decoded_cache_bytes'] + 2 * data['latest_event_bytes']
        second, _ = self.items(self.store.query('organized', 'sim:2', self.head(), first['source_snapshot_id']))
        self.assertEqual(self.store.metrics()['decoded_cache_bytes'], 0)
        self.assertLessEqual(self.store.metrics()['estimated_bytes'], self.store.memory_limit)
        self.assertEqual(self.items(self.store.status(first['request_id']))[1], expected)
        self.assertEqual(second['source_snapshot_id'], first['source_snapshot_id'])

    def test_new_prefix_reclaims_old_decode_cache_and_keeps_old_projection(self):
        first, expected = self.items(self.store.query('organized', 'sim:1', self.head()))
        old_source = self.store._sources[first['source_snapshot_id']]
        self.assertGreater(old_source['decoded_memory'], 0)
        self.add_record(kind='event_revision', event=dict(action('new_sleep', ('13094', 'bed_sleep')), revision=1))
        second, current = self.items(self.store.query('organized', 'sim:1', self.head()))
        self.assertNotEqual(first['source_snapshot_id'], second['source_snapshot_id'])
        self.assertNotIn('decoded', old_source)
        self.assertEqual(len(current), len(expected) + 1)
        self.assertEqual(self.items(self.store.status(first['request_id']))[1], expected)

    def test_export_fast_and_budget_fallback_are_byte_identical(self):
        from context_overlay.run_artifacts import export_layers
        self.shared_people()
        data = read_prefix(self.path, 'run', len(self.rows), self.path.stat().st_size, lambda: None, 4096 * 1024 ** 2)
        options = (self.path.parent, 'run', self.head(), {'sim:1': 'One', 'sim:2': 'Two'},
                   {'capture_complete': None}, '1.126.73.1030', lambda: None)
        fast = export_layers(*options)
        slow = export_layers(*options, memory_limit=data['memory_bytes'] + 2 * data['latest_event_bytes'])
        self.assertEqual(fast['derivation_cache']['mode'], 'decoded_shared')
        self.assertEqual(slow['derivation_cache']['mode'], 'decode_on_demand')
        first = self.path.parent / 'views' / fast['directory']
        second = self.path.parent / 'views' / slow['directory']
        manifest = json.loads((first / 'manifest.json').read_text(encoding='utf8'))
        for filename in manifest['files']:
            self.assertEqual((first / filename).read_bytes(), (second / filename).read_bytes(), filename)

    def test_cold_cached_and_exported_organization_have_the_same_order(self):
        from context_overlay.run_artifacts import export_layers
        for i in range(12):
            row = action("order_" + str(i), ("13094", "bed_sleep"), 3000 + i, 4000 + i)
            self.add_record(kind="event_revision", event=dict(row, revision=1))
            unknown = action("unknown_" + str(i), ("unreviewed", "unknown_resource"), 5000 + i, 6000 + i)
            self.add_record(kind="event_revision", event=dict(unknown, revision=1))
        cold, first = self.items(self.store.query("organized", "sim:1", self.head(), page_size=3))
        warm, second = self.items(self.store.query("organized", "sim:1", self.head(), cold["source_snapshot_id"], page_size=3))
        self.assertEqual(cold["snapshot_id"], warm["snapshot_id"])
        self.assertEqual(first, second)
        exported = export_layers(self.path.parent, "run", self.head(), {"sim:1": "Test"},
                                 {"capture_complete": None}, "1.126.73.1030", lambda: None)
        path = self.path.parent / "views" / exported["directory"] / "sim-1/organized.jsonl"
        self.assertEqual(first, [json.loads(line) for line in path.read_text(encoding="utf8").splitlines()])

    def test_cached_activity_lineage_keeps_numeric_evidence_order(self):
        for i in range(12):
            row = event("effect_" + str(i), "payment.completed", {"actual_amount": -1})
            row["cause"] = {"event_id": "run:eat"}
            self.add_record(kind="event_revision", event=dict(row, revision=1))
        req, rows = self.items(self.store.query("organized", "sim:1", self.head()))
        root = next(row["item_id"] for row in rows if row.get("unit", {}).get("action_tuning") == "generic_consume_food")
        cold, first = self.items(self.store.explain(req["snapshot_id"], root, "lineage"))
        warm, second = self.items(self.store.explain(req["snapshot_id"], root, "lineage"))
        self.assertEqual(cold["snapshot_id"], warm["snapshot_id"])
        self.assertEqual(first, second)
        aliases = [int(row["evidence_ref"][1:]) for row in first]
        self.assertEqual(len(aliases), 13)
        self.assertEqual(aliases, sorted(aliases))

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
