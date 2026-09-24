"""Trial handoff: usable examples and complete, checked distribution contents."""

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from support import ROOT, facts, runtime_fixture
from context_overlay import api, VERSION, SCHEMA_VERSION
from package import build_bundle, check_links

sys.path.insert(0, str(ROOT / "sdk"))
import context_overlay_client


def load_example(name):
    aliases = {"my_overlay_mod": ModuleType("my_overlay_mod"),
               "my_overlay_mod.vendor": ModuleType("my_overlay_mod.vendor"),
               "my_overlay_mod.vendor.context_overlay_client": context_overlay_client}
    spec = importlib.util.spec_from_file_location("trial_" + name, str(ROOT / "sdk/examples" / (name + ".py")))
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, aliases):
        spec.loader.exec_module(module)
    return module


class TrialExamples(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime_fixture(self)

    def test_quickstart_round_trip_does_not_read_back_other_producers(self):
        self.runtime.recorder.interaction("started", facts(), {"ticks": "99"}, "test")
        api.append_event("someone.else", {}, expected_session_id=self.runtime.session_id)
        example = load_example("quickstart")
        first = example.round_trip()
        self.assertTrue(first["recorded"])
        self.assertEqual(len(first["context"]["history"]["events"]), 1)
        self.assertEqual(first["context"]["history"]["events"][0]["origin"], "game")
        self.assertEqual([event["event_id"] for event in first["recent_own_events"]], [first["receipt"]["event_id"]])
        second = example.round_trip()
        self.assertNotEqual(first["receipt"]["event_id"], second["receipt"]["event_id"])
        self.assertEqual(len(second["recent_own_events"]), 2)
        self.assertEqual(self.runtime.recorder.index.status()["snapshots"], 0)
        self.runtime.closed = True
        self.assertFalse(example.round_trip()["ready"])

    def test_example_batch_expiry_replays_uncommitted_work_without_losing_checkpoint(self):
        rec = self.runtime.recorder
        clock = [0]
        rec.index._clock = lambda: clock[0]
        example = load_example("overlay_events")
        reader = example.IncrementalReader(rec.session_id, origins=["game"], start="now")
        self.assertTrue(reader.step(lambda events: self.assertEqual(events, [])))
        checkpoint = reader.checkpoint
        for i in range(51):
            rec.interaction("started", facts(i + 1), {"ticks": str(i + 1)}, "test")
        handled = []
        self.assertFalse(reader.step(lambda events: handled.extend(event["event_id"] for event in events)))
        self.assertEqual(len(handled), 50)
        clock[0] = 121
        with self.assertRaises(context_overlay_client.ContextOverlayError) as error:
            reader.step(lambda events: self.fail("Expired page must not reach consumer"))
        self.assertEqual(error.exception.code, "cursor_expired")
        reader.retry_batch()
        self.assertEqual(reader.checkpoint, checkpoint)
        self.assertFalse(reader.step(lambda events: handled.extend(event["event_id"] for event in events)))
        self.assertTrue(reader.step(lambda events: handled.extend(event["event_id"] for event in events)))
        self.assertEqual(len(handled), 101)
        self.assertEqual(len(set(handled)), 51)
        self.assertNotEqual(reader.checkpoint, checkpoint)
        self.assertEqual(rec.index.status()["snapshots"], 0)

    def test_game_examples_import_without_starting_game_or_writing(self):
        with patch.object(api, "get_status", side_effect=AssertionError("Import must not read game")):
            for name in ("quickstart", "consumer", "overlay_events", "event_history", "event_views", "nearby_entities", "camera_view"):
                load_example(name)
        self.assertEqual(self.runtime.recorder.journal.records, [])


class TrialPackage(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ("docs", "sdk"):
            shutil.copytree(str(ROOT / name), str(self.root / name), ignore=shutil.ignore_patterns("__pycache__"))
        for name in ("README.md", "Install.cmd", "scripts/install.ps1"):
            target = self.root / name
            target.parent.mkdir(exist_ok=True)
            target.write_bytes((ROOT / name).read_bytes())
        (self.root / "src").mkdir()
        (self.root / "src/sample.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "dist").mkdir()
        (self.root / "dist/ContextOverlay.ts4script").write_bytes(b"fixture-package")
        self.build = {"package_sha256": hashlib.sha256(b"fixture-package").hexdigest(),
                      "files": {"sample.py": hashlib.sha256((self.root / "src/sample.py").read_bytes()).hexdigest()},
                      "build_info": {"module_version": VERSION, "public_api_version": api.API_VERSION,
                                     "schema_version": SCHEMA_VERSION, "build_game_version": "fixture"}}
        self.save_manifest()

    def save_manifest(self):
        (self.root / "dist/build-manifest.json").write_text(json.dumps(self.build), encoding="utf-8")

    def test_bundles_include_entrypoints_resolved_links_and_hash_all_shipped_files(self):
        for kind in ("windows", "sdk"):
            output, digest = build_bundle(kind, self.root)
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), digest)
            with ZipFile(str(output)) as archive:
                self.assertIsNone(archive.testzip())
                contents = {name.split("/", 1)[1]: archive.read(name) for name in archive.namelist()}
            manifest = json.loads(contents.pop("manifest.json").decode("utf-8"))
            self.assertEqual(set(manifest["files"]), set(contents))
            for name, content in contents.items():
                self.assertEqual(hashlib.sha256(content).hexdigest(), manifest["files"][name])
            self.assertIn("README.md", contents)
            self.assertIn("docs/quickstart.md", contents)
            self.assertIn("docs/reading-guide.md", contents)
            self.assertIn("docs/event-layers-example.md", contents)
            self.assertIn("sdk/examples/quickstart.py", contents)
            title = contents["README.txt"].decode("utf-8-sig").splitlines()[0]
            self.assertEqual(title, {"windows": "ContextOverlay Windows 构建包", "sdk": "ContextOverlay SDK 包"}[kind])
            self.assertEqual("Install.cmd" in contents, kind == "windows")
            self.assertEqual("dist/ContextOverlay.ts4script" in contents, kind == "windows")
            self.assertFalse(any(name.startswith(("src/", ".local/", "ContextOverlay/runs/")) for name in contents))
            check_links(contents)

    def test_stale_sources_or_build_versions_cannot_replace_existing_bundle(self):
        output, _ = build_bundle("windows", self.root)
        original = output.read_bytes()
        (self.root / "src/new.py").write_text("value = 2", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sources differ"):
            build_bundle("windows", self.root)
        self.assertEqual(output.read_bytes(), original)
        (self.root / "src/new.py").unlink()
        self.build["build_info"]["public_api_version"] = "1.0.0"
        self.save_manifest()
        with self.assertRaisesRegex(ValueError, "versions differ"):
            build_bundle("windows", self.root)
        self.assertEqual(output.read_bytes(), original)

    def test_missing_linked_file_stops_packaging(self):
        (self.root / "docs/quickstart.md").write_text("[missing example](../sdk/missing.py)", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Missing linked file"):
            build_bundle("sdk", self.root)
        self.assertFalse(list((self.root / "dist").glob("*.zip")))


if __name__ == "__main__":
    unittest.main()
