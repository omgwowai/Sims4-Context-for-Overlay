"""Extract typed tuning-to-STBL links; never translate tuning-name substrings."""

import argparse
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path


KINDS = {"buff": ("buff_name",), "relbit": ("display_name",),
         "statistic": ("stat_name",), "interaction": ("display_name_in_queue", "display_name"),
         "object_state": ("_display_data",)}


def resolve(node, shared, seen=()):
    if node.tag != "r":
        return node
    key = node.get("x")
    if key in seen or key not in shared:
        raise ValueError("Invalid merged reference: " + str(key))
    return resolve(shared[key], shared, seen + (key,))


def scalar(node, shared):
    node = resolve(node, shared)
    if node.get("o") == "true":
        return None
    if node.tag == "V":
        if not (node.get("t") or "").startswith(("enabled", "optional_display")):
            return None
        return scalar(node[0], shared) if len(node) == 1 else None
    return node.text if node.tag == "T" and not len(node) else None


def display_data_key(node, shared):
    node = resolve(node, shared)
    if node.get("o") == "true":
        return None
    if node.tag == "V":
        if not (node.get("t") or "").startswith(("enabled", "optional_display")):
            return None
        return display_data_key(node[0], shared) if len(node) == 1 else None
    if node.tag == "U":
        for child in node:
            if child.get("n") == "instance_display_name":
                return scalar(child, shared)
    return None


def extract(paths):
    entries, conflicts, inputs = {}, set(), {}
    for path in paths:
        if path.name == "combined_tuning_BASEFull.xml":
            continue  # This is the 2014 launch snapshot, not the active BASE delta.
        inputs[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        root = ET.parse(str(path)).getroot()
        shared = {node.get("x"): node for group in root if group.tag == "g" for node in group}
        for group in root:
            kind = group.get("n")
            if group.tag != "R" or kind not in KINDS:
                continue
            for instance in group:
                if instance.tag != "I":
                    continue
                key = kind + ":" + instance.get("s")
                fields = {node.get("n"): node for node in instance}
                names = []
                for attr in KINDS[kind]:
                    if attr not in fields:
                        continue
                    text = display_data_key(fields[attr], shared) if attr == "_display_data" else scalar(fields[attr], shared)
                    if text is not None:
                        try:
                            value = int(text, 0)
                        except ValueError:
                            continue
                        if 0 < value <= 0xFFFFFFFF:
                            names.append({"hash": "0x{:08X}".format(value), "attribute": attr})
                visible = scalar(fields["visible"], shared) if "visible" in fields else None
                entry = {"tuning_name": instance.get("n"), "class": instance.get("c"),
                         "names": names, "visible": None if visible is None else visible == "True",
                         "source_file": path.name}
                if key in entries and entries[key] != entry:
                    conflicts.add(key)
                else:
                    entries[key] = entry
    for key in conflicts:
        entries.pop(key, None)
    return {"format": "typed_tuning_names_v1", "inputs": inputs,
            "excluded": ["combined_tuning_BASEFull.xml (2014 launch snapshot)"],
            "conflicts": sorted(conflicts), "entries": entries}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("tuning", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    paths = sorted(args.tuning.glob("combined_tuning_*.xml"))
    if not paths:
        raise SystemExit("No combined tuning XML found")
    if args.output.resolve() in {path.resolve() for path in paths}:
        raise SystemExit("Catalog must not replace source tuning")
    result = extract(paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    print("entries={} with_names={} conflicts={}".format(len(result["entries"]), sum(bool(x["names"]) for x in result["entries"].values()), len(result["conflicts"])))


if __name__ == "__main__":
    main()
