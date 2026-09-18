"""Offline compatibility entry point for the shared experience core."""

import argparse
import hashlib
import json
from pathlib import Path

from tool_support import ROOT, report, write_text
from tool_support import report as emit_report
from offline import read_journal
from context_overlay.experience.experience_recap import *


def load_source(path):
    if path.suffix.lower() != ".jsonl":
        raise ValueError("A complete, stable journal.jsonl is required")
    loaded = read_journal(path, "all", include_observations=False)
    if not loaded["complete"]:
        raise ValueError("Invalid journal: " + str(loaded["errors"]))
    # read_journal tolerates retried records; additionally reject mixed sessions and
    # writes during replay. The second pass hashes exactly the records it checks.
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for line in stream:
            sha.update(line)
            record = json.loads(line.decode("utf-8"))
            if record["session_id"] != loaded["session_id"]:
                raise ValueError("Mixed journal sessions")
            if record.get("kind") == "event_revision" and not record["event"]["event_id"].startswith(loaded["session_id"] + ":"):
                raise ValueError("Event belongs to another session")
    if sha.hexdigest() != loaded["sha256"]:
        raise ValueError("Journal changed while reading; freeze a copy first")
    return loaded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("input", type=Path)
    build.add_argument("--entity", required=True)
    build.add_argument("--game-version")
    build.add_argument("--output", type=Path, required=True, help="Full immutable detail bundle")
    build.add_argument("--recap", type=Path, help="Only the default consumer input")
    build.add_argument("--markdown", type=Path)
    build.add_argument("--token-encoding")
    query = commands.add_parser("query")
    query.add_argument("input", type=Path, help="Detail bundle")
    query.add_argument("--snapshot", required=True)
    query.add_argument("--ref", required=True)
    query.add_argument("--facet", choices=("units", "decisions", "evidence", "raw", "links", "labels"), default="units")
    query.add_argument("--offset", type=int, default=0)
    query.add_argument("--limit", type=int, default=20)
    query.add_argument("--journal", type=Path)
    query.add_argument("--output", type=Path)
    args = parser.parse_args()
    paths = [getattr(args, key, None) for key in ("input", "output", "recap", "markdown", "journal")]
    paths = [p.resolve() for p in paths if p is not None]
    if len(paths) != len(set(paths)):
        raise ValueError("Input and output paths must be distinct")
    if args.command == "build":
        bundle = build_recap(load_source(args.input), args.entity, args.game_version)
        if args.token_encoding:
            import tiktoken
            enc = tiktoken.get_encoding(args.token_encoding)
            bundle["metrics"].update(recap_tokens=len(enc.encode(packed(bundle["recap"]), disallowed_special=())),
                markdown_tokens=len(enc.encode(markdown(bundle), disallowed_special=())), token_encoding=args.token_encoding,
                tiktoken_version=tiktoken.__version__)
        report(bundle, bundle["metrics"], args.output, [args.input])
        if args.recap:
            write_text(args.recap, packed(bundle["recap"]) + "\n", paths[:1])
        if args.markdown:
            write_text(args.markdown, markdown(bundle), paths[:1])
    else:
        bundle = json.loads(args.input.read_text(encoding="utf-8"))
        result = resolve(bundle, args.snapshot, args.ref, args.facet, args.offset, args.limit,
                         load_source(args.journal) if args.journal else None)
        if args.output:
            report(result, {k: v for k, v in result.items() if k != "items"}, args.output, [args.input, args.journal])
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
