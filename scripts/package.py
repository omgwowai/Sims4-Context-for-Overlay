"""Create a Windows distribution (with SDK) or a standalone SDK archive."""

import argparse
import json
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from tool_support import ROOT, sha256

sys.path.insert(0, str(ROOT / "sdk"))
from context_overlay_client import SDK_VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("windows", "sdk"))
    args = parser.parse_args()
    files = sorted(path for path in (ROOT / "sdk").rglob("*")
                   if path.is_file() and path.suffix in (".py", ".json", ".md"))
    files.extend(ROOT / name for name in ("docs/public-api-v1.md", "docs/architecture.md"))
    metadata = {"sdk_version": SDK_VERSION, "api_major": 1, "schema_version": "1"}
    folder = "ContextOverlay-SDK-" + SDK_VERSION
    if args.kind == "windows":
        build = json.loads((ROOT / "dist/build-manifest.json").read_text(encoding="utf-8"))
        sources = {path.relative_to(ROOT / "src").as_posix(): sha256(path)
                   for path in (ROOT / "src").rglob("*.py")}
        if sha256(ROOT / "dist/ContextOverlay.ts4script") != build["package_sha256"] or sources != build["files"]:
            raise SystemExit("Package or sources differ from build; rebuild before packaging")
        metadata["module_version"] = build["build_info"]["module_version"]
        folder = "ContextOverlay-" + metadata["module_version"] + "-Windows"
        files.extend(ROOT / name for name in ("Install.cmd", "scripts/install.ps1",
            "dist/ContextOverlay.ts4script", "dist/build-manifest.json", "docs/install.md", "docs/development.md"))
    output = ROOT / "dist" / (folder + ".zip")
    output.parent.mkdir(exist_ok=True)
    metadata["files"] = {path.relative_to(ROOT).as_posix(): sha256(path) for path in files}
    with ZipFile(str(output), "w", compression=ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(str(path), folder + "/" + path.relative_to(ROOT).as_posix())
        archive.writestr(folder + "/manifest.json", json.dumps(metadata, indent=2))
        if args.kind == "windows":
            archive.writestr(folder + "/README.txt", (
                "Extract this entire ZIP, close The Sims 4, then double-click Install.cmd.\r\n"
                "No Python installation or administrator rights required.\r\n"
                "Installation and in-game UI: docs/install.md\r\n"
                "Developer integration: docs/public-api-v1.md\r\n"
                "Only the original .ts4script is installed; do not unzip that file.\r\n"))
    digest = sha256(output)
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    print(str(output))
    print("sha256=" + digest)


if __name__ == "__main__":
    main()
