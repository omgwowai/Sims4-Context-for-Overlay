"""Exercise the Windows installer against disposable profiles, never real saves."""

import hashlib
import json
import os
import shutil
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
        installer = str(options.get("installer", ROOT / "scripts/install.ps1")).replace("'", "''")
        wrapper.write_text(
            "[CmdletBinding(SupportsShouldProcess=$true)]\n"
            "param([Alias('Profile')][string]$UserData,[string]$PackageDirectory,[switch]$NonInteractive)\n"
            "$ErrorActionPreference='Stop'\n"
            "Import-Module Microsoft.PowerShell.Utility,Microsoft.PowerShell.Management\n"
            "function Get-Process { param($Name,$ErrorAction) " + process + " }\n"
            "& '" + installer + "' @PSBoundParameters\n", encoding="utf-8")
        package_options = [] if options.get("default_package") else ["-PackageDirectory", str(self.package_dir)]
        return subprocess.run([
            "powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-File", str(wrapper),
            "-Profile", str(self.profile),
            "-NonInteractive",
        ] + package_options + list(extra), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)

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

    def test_successive_upgrades_retain_only_previous_distinct_package(self):
        self.destination.parent.mkdir()
        self.destination.write_bytes(b"oldest")
        self.assertEqual(self.run_installer().returncode, 0)
        previous = self.destination.read_bytes()
        with zipfile.ZipFile(str(self.package_dir / self.destination.name), "a") as archive:
            archive.writestr("revision.txt", "new-build")
        manifest_path = self.package_dir / "build-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["package_sha256"] = hashlib.sha256((self.package_dir / self.destination.name).read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest))
        result = self.run_installer()
        self.assertEqual(result.returncode, 0, result.stdout)
        backups = list((self.profile / "ContextOverlay/install-backups").rglob("*.ts4script"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), previous)
        receipt = json.loads((self.profile / "ContextOverlay/install-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(Path(receipt["backup"]), backups[0])
        # Reinstalling identical content must not rotate or delete the rollback.
        self.assertEqual(self.run_installer().returncode, 0)
        self.assertEqual(list((self.profile / "ContextOverlay/install-backups").rglob("*.ts4script")), backups)
        self.assert_preserved()

    def test_failed_upgrade_preserves_backup(self):
        backup = self.profile / "ContextOverlay/install-backups/20260916T0000000000000Z/ContextOverlay.ts4script"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"rollback")
        (self.package_dir / "ContextOverlay.ts4script").write_bytes(b"broken")
        self.assertNotEqual(self.run_installer().returncode, 0)
        self.assertEqual(backup.read_bytes(), b"rollback")

    def test_checkout_source_manifest_rejects_changed_or_added_source(self):
        checkout = self.root / "checkout"
        installer = checkout / "scripts/install.ps1"
        installer.parent.mkdir(parents=True)
        installer.write_bytes((ROOT / "scripts/install.ps1").read_bytes())
        (checkout / "src").mkdir()
        source = checkout / "src/example.py"
        source.write_bytes(b"version = 1\n")
        (checkout / "dist").mkdir()
        for name in ("ContextOverlay.ts4script", "build-manifest.json"):
            (checkout / "dist" / name).write_bytes((self.package_dir / name).read_bytes())
        manifest_path = checkout / "dist/build-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"] = {"example.py": hashlib.sha256(source.read_bytes()).hexdigest()}
        manifest_path.write_text(json.dumps(manifest))
        good = self.run_installer("-WhatIf", installer=installer, default_package=True)
        self.assertEqual(good.returncode, 0, good.stdout)
        source.write_bytes(b"version = 2\n")
        self.assertNotEqual(self.run_installer(installer=installer, default_package=True).returncode, 0)
        source.write_bytes(b"version = 1\n")
        (checkout / "src/added.py").write_bytes(b"extra = True\n")
        self.assertNotEqual(self.run_installer(installer=installer, default_package=True).returncode, 0)
        self.assertFalse(self.destination.exists())
        self.assert_preserved()

    def test_extracted_bundle_resolves_default_package_directory(self):
        bundle = self.root / "extracted [bundle]"
        installer = bundle / "scripts/install.ps1"
        installer.parent.mkdir(parents=True)
        installer.write_bytes((ROOT / "scripts/install.ps1").read_bytes())
        (bundle / "dist").mkdir()
        for name in ("ContextOverlay.ts4script", "build-manifest.json"):
            (bundle / "dist" / name).write_bytes((self.package_dir / name).read_bytes())
        result = self.run_installer("-WhatIf", installer=installer, default_package=True)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.destination.parent.exists())
        self.assertFalse((self.profile / "ContextOverlay/install-receipt.json").exists())
        self.assert_preserved()

    def test_game_test_preflight_and_restore_preserve_original_files(self):
        checkout = self.root / "checkout"
        scripts = checkout / "scripts"
        scripts.mkdir(parents=True)
        for name in ("game_test.ps1", "install.ps1"):
            shutil.copyfile(str(ROOT / "scripts" / name), str(scripts / name))
        shutil.copytree(str(self.package_dir), str(checkout / "dist"))
        (self.profile / "Mods/Resource.cfg").write_text("Priority 500\n")
        package = checkout / "dist/ContextOverlay.ts4script"
        good_package = package.read_bytes()

        def run(action, *args):
            wrapper = self.root / "invoke-environment.ps1"
            wrapper.write_text(
                "$ErrorActionPreference='Stop'\n"
                "function Get-Process { param($Name,$ErrorAction) }\n"
                "& '" + str(scripts / "game_test.ps1").replace("'", "''") + "' @args\n", encoding="utf-8")
            return subprocess.run([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)
            ] + [action, "-UserData", str(self.profile)] + list(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)

        prepare_args = ("-SaveName", "Slot_00000001.save")
        package.write_bytes(b"corrupt")
        result = run("prepare", *prepare_args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"checksum mismatch", result.stdout)
        self.assert_preserved()
        backup = self.profile / "ContextOverlay/test-backup"
        self.assertFalse(backup.exists())
        package.write_bytes(good_package)
        result = run("prepare", *prepare_args)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.destination.read_bytes(), good_package)
        config = self.profile / "ContextOverlay/config.json"
        self.assertEqual(json.loads(config.read_text()), {"inspector_enabled": True, "development_driver": True})
        self.assertTrue((backup / "environment.json").exists())
        # A failed optional install must not leave restored saves marked active.
        package.write_bytes(b"corrupt")
        result = run("restore")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"checksum mismatch", result.stdout)
        self.assertFalse(backup.exists())
        self.assert_preserved()
        # A profile without config must return to that same state.
        config.unlink()
        package.write_bytes(good_package)
        self.assertEqual(run("prepare", *prepare_args).returncode, 0)
        restored = run("restore", "-SkipInstall")
        self.assertEqual(restored.returncode, 0, restored.stdout)
        self.assertFalse(config.exists())
        self.assertFalse(backup.exists())

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
