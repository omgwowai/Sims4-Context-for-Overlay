"""Offline reports preserve evidence, reject corruption, and filter only display."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from support import ROOT, PYTHON
from offline import read_packet
spec = importlib.util.spec_from_file_location("offline_translate", str(ROOT / "scripts/translate.py"))
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def event(identifier, tier="main"):
    return {"event_id": identifier, "revision": 1, "event_type": "state_change", "tier": tier,
            "field": "buffs", "before": None, "after": {"name": "状态 <details> *原文*"},
            "entities": ["sim:1"], "participants": [{"key": "sim:1", "name": "甲"}],
            "last_observed_time": {"ticks": "100", "display": "第 1 天 12:00"}}


class OfflineReportChecks(unittest.TestCase):
    def test_complete_journal_keeps_evicted_events_and_latest_revisions(self):
        first, second = event("run:a"), event("run:b")
        revised = dict(second, revision=2, after={"name": "最终状态"})
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "journal.jsonl"
            records = [dict(sequence=i, session_id="run", kind="event_revision", event=value)
                       for i, value in enumerate((first, second, revised), 1)]
            records[1]["evicted_event_ids"] = [first["event_id"]]
            records[0].update(module_version="0.6.0", schema_version="1", recorded_at="2026-09-15T00:00:00Z")
            # Repeated earlier records are valid even after newer revisions.
            records.extend(copy.deepcopy(row) for row in (records[-1], records[1], records[0]))
            source.write_text("\n".join(json.dumps(row) for row in records) + "\n", encoding="utf-8")
            original = source.read_bytes()
            packet = read_packet(source)[0]
            self.assertEqual(packet["history"]["events"], [first, revised])
            self.assertEqual(packet["module_version"], "0.6.0")
            self.assertEqual(source.read_bytes(), original)

    def test_invalid_journal_never_creates_or_overwrites_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "journal.jsonl", Path(directory) / "report.md"
            output.write_text("previous report")
            source.write_text('{"sequence":', encoding="utf-8")
            result = subprocess.run(PYTHON + [str(ROOT / "scripts/translate.py"), str(source), str(output)], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_text(), "previous report")
            with self.assertRaises(ValueError):
                read_packet(source)[0]

    def test_markdown_filters_internal_but_keeps_autonomy_and_escapes_text(self):
        normal, internal = event("run:main"), event("run:internal", "internal")
        decision = dict(event("run:decision", "internal"), event_type="game_event", category="autonomy.decision",
                        payload={"retention_gate": "queue_success", "stages": [], "filters": {}, "interaction_event_id": "run:main"})
        packet = {"session_id": "run", "history": {"events": [normal, internal, decision]}}
        original = copy.deepcopy(packet)
        text = report.markdown_report(packet, Path("journal.jsonl"))
        self.assertIn("事件：2 / 3", text)
        self.assertNotIn("run:internal", text)
        self.assertIn("run:decision", text)
        self.assertIn("<summary>Autonomy 候选与评分</summary>", text)
        self.assertIn("&lt;details&gt;", text)
        self.assertIn("\\*原文\\*", text)
        self.assertIn("第 1 天 12:00", text)
        self.assertEqual(packet, original)
        self.assertIn("run:internal", report.markdown_report(packet, Path("journal.jsonl"), include_internal=True))

    def test_cli_defaults_markdown_and_json_is_explicit_and_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "packet.json"
            packet = {"history": {"events": [event("a"), event("b", "internal")]}}
            source.write_text(json.dumps(packet), encoding="utf-8")
            command = PYTHON + [str(ROOT / "scripts/translate.py"), str(source)]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(source.with_suffix(".md").read_text(encoding="utf-8").startswith("# ContextOverlay"))
            result = subprocess.run(command + ["--format", "json"], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            translated = json.loads(source.with_name("packet-translated.json").read_text(encoding="utf-8"))
            self.assertEqual(translated["history"], packet["history"])
            self.assertEqual(len(translated["rendered"]["history"]), 2)
            result = subprocess.run(command + [str(source), "--format", "json"], capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(source.read_text()), packet)

    def test_context_and_nearby_examples_have_readable_content(self):
        for name, expected in (("context-packet.json", "示例角色"), ("nearby-packet.json", "Entity 2")):
            source = ROOT / "sdk/examples" / name
            self.assertIn(expected, report.markdown_report(read_packet(source)[0], source))


if __name__ == "__main__":
    unittest.main()
