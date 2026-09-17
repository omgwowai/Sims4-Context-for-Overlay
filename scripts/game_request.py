"""Send one request to the opt-in local test driver and read its response."""

import argparse
import json
import os
import time
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", help="Operation name or a JSON request file")
    parser.add_argument("parameters", nargs="*", help="key=value; JSON values become numbers, booleans, lists or objects")
    parser.add_argument("--user-data", type=Path, default=Path.home() / "Documents/Electronic Arts/The Sims 4")
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = Path(args.request)
    request = json.loads(source.read_text(encoding="utf-8-sig")) if source.is_file() else {"operation": args.request}
    for parameter in args.parameters:
        key, separator, value = parameter.partition("=")
        if not key or not separator:
            parser.error("Parameters must use key=value")
        try:
            request[key] = json.loads(value)
        except ValueError:
            request[key] = value
    request["request_id"] = uuid.uuid4().hex
    directory = args.user_data / "ContextOverlay/validation"
    if not directory.exists():
        raise SystemExit("The development driver has not started in a loaded zone")
    pending = directory / "request.pending"
    pending.write_text(json.dumps(request, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(str(pending), str(directory / "request.json"))
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        response_path = directory / "response.json"
        if response_path.exists():
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if response.get("request_id") == request["request_id"]:
                text = json.dumps(response, ensure_ascii=False, indent=2)
                if args.output:
                    args.output.write_text(text, encoding="utf-8")
                else:
                    print(text)
                if not response["ok"]:
                    raise SystemExit(1)
                return
        time.sleep(0.1)
    raise SystemExit("No matching game response before timeout; do not assume the operation did not execute")


if __name__ == "__main__":
    main()
