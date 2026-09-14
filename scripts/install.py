"""Install the built script package atomically, retaining the previous package."""

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_game_closed():
    if os.name != "nt":
        raise SystemExit("This installer checks the Windows game process; run on Windows")
    result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=True)
    rows = csv.reader(io.StringIO(result.stdout.decode("utf-8", errors="replace")))
    running = [row[0] for row in rows if row and row[0].lower() in
               ("ts4.exe", "ts4_x64.exe", "ts4_dx9_x64.exe")]
    if running:
        raise SystemExit("Close The Sims 4 before installation: " + ", ".join(running))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    profile = args.profile.resolve()
    if not (profile / "Mods").is_dir() or not (profile / "Options.ini").is_file():
        raise SystemExit("Expected an existing Sims 4 user-data directory with Mods and Options.ini")
    assert_game_closed()
    source = ROOT / "dist/ContextOverlay.ts4script"
    manifest = json.loads((ROOT / "dist/build-manifest.json").read_text(encoding="utf-8"))
    if sha256(source) != manifest["package_sha256"]:
        raise SystemExit("Built package does not match its manifest")
    with zipfile.ZipFile(str(source)) as archive:
        if json.loads(archive.read("context_overlay/build_info.json").decode("utf-8")) != manifest["build_info"]:
            raise SystemExit("Embedded build metadata does not match the manifest")
        for name in archive.namelist():
            if name.endswith(".pyc") and archive.read(name)[:4].hex() != manifest["game_bytecode_magic"]:
                raise SystemExit("Bytecode magic mismatch: " + name)
    destination = profile / "Mods/ContextOverlay/ContextOverlay.ts4script"
    duplicates = [str(path) for path in (profile / "Mods").rglob("*.ts4script")
                  if path.name.lower() == destination.name.lower() and path.resolve() != destination.resolve()]
    if duplicates:
        raise SystemExit("Remove duplicate ContextOverlay packages first: " + ", ".join(duplicates))
    protected = [profile / "Options.ini", profile / "ContextOverlay/config.json"]
    before = {str(path): sha256(path) if path.exists() else None for path in protected}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = ROOT / ".validation/install-backups" / stamp / destination.name
    previous = None
    if destination.exists():
        previous = sha256(destination)
        backup.parent.mkdir(parents=True, exist_ok=False)
        shutil.copy2(str(destination), str(backup))
        if sha256(backup) != previous:
            raise SystemExit("Backup verification failed; installed package was not changed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    assert_game_closed()
    # Only this exact module file is replaced. Config, options and saves are not edited.
    with tempfile.NamedTemporaryFile(dir=str(destination.parent), suffix=".installing", delete=False) as stream:
        pending = Path(stream.name)
        with source.open("rb") as package:
            shutil.copyfileobj(package, stream)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        if sha256(pending) != manifest["package_sha256"]:
            raise SystemExit("Copy verification failed; installed package was not changed")
        assert_game_closed()
        os.replace(str(pending), str(destination))
    finally:
        if pending.exists():
            pending.unlink()
    after = {str(path): sha256(path) if path.exists() else None for path in protected}
    receipt = {"installed_at_utc": stamp, "module_version": manifest["build_info"]["module_version"],
               "destination": str(destination), "package_sha256": sha256(destination),
               "previous_sha256": previous, "backup": str(backup) if previous else None,
               "options_and_config_unchanged": before == after, "protected_files": after,
               "game_started": False, "validation": "offline_only_pending_user_game_test"}
    if receipt["package_sha256"] != manifest["package_sha256"] or before != after:
        raise SystemExit("Post-install verification failed")
    receipt_path = ROOT / ".validation/inspector-install.json"
    receipt_path.parent.mkdir(exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(receipt, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
