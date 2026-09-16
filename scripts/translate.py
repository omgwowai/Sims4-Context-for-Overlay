"""Translate an exported packet with the pure semanticizer."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_overlay.semanticizer import translate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--strings", type=Path, help="Local CHS_CN.json for name resolution")
    parser.add_argument("--catalog", type=Path, help="Typed name catalog from build_name_catalog.py")
    parser.add_argument("--string-sources", type=Path, help="Matched string_sources.json from build_resource_catalog.py")
    args = parser.parse_args()
    if args.output.resolve() in {path.resolve() for path in (args.input, args.strings, args.catalog, args.string_sources) if path}:
        raise SystemExit("Use a separate output path to retain the original evidence")
    packet = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if bool(args.strings) != bool(args.catalog):
        raise SystemExit("Use --strings and --catalog together")
    if args.string_sources and not args.strings:
        raise SystemExit("--string-sources requires --strings")
    catalog = None
    if args.catalog:
        from context_overlay.localization import Localizer
        from context_overlay.name_catalog import NameCatalog
        import hashlib
        metadata = json.loads(args.string_sources.read_text(encoding="utf-8")) if args.string_sources else None
        if metadata and metadata.get("strings_sha256") != hashlib.sha256(args.strings.read_bytes()).hexdigest():
            raise SystemExit("String source metadata does not match dictionary")
        catalog = NameCatalog(json.loads(args.catalog.read_text(encoding="utf-8")),
                              Localizer(json.loads(args.strings.read_text(encoding="utf-8")), metadata))
    result = translate(packet, catalog=catalog)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(args.output))


if __name__ == "__main__":
    main()
