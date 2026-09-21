"""Offline tools and MOD views must agree on source integrity, including retries."""

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import ROOT, PYTHON, facts
from context_overlay import game_runtime
from context_overlay.recorder import Recorder
from context_overlay.storage import Journal
from context_overlay.view_source import LINE_LIMIT, ViewError, packed, read_prefix
from experience_recap import build_recap, load_source
from offline import read_journal, read_packet
from validate_run import audit


class JournalValidationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        writer = Journal(self.root, reserve_bytes=0)
        rec = Recorder(writer, session_id="review")
        rec.note("session_start", {})
        rec.interaction("started", facts(1), {"ticks": "1"}, "native")
        rec.interaction("exited", dict(facts(1), finishing_type="NATURAL"), {"ticks": "2"}, "native")
        end = rec.note("session_end", {})
        writer.close(wait=True)
        self.path = writer.path
        self.original = self.path.read_bytes()
        self.rows = [json.loads(line) for line in self.original.splitlines()]
        runtime = game_runtime.Runtime.__new__(game_runtime.Runtime)
        runtime.recorder, runtime.writer, runtime.session_id = rec, writer, "review"
        runtime.session_end_sequence = end
        self.report = runtime._run_report("closed")
        self.status_path = self.root / "run-status.json"
        self.status_path.write_text(json.dumps(self.report), encoding="utf-8")

    def write(self, rows):
        raw = b"".join(packed(row) + b"\n" for row in rows)
        self.path.write_bytes(raw)
        return raw

    def runtime_read(self, count=4):
        return read_prefix(self.path, "review", count, self.path.stat().st_size,
                           lambda: None, 64 * 1024 * 1024)

    def test_corrupt_sources_are_rejected_by_both_readers_and_offline_entry_points(self):
        cases = {}
        for name, index, fields in (
                ("sequence_gap", 2, {"sequence": 4}),
                ("sequence_bool", 0, {"sequence": True}),
                ("sequence_float", 0, {"sequence": 1.0}),
                ("mixed_session", 3, {"session_id": "another"}),
                ("unknown_kind", 3, {"kind": "status"}),
                ("invalid_event", 2, {"event": []})):
            rows = copy.deepcopy(self.rows)
            rows[index].update(fields)
            cases[name] = rows
        for name, index, fields in (
                ("missing_initial_revision", 1, {"revision": 2}),
                ("revision_gap", 2, {"revision": 3}),
                ("revision_repeat", 2, {"revision": 1}),
                ("revision_bool", 1, {"revision": True}),
                ("event_outside_session", 2, {"event_id": "another:event"}),
                ("invalid_entities", 2, {"entities": None})):
            rows = copy.deepcopy(self.rows)
            rows[index]["event"].update(fields)
            cases[name] = rows
        retry = copy.deepcopy(self.rows[1])
        retry["event"]["facts"]["name"] = "conflicting retry"
        cases["conflicting_retry"] = self.rows[:2] + [retry] + self.rows[2:]
        cases["invalid_record"] = self.rows[:2] + [[]] + self.rows[2:]
        for name, rows in cases.items():
            with self.subTest(case=name):
                raw = self.write(rows)
                for scope in ("all", "retained"):
                    result = read_journal(self.path, scope)
                    self.assertFalse(result["complete"])
                    self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
                    self.assertTrue(result["errors"][0]["line"])
                with self.assertRaises(ViewError) as caught:
                    self.runtime_read()
                self.assertEqual(caught.exception.code, "source_invalid")
                with self.assertRaises(ValueError):
                    load_source(self.path)
                with self.assertRaises(ValueError):
                    read_packet(self.path)
                self.assertFalse(audit(self.root)["integrity_passed"])
                self.assertFalse(audit(self.root)["capture"]["capture_complete"])

    def test_same_size_revision_corruption_cannot_pass_closed_cli_or_publish_bundle(self):
        lines = self.original.splitlines(keepends=True)
        lines[2] = lines[2].replace(b'"revision":2', b'"revision":3').replace(b'"revision": 2', b'"revision": 3')
        changed = b"".join(lines)
        self.assertNotEqual(changed, self.original)
        self.assertEqual(len(changed), len(self.original))
        self.path.write_bytes(changed)
        result = audit(self.root)
        self.assertFalse(result["integrity_passed"])
        self.assertFalse(result["capture"]["capture_complete"])
        run = subprocess.run(PYTHON + [str(ROOT / "scripts/validate_run.py"), str(self.root), "--require-closed"], capture_output=True)
        self.assertEqual(run.returncode, 1, run.stderr)
        self.assertFalse(json.loads(run.stdout)["capture"]["capture_complete"])
        output = self.root / "bundle.json"
        output.write_text("previous valid output", encoding="utf-8")
        run = subprocess.run(PYTHON + [str(ROOT / "scripts/experience_recap.py"), "build", str(self.path),
            "--entity", facts()["actor"]["key"], "--output", str(output)], capture_output=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(output.read_text(encoding="utf-8"), "previous valid output")

    def test_equivalent_retries_keep_full_source_hash_and_complete_capture(self):
        # Equivalent JSON may have different whitespace/order, but identical values.
        retry = json.dumps(dict(reversed(list(self.rows[1].items()))), ensure_ascii=False).encode("utf-8") + b"\n"
        lines = self.original.splitlines(keepends=True)
        raw = b"".join(lines[:2] + [retry] + lines[2:] + [retry])
        self.path.write_bytes(raw)
        self.report["recorder"]["persistence"]["durable_byte_offset"] = len(raw)
        self.status_path.write_text(json.dumps(self.report), encoding="utf-8")
        loaded = load_source(self.path)
        runtime = self.runtime_read()
        self.assertEqual(loaded["events"], list(runtime["events"].values()))
        self.assertEqual(loaded["sha256"], runtime["sha256"])
        self.assertEqual(loaded["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(loaded["last_sequence"], 4)
        self.assertEqual(len(runtime["revisions"][loaded["events"][0]["event_id"]]), 2)
        self.assertEqual(len(read_journal(self.path)["observations"]), 2)
        self.assertTrue(audit(self.root)["capture"]["capture_complete"])
        self.assertTrue(build_recap(loaded, facts()["actor"]["key"])["snapshot_id"])

    def test_fifo_does_not_reset_revision_validation(self):
        original_id = self.rows[1]["event"]["event_id"]
        other = copy.deepcopy(self.rows[1])
        other.update(sequence=4, evicted_event_ids=[original_id])
        other["event"]["event_id"] = "review:other"
        rows = self.rows[:3] + [other]
        self.write(rows)
        self.assertEqual([e["event_id"] for e in read_journal(self.path)["events"]], ["review:other"])
        restored = copy.deepcopy(self.rows[2])
        restored["sequence"], restored["event"]["revision"] = 5, 3
        rows.append(restored)
        self.write(rows)
        self.assertTrue(read_journal(self.path)["complete"])
        self.assertEqual(len(self.runtime_read(5)["events"]), 2)
        restored["event"]["revision"] = 1
        self.write(rows)
        self.assertFalse(read_journal(self.path)["complete"])
        with self.assertRaises(ViewError):
            self.runtime_read(5)

    def test_framing_and_nonfinite_values_fail_without_hiding_the_input_hash(self):
        cases = [self.original[:-1], self.original + b'{"sequence":',
                 self.original + b' ' * (LINE_LIMIT + 1) + b'\n',
                 self.original.replace(b'"data":{}', b'"data":{"invalid":NaN}', 1),
                 self.original.replace(b'"data":{}', b'"data":{"invalid":1e999}', 1)]
        for raw in cases:
            with self.subTest(size=len(raw)):
                self.assertNotEqual(raw, self.original)
                self.path.write_bytes(raw)
                loaded = read_journal(self.path)
                self.assertFalse(loaded["complete"])
                self.assertEqual(loaded["sha256"], hashlib.sha256(raw).hexdigest())
                with self.assertRaises(ViewError):
                    self.runtime_read()


if __name__ == "__main__":
    unittest.main()
