"""Exercise the Windows installer against disposable profiles, never real saves."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "Windows PowerShell installer")
class WindowsInstallChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="co install [test] ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile"
        self.package_dir = self.root / "package"
        self.package_dir.mkdir()
        (self.profile / "Mods").mkdir(parents=True)
        (self.profile / "saves").mkdir()
        (self.profile / "ContextOverlay").mkdir()
        self.protected = {
            self.profile / "Options.ini": b"scriptmodsenabled = 1\n",
            self.profile / "saves/Slot_00000001.save": b"original-save",
            self.profile / "ContextOverlay/config.json": b'{"inspector_enabled":true}',
            self.profile / "Mods/OtherMod.ts4script": b"other-mod",
        }
        for path, content in self.protected.items():
            path.write_bytes(content)
        self.destination = self.profile / "Mods/ContextOverlay/ContextOverlay.ts4script"
        self.make_package()

    def make_package(self, magic=b"\x42\x0d\x0d\x0a", embedded_version="0.3.2"):
        info = {"module_version": "0.3.2", "source_sha256": "sample-source"}
        path = self.package_dir / "ContextOverlay.ts4script"
        with zipfile.ZipFile(str(path), "w") as archive:
            archive.writestr("context_overlay/build_info.json", json.dumps(dict(info, module_version=embedded_version)))
            archive.writestr("context_overlay_bootstrap.pyc", magic + b"fixture-not-executed")
        manifest = {"package_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "game_bytecode_magic": "420d0d0a", "build_info": info}
        (self.package_dir / "build-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def run_installer(self, *extra, **options):
        # Isolate process discovery as well as the profile: the real game may
        # be running while these disposable filesystem cases are exercised.
        wrapper = self.root / "invoke.ps1"
        process = "[pscustomobject]@{Name='TS4_x64'}" if options.get("game_running") else ""
        installer = str(ROOT / "scripts/install.ps1").replace("'", "''")
        wrapper.write_text(
            "[CmdletBinding(SupportsShouldProcess=$true)]\n"
            "param([string]$Profile,[string]$PackageDirectory,[switch]$NonInteractive)\n"
            "$ErrorActionPreference='Stop'\n"
            "Import-Module Microsoft.PowerShell.Utility,Microsoft.PowerShell.Management\n"
            "function Get-Process { param($Name,$ErrorAction) " + process + " }\n"
            "& '" + installer + "' @PSBoundParameters\n", encoding="utf-8")
        return subprocess.run([
            "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(wrapper),
            "-Profile", str(self.profile), "-PackageDirectory", str(self.package_dir),
            "-NonInteractive",
        ] + list(extra), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)

    def assert_preserved(self):
        for path, content in self.protected.items():
            self.assertEqual(path.read_bytes(), content, str(path))

    def test_first_install_and_repeat_preserve_user_data(self):
        first = self.run_installer()
        self.assertEqual(first.returncode, 0, first.stdout)
        self.assertEqual(self.destination.read_bytes(), (self.package_dir / self.destination.name).read_bytes())
        receipt = self.profile / "ContextOverlay/install-receipt.json"
        before = receipt.read_bytes()
        second = self.run_installer()
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertEqual(receipt.read_bytes(), before)
        self.assertFalse((self.profile / "ContextOverlay/install-backups").exists())
        self.assert_preserved()

    def test_upgrade_keeps_verified_backup_outside_mods(self):
        self.destination.parent.mkdir()
        self.destination.write_bytes(b"previous-version")
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout)
        receipt = json.loads((self.profile / "ContextOverlay/install-receipt.json").read_text(encoding="utf-8"))
        backup = Path(receipt["backup"])
        backup.relative_to(self.profile / "ContextOverlay/install-backups")
        self.assertEqual(backup.read_bytes(), b"previous-version")
        self.assertEqual(self.destination.read_bytes(), (self.package_dir / self.destination.name).read_bytes())
        self.assert_preserved()

    def test_checksum_failure_makes_no_install(self):
        (self.package_dir / "ContextOverlay.ts4script").write_bytes(b"corrupt-download")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"checksum mismatch", result.stdout)
        self.assertFalse(self.destination.parent.exists())
        self.assert_preserved()

    def test_duplicate_copy_is_not_silently_overwritten(self):
        duplicate = self.profile / "Mods/ContextOverlay.ts4script"
        duplicate.write_bytes(b"duplicate")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Duplicate ContextOverlay", result.stdout)
        self.assertFalse(self.destination.exists())
        self.assertEqual(duplicate.read_bytes(), b"duplicate")
        self.assert_preserved()

    def test_whatif_writes_nothing(self):
        result = self.run_installer("-WhatIf")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.destination.parent.exists())
        self.assertFalse((self.profile / "ContextOverlay/install-receipt.json").exists())
        self.assert_preserved()

    def test_wrong_bytecode_is_rejected_even_with_matching_checksum(self):
        self.make_package(magic=b"bad!")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Bytecode mismatch", result.stdout)
        self.assertFalse(self.destination.exists())

    def test_embedded_version_mismatch_is_rejected(self):
        self.make_package(embedded_version="0.0.0")
        result = self.run_installer()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Embedded build metadata mismatch", result.stdout)
        self.assertFalse(self.destination.exists())

    def test_running_game_refuses_installation(self):
        result = self.run_installer(game_running=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Close The Sims 4", result.stdout)
        self.assertFalse(self.destination.parent.exists())
        self.assert_preserved()
