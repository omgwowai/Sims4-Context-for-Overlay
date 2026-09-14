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
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise SystemExit("Use a separate output path to retain the original evidence")
    packet = json.loads(args.input.read_text(encoding="utf-8-sig"))
    result = translate(packet)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(args.output))


if __name__ == "__main__":
    main()
