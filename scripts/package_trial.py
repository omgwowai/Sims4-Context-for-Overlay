"""Bundle the verified local build for internal distribution; no game launch."""

import hashlib
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / "dist/build-manifest.json").read_text(encoding="utf-8"))
    package = ROOT / "dist/ContextOverlay.ts4script"
    if hashlib.sha256(package.read_bytes()).hexdigest() != manifest["package_sha256"]:
        raise SystemExit("Package checksum mismatch; build again before packaging")
    for name, expected in manifest["files"].items():
        if hashlib.sha256((ROOT / "src" / name).read_bytes()).hexdigest() != expected:
            raise SystemExit("Source differs from build: " + name)
    version = manifest["build_info"]["module_version"]
    folder = "ContextOverlay-" + version + "-Windows"
    output = ROOT / "dist" / (folder + ".zip")
    files = ["Install.cmd", "scripts/install.ps1", "dist/ContextOverlay.ts4script",
             "dist/build-manifest.json", "docs/install.md", "docs/mod-integration.md",
             "docs/inspector-manual-test.md", "docs/runtime-usage.md", "docs/semanticizer.md",
             "docs/validation/2026-09-15-semantic-resolution.md",
             "docs/validation/2026-09-15-semantic-resolution.json", "docs/public-api-v1.md",
             "docs/validation/2026-09-15-public-api.md", "docs/validation/2026-09-15-public-api.json",
             "docs/event-coverage-0.6.0.md", "docs/event-expansion-plan.md", "docs/event-recorder.md",
             "docs/validation/2026-09-15-event-expansion.md", "docs/validation/2026-09-15-event-expansion.json",
             "docs/validation/2026-09-15-event-expansion-first-live.md", "docs/validation/2026-09-15-event-expansion-first-live.json",
             "docs/validation/2026-09-15-event-quality-fixes.md", "docs/validation/2026-09-15-event-quality-fixes.json",
             "docs/nearby-entities.md", "docs/validation/2026-09-15-nearby-entities.md",
             "docs/validation/2026-09-15-nearby-entities.json",
             "examples/mod_consumer.py"]
    files.extend(path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "sdk").rglob("*"))
                 if path.is_file() and path.suffix in (".py", ".json", ".md"))
    with zipfile.ZipFile(str(output), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(str(ROOT / name), folder + "/" + name)
        archive.writestr(folder + "/README.txt", (
            "ContextOverlay " + version + " - Windows internal trial\r\n"
            "Extract this entire ZIP, close The Sims 4, then double-click Install.cmd.\r\n"
            "No Python installation or administrator rights required.\r\n"
            "Installation (Chinese): docs/install.md\r\n"
            "Developer integration: docs/mod-integration.md\r\n"
            "In-game UI: docs/inspector-manual-test.md\r\n"
            "Internal guide: https://omgwowai.feishu.cn/wiki/AAvPw03vJiR1NSkdDtmcxP04ng6\r\n"
            "Only the original .ts4script is installed; do not unzip that file.\r\n"
        ))
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    print(str(output))
    print("sha256=" + digest)


if __name__ == "__main__":
    main()
