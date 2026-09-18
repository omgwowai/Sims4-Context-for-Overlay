"""Accepted-data drainage, independent diagnostics and human-readable layers."""

import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from support import facts
from context_overlay import game_runtime
from context_overlay.recorder import Recorder
from context_overlay.run_artifacts import export_layers, RunArtifacts
from context_overlay.storage import Journal, StorageError
from context_overlay.view_source import ViewError
from offline import read_journal
from validate_run import capture_status


class PersistenceRecovery(unittest.TestCase):
    def test_admission_failure_drains_accepted_records_and_keeps_independent_error(self):
        entered, release = threading.Event(), threading.Event()
        def delayed_open(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("Test gate timeout")
            return open(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            writer = Journal(directory, capacity=2, opener=delayed_open, reserve_bytes=0)
            rec = Recorder(writer, session_id="test")
            try:
                self.assertTrue(entered.wait(2))
                rec.note("session_start", {})
                rec.note("accepted", {})
                self.assertIsNone(rec.note("rejected", {}))
                self.assertTrue(rec.paused)
                release.set()
                writer.close(wait=True)
                state = writer.status()
                self.assertEqual(state["accepted_sequence"], 2)
                self.assertEqual(state["durable_sequence"], 2)
                self.assertEqual(state["pending_bytes"], 0)
                self.assertEqual([r["category"] for r in read_journal(writer.path)["observations"]], ["session_start", "accepted"])
                saved = json.loads((Path(directory) / "persistence-status.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["first_failure"]["kind"], "admission")
                self.assertEqual(saved["durable_sequence"], 2)
                self.assertIn("queue full", saved["error"])
                runtime = game_runtime.Runtime.__new__(game_runtime.Runtime)
                runtime.recorder, runtime.writer, runtime.session_id = rec, writer, "test"
                with patch.object(game_runtime, "log") as log:
                    runtime.fail("cleanup also failed")
                    runtime.fail("another later failure")
                self.assertEqual(log.call_count, 1)
                self.assertIn("queue full", rec.error)
                report = json.loads((Path(directory) / "run-status.json").read_text(encoding="utf-8"))
                self.assertEqual(report["phase"], "failed")
                self.assertIn("queue full", report["recorder"]["error"])
            finally:
                release.set()
                writer.close(wait=True)

    def test_batches_preserve_order_and_do_not_acknowledge_before_sync(self):
        entered, release = threading.Event(), threading.Event()
        sync_started, sync_release = threading.Event(), threading.Event()
        def delayed_open(*args, **kwargs):
            entered.set()
            release.wait(3)
            return open(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            writer = Journal(directory, opener=delayed_open, reserve_bytes=0)
            original = writer._sync
            def gated_sync(stream):
                sync_started.set()
                if not sync_release.wait(3):
                    raise RuntimeError("Sync gate timeout")
                original(stream)
            writer._sync = gated_sync
            try:
                self.assertTrue(entered.wait(2))
                for i in range(300):
                    writer.append({"kind": "observation", "session_id": "test", "category": "sample", "data": {"i": i}})
                output = writer.export({"request_id": "a" * 32, "value": 7})
                writer.append({"kind": "observation", "session_id": "test", "category": "session_end"})
                release.set()
                self.assertTrue(sync_started.wait(2))
                self.assertEqual(writer.status()["durable_sequence"], 0)
                self.assertEqual(writer.status()["durable_byte_offset"], 0)
                sync_release.set()
                writer.close(wait=True)
                state = writer.status()
                self.assertEqual(state["durable_sequence"], 301)
                self.assertEqual(state["durable_byte_offset"], writer.path.stat().st_size)
                self.assertLess(state["record_batches"], 10)
                self.assertEqual(state["pending_bytes"], 0)
                self.assertEqual(json.loads(Path(output).read_text())["value"], 7)
                rows = [json.loads(line) for line in writer.path.read_text().splitlines()]
                self.assertEqual([r["sequence"] for r in rows], list(range(1, 302)))
                self.assertEqual([r["data"]["i"] for r in rows[:-1]], list(range(300)))
            finally:
                release.set()
                sync_release.set()
                writer.close(wait=True)

    def test_io_failure_keeps_unacknowledged_batch_and_diagnostic(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = Journal(directory, reserve_bytes=0)
            writer._sync = lambda stream: (_ for _ in ()).throw(OSError("failed sync"))
            writer.append({"kind": "observation", "session_id": "test", "category": "sample"})
            writer._thread.join(3)
            self.assertEqual(writer.status()["durable_sequence"], 0)
            self.assertTrue(writer.status()["io_failed"])
            saved = json.loads((Path(directory) / "persistence-status.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["first_failure"]["kind"], "io")
            self.assertIn("failed sync", saved["error"])
            with self.assertRaises(StorageError):
                writer.flush()
            writer.close(wait=True)


class LayerArtifacts(unittest.TestCase):
    def test_closed_capture_requires_consistent_final_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = Journal(root, reserve_bytes=0)
            rec = Recorder(writer, session_id="closed")
            rec.note("session_start", {})
            end = rec.note("session_end", {})
            writer.close(wait=True)
            runtime = game_runtime.Runtime.__new__(game_runtime.Runtime)
            runtime.recorder, runtime.writer, runtime.session_id = rec, writer, "closed"
            runtime.session_end_sequence = end
            report = runtime._run_report("closed")
            loaded = read_journal(writer.path)
            path = root / "run-status.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            self.assertTrue(capture_status(root, loaded)["capture_complete"])
            for field, value in (("session_end_sequence", end - 1), ("cleanup_errors", ["cleanup failure"]),
                                 ("journal_drained", False)):
                with self.subTest(field=field):
                    changed = dict(report, **{field: value})
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    self.assertFalse(capture_status(root, loaded)["capture_complete"])
            for field, value in (("pending_bytes", 1), ("error", "write failed"), ("io_failed", True)):
                with self.subTest(persistence_field=field):
                    changed = json.loads(json.dumps(report))
                    changed["recorder"]["persistence"][field] = value
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    self.assertFalse(capture_status(root, loaded)["capture_complete"])

    def test_atomic_outputs_cover_source_and_match_shared_recap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = Journal(root, reserve_bytes=0)
            rec = Recorder(writer, session_id="layers")
            rec.note("session_start", {})
            rec.interaction("started", facts(1), {"ticks": "1"}, "native")
            rec.interaction("exited", dict(facts(1), finishing_type="NATURAL"), {"ticks": "2"}, "native")
            rec.note("session_end", {})
            writer.close(wait=True)
            target = facts()["actor"]
            result = export_layers(root, "layers", writer.status(), {target["key"]: target["name"]},
                                   {"capture_complete": True}, "1.126.73.1030", lambda: None)
            dest = root / "views" / result["directory"]
            manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["coverage"]["capture_complete"])
            self.assertEqual(manifest["source"]["sequence"], 4)
            self.assertEqual(manifest["source"]["records"], 4)
            self.assertEqual(manifest["source"]["events"], 1)
            for name, saved in manifest["files"].items():
                self.assertEqual(hashlib.sha256((dest / name).read_bytes()).hexdigest(), saved["sha256"])
            person = result["people"][0]
            folder = dest / person["folder"]
            bundle = json.loads((folder / "details.bundle.json").read_text(encoding="utf-8"))
            recap = json.loads((folder / "recap.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["recap"], recap)
            self.assertEqual(bundle["manifest"]["source_sha256"], hashlib.sha256(writer.path.read_bytes()).hexdigest())
            self.assertIn("采集正常结束", (folder / "recap.md").read_text(encoding="utf-8"))
            self.assertEqual(len((dest / "events.jsonl").read_text().splitlines()), 1)
            prior = (root / "views/latest.json").read_bytes()
            with self.assertRaises(ViewError):
                export_layers(root, "layers", writer.status(), {target["key"]: target["name"]},
                              {"capture_complete": False}, "1.126.73.1030", lambda: None, output_limit=1)
            self.assertEqual((root / "views/latest.json").read_bytes(), prior)
            self.assertFalse(list((root / "views").glob(".pending-*")))
            exporter = RunArtifacts(root, "layers", "1.126.73.1030", 128 * 1024 * 1024, 512 * 1024 * 1024)
            final = exporter.finish(writer.status(), {target["key"]: target["name"]}, {"capture_complete": False})
            self.assertEqual(final["state"], "ready")
            self.assertFalse(final["capture_complete"])
            self.assertIn("片段", (root / "views" / final["directory"] / person["folder"] / "recap.md").read_text(encoding="utf-8"))
            self.assertEqual(json.loads((root / "view-export-status.json").read_text())["state"], "ready")


if __name__ == "__main__":
    unittest.main()
