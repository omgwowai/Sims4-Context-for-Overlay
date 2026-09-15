"""Run with ordinary Python to preview synthetic input for an Overlay."""

import json
from pathlib import Path


def main():
    packet = json.loads(Path(__file__).with_name("context-packet.json").read_text(encoding="utf-8"))
    print("SYNTHETIC EXAMPLE - not game evidence")
    for row in packet["rendered"]["current"]:
        print(row["text"])


if __name__ == "__main__":
    main()
