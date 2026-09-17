"""Build a local script package using CPython 3.7; no third-party packages."""

import argparse
import configparser
import hashlib
import importlib.util
import json
import py_compile
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from context_overlay import EA_REFERENCE_COMMIT, VERSION
from context_overlay.api import API_VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--game", type=Path, default=Path("D:/Games/The Sims 4"))
    parser.add_argument("--strings", type=Path)
    parser.add_argument("--string-sources", type=Path, help="Matched string_sources.json from build_resource_catalog.py")
    args = parser.parse_args()
    if args.string_sources and not args.strings:
        raise SystemExit("--string-sources requires --strings")
    if args.string_sources:
        metadata = json.loads(args.string_sources.read_text(encoding="utf-8"))
        if metadata.get("format") != "string_sources_v1" or metadata.get("strings_sha256") != hashlib.sha256(args.strings.read_bytes()).hexdigest():
            raise SystemExit("String provenance does not match the supplied dictionary")
    if sys.version_info[:2] != (3, 7):
        raise SystemExit("Build with CPython 3.7, not the host default interpreter")
    gameplay = args.game / "Data/Simulation/Gameplay/simulation.zip"
    with zipfile.ZipFile(str(gameplay)) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".pyc"))
        game_magic = archive.read(member)[:4]
    if game_magic != importlib.util.MAGIC_NUMBER:
        raise SystemExit("Compiler bytecode magic does not match the installed game")
    game_config = configparser.ConfigParser()
    game_config.read(str(args.game / "Game/Bin/Default.ini"), encoding="utf-8-sig")
    game_version = game_config.get("Version", "gameversion")
    if args.string_sources and metadata.get("provenance", {}).get("game_version") != game_version:
        raise SystemExit("String resources were extracted for a different game version; rebuild the resource catalog")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    output = dist / "ContextOverlay.ts4script"
    manifest = {"python": sys.version, "game_bytecode_magic": game_magic.hex(), "files": {}}
    with tempfile.TemporaryDirectory(prefix="context-overlay-build-") as directory, \
            zipfile.ZipFile(str(output), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        build = Path(directory)
        for source in sorted((ROOT / "src").rglob("*.py")):
            relative = source.relative_to(ROOT / "src")
            compiled = build / relative.with_suffix(".pyc")
            compiled.parent.mkdir(parents=True, exist_ok=True)
            py_compile.compile(str(source), cfile=str(compiled), dfile=relative.as_posix(), doraise=True)
            archive.write(str(compiled), relative.with_suffix(".pyc").as_posix())
            manifest["files"][relative.as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
        if args.strings:
            data = args.strings.read_bytes()
            json.loads(data.decode("utf-8"))
            archive.writestr("context_overlay/strings_zh.json", data)
            manifest["strings_sha256"] = hashlib.sha256(data).hexdigest()
        if args.string_sources:
            data = args.string_sources.read_bytes()
            archive.writestr("context_overlay/string_sources.json", data)
            manifest["string_sources_sha256"] = hashlib.sha256(data).hexdigest()
        build_info = {"module_version": VERSION, "public_api_version": API_VERSION, "build_game_version": game_version,
                      "game_version_source": "Game/Bin/Default.ini at build time",
                      "game_bytecode_magic": game_magic.hex(),
                      "ea_reference_project": "sims4-python", "ea_reference_commit": EA_REFERENCE_COMMIT,
                      "source_sha256": hashlib.sha256(json.dumps(manifest["files"], sort_keys=True).encode("utf-8")).hexdigest(),
                      "strings_sha256": manifest.get("strings_sha256"),
                      "string_sources_sha256": manifest.get("string_sources_sha256")}
        archive.writestr("context_overlay/build_info.json", json.dumps(build_info, sort_keys=True))
        manifest["build_info"] = build_info
    manifest["package_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    (dist / "build-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(str(output))
    print("sha256=" + manifest["package_sha256"])


if __name__ == "__main__":
    main()
