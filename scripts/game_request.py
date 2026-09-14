"""Send one request to the opt-in local test driver and read its response."""

import argparse
import json
import os
import time
import uuid
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path, help="JSON file containing an allowlisted validation operation")
    parser.add_argument("--user-data", type=Path, default=Path("C:/Users/ZixuanMin/Documents/Electronic Arts/The Sims 4"))
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8-sig"))
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
