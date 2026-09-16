"""Package source-only consumer SDK and its contract, without EA game data."""

import hashlib
import json
from pathlib import Path
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))
from context_overlay_client import SDK_VERSION


def main():
    files = sorted(path for path in (ROOT / "sdk").rglob("*")
                   if path.is_file() and path.suffix in (".py", ".json", ".md"))
    files.append(ROOT / "docs/public-api-v1.md")
    files.append(ROOT / "docs/event-coverage-0.6.0.md")
    files.append(ROOT / "docs/nearby-entities.md")
    files.append(ROOT / "docs/resource-semantics.md")
    files.append(ROOT / "docs/validation/2026-09-15-resource-semantics.md")
    files.append(ROOT / "docs/validation/2026-09-15-resource-semantics.json")
    files.append(ROOT / "docs/validation/2026-09-15-nearby-entities.md")
    files.append(ROOT / "docs/validation/2026-09-15-nearby-entities.json")
    folder = "ContextOverlay-SDK-" + SDK_VERSION
    output = ROOT / "dist" / (folder + ".zip")
    output.parent.mkdir(exist_ok=True)
    manifest = {"sdk_version": SDK_VERSION, "api_major": 1, "schema_version": "1", "files": {}}
    with zipfile.ZipFile(str(output), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            archive.writestr(folder + "/" + name, data)
            manifest["files"][name] = hashlib.sha256(data).hexdigest()
        archive.writestr(folder + "/manifest.json", json.dumps(manifest, indent=2))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    print(str(output))
    print("sha256=" + digest)


if __name__ == "__main__":
    main()
