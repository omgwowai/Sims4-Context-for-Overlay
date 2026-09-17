"""Create a Windows distribution (with SDK) or a standalone SDK archive."""

import argparse
import hashlib
import json
import os
import posixpath
import re
import sys
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

from tool_support import ROOT, sha256

sys.path.insert(0, str(ROOT / "sdk"))
from context_overlay_client import SDK_VERSION
from context_overlay import SCHEMA_VERSION, VERSION
from context_overlay.api import API_VERSION


DOCS = ("docs/quickstart.md", "docs/install.md", "docs/public-api-v2.md", "docs/public-api-v1.md",
        "docs/architecture.md", "docs/validation.md", "docs/development.md", "docs/future.md")


def check_links(contents):
    """Every relative Markdown link should work after extracting the bundle."""
    for name, data in contents.items():
        if not name.endswith(".md"):
            continue
        for link in re.findall(r"\]\(([^)]+)\)", data.decode("utf-8-sig")):
            parsed = urlsplit(link.strip("<>"))
            if parsed.scheme or not parsed.path:
                continue
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(parsed.path)))
            if target not in contents:
                raise ValueError("Missing linked file in bundle: {} -> {}".format(name, link))


def build_bundle(kind, root=ROOT):
    if kind not in ("windows", "sdk"):
        raise ValueError("Choose windows or sdk")
    files = sorted(path for path in (root / "sdk").rglob("*")
                   if path.is_file() and path.suffix in (".py", ".json", ".md"))
    files.extend(root / name for name in DOCS)
    metadata = {"channel": "internal_trial", "kind": kind, "sdk_version": SDK_VERSION,
                "api_version": API_VERSION, "api_major": int(API_VERSION.split(".")[0]),
                "schema_version": SCHEMA_VERSION, "module_version": VERSION}
    folder = "ContextOverlay-SDK-" + SDK_VERSION
    if kind == "windows":
        build = json.loads((root / "dist/build-manifest.json").read_text(encoding="utf-8"))
        sources = {path.relative_to(root / "src").as_posix(): sha256(path)
                   for path in (root / "src").rglob("*.py")}
        if sha256(root / "dist/ContextOverlay.ts4script") != build["package_sha256"] or sources != build["files"]:
            raise ValueError("Package or sources differ from build; rebuild before packaging")
        expected = {"module_version": VERSION, "public_api_version": API_VERSION, "schema_version": SCHEMA_VERSION}
        if any(build["build_info"].get(key) != value for key, value in expected.items()):
            raise ValueError("Build versions differ from current API; rebuild before packaging")
        metadata["build_game_version"] = build["build_info"]["build_game_version"]
        folder = "ContextOverlay-" + metadata["module_version"] + "-Windows"
        files.extend(root / name for name in ("README.md", "Install.cmd", "scripts/install.ps1",
                                              "dist/ContextOverlay.ts4script", "dist/build-manifest.json"))
    contents = {path.relative_to(root).as_posix(): path.read_bytes() for path in files}
    if kind == "sdk":
        contents["README.md"] = (
            "# ContextOverlay SDK {}\n\n这份只带 SDK、示例和文档，游戏脚本请另取 Windows 试用包。\n\n"
            "从[快速接入](docs/quickstart.md)开始，或直接看 [SDK 示例](sdk/README.md)。\n"
        ).format(SDK_VERSION).encode("utf-8")
    intro = ("完整解压这个 ZIP，退出游戏，再双击 Install.cmd。安装不用额外装 Python。\r\n"
             "请保留 dist 和 scripts 文件夹，不要解压里面的 .ts4script 文件。\r\n" if kind == "windows" else
             "这份只有 SDK、示例和文档；游戏脚本需要另外安装 Windows 试用包。\r\n")
    contents["README.txt"] = ("ContextOverlay 团队试用版\r\n\r\n" + intro +
        "\r\n先看 README.md，再跟着 docs/quickstart.md 接入自己的 Overlay。\r\n"
        "安装和游戏自检：docs/install.md\r\n完整接口参数：docs/public-api-v2.md\r\n"
        "示例不会自己启动，改好导入路径后从自己的游戏线程回调调用。\r\n"
    ).encode("utf-8-sig")
    check_links(contents)
    metadata["files"] = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(contents.items())}
    output = root / "dist" / (folder + ".zip")
    output.parent.mkdir(exist_ok=True)
    pending = output.with_suffix(".zip.pending")
    try:
        with ZipFile(str(pending), "w", compression=ZIP_DEFLATED) as archive:
            for name, data in sorted(contents.items()):
                archive.writestr(folder + "/" + name, data)
            archive.writestr(folder + "/manifest.json", json.dumps(metadata, indent=2))
        os.replace(str(pending), str(output))
    finally:
        if pending.exists():
            pending.unlink()
    digest = sha256(output)
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    return output, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("windows", "sdk"))
    args = parser.parse_args()
    try:
        output, digest = build_bundle(args.kind)
    except (OSError, ValueError, KeyError) as exc:
        raise SystemExit(str(exc))
    print(str(output))
    print("sha256=" + digest)


if __name__ == "__main__":
    main()
