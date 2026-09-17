"""Build an auditable official-game semantic catalog from installed DBPFs.

Uses the read-only DBPF/packed-XML decoders from the explicitly supplied
sims4-python checkout. No Mods or alphabetical last-wins string merging.
"""

import argparse
from collections import Counter, defaultdict
import configparser
import hashlib
import io
import json
from pathlib import Path
import struct
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from context_overlay.semantic_fields import FIELDS

STBL = 0x220557DA
COMBINED = 0x62E94D38


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.write_bytes(data)
    return digest(data)


def parse_config(path):
    """Return unconditional PackedFile rules; never guess Select conditions."""
    priority, conditional, rules = None, 0, []
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split(None, 1)
        command, argument = parts[0].lower(), parts[1] if len(parts) == 2 else ""
        if command == "select":
            conditional += 1
        elif command == "end":
            conditional -= 1
            if conditional < 0:
                raise ValueError("Unmatched End in " + str(path))
        elif conditional:
            continue
        elif command == "priority":
            priority = int(argument)
        elif command == "packedfile":
            if priority is None:
                raise ValueError("PackedFile without priority: " + str(path))
            rules.append({"pattern": argument.strip('"').replace("\\", "/"),
                          "priority": priority, "line": number})
    if conditional:
        raise ValueError("Unclosed Select in " + str(path))
    return rules


def configured_packages(game):
    # Deliberately restrict this builder to official Client/Simulation configs.
    configs = [game / "Data/Client/Resource.cfg", game / "Data/Simulation/Resource.cfg"]
    configs += sorted(game.glob("*/ResourceClient.cfg")) + sorted(game.glob("*/ResourceSimulation.cfg"))
    configs += sorted(game.glob("Delta/*/ResourceClient.cfg")) + sorted(game.glob("Delta/*/ResourceSimulation.cfg"))
    packages, inputs = {}, {}
    for config in configs:
        if not config.is_file():
            continue
        relative = config.relative_to(game).as_posix()
        inputs[relative] = digest(config.read_bytes())
        for rule in parse_config(config):
            for path in sorted(config.parent.glob(rule["pattern"])):
                if not path.is_file() or not (path.name == "Strings_CHS_CN.package" or path.name.startswith("Simulation")):
                    continue
                key = path.relative_to(game).as_posix()
                evidence = dict(rule, config=relative)
                item = packages.setdefault(key, {"path": key, "priority": rule["priority"], "rules": []})
                item["priority"] = max(item["priority"], rule["priority"])
                item["rules"].append(evidence)
    missing = sorted(p.relative_to(game).as_posix() for p in game.rglob("Strings_CHS_CN.package")
                     if p.relative_to(game).as_posix() not in packages)
    if missing:
        raise ValueError("Unconfigured official language packages: " + repr(missing))
    return packages, inputs


def parse_stbl(data):
    if len(data) < 21 or data[:4] != b"STBL" or struct.unpack_from("<H", data, 4)[0] != 5:
        raise ValueError("Expected complete STBL v5 header")
    if data[6] != 0:
        raise ValueError("Unsupported internal STBL compression")
    count = struct.unpack_from("<Q", data, 7)[0]
    position, strings = 21, {}
    for _ in range(count):
        if position + 7 > len(data):
            raise ValueError("Truncated STBL entry")
        key, flags, size = struct.unpack_from("<IBH", data, position)
        position += 7
        if position + size > len(data):
            raise ValueError("Truncated STBL text")
        text = data[position:position + size].decode("utf-8", errors="strict")
        position += size
        key = "0x{:08X}".format(key)
        if key in strings and strings[key] != text:
            raise ValueError("Conflicting keys inside one STBL: " + key)
        strings[key] = text
    if position != len(data):
        raise ValueError("Unexpected trailing STBL data")
    return strings


def select_resources(records):
    """Resolve complete TGI resources before ever looking at string keys."""
    groups = defaultdict(list)
    for record in records:
        groups[record["tgi"]].append(record)
    result = {}
    for tgi, candidates in sorted(groups.items()):
        highest = max(item["priority"] for item in candidates)
        top = [item for item in candidates if item["priority"] == highest]
        conflict = len({item["sha256"] for item in top}) > 1
        result[tgi] = {"status": "same_priority_conflict" if conflict else "selected",
                       "selected": [] if conflict else [item["source_id"] for item in top],
                       "candidates": [{"source_id": item["source_id"], "priority": item["priority"],
                                       "sha256": item["sha256"]} for item in candidates]}
    return result


def merge_strings(records, selection, tables, audit=False):
    strings, keys = {}, {}
    for record in records:
        if record["type"] != STBL:
            continue
        decision = selection[record["tgi"]]
        selected = record["source_id"] in decision["selected"]
        for key, text in tables[record["source_id"]].items():
            item = keys.setdefault(key, {"status": "overridden_only", "sources": []})
            if audit:
                item.setdefault("candidates", []).append({"source_id": record["source_id"], "text": text,
                                                          "active": selected, "resource_status": decision["status"]})
            if decision["status"] != "selected":
                item["status"] = "resource_conflict"
            elif selected:
                item["sources"].append(record["source_id"])
                if item["status"] in ("overridden_only", "selected"):
                    item["status"] = "cross_resource_conflict" if key in strings and strings[key] != text else "selected"
                strings[key] = text
    keys = {key: keys[key] for key in sorted(keys)}
    return {key: strings[key] for key, item in keys.items() if item["status"] == "selected"}, keys


def compact_sources(keys):
    """Share provenance groups instead of allocating a dict/list per string."""
    groups, indices, lookup = [], {}, {}
    for key, value in keys.items():
        identity = (value["status"], tuple(value["sources"]))
        if identity not in indices:
            indices[identity] = len(groups)
            groups.append({"status": identity[0], "sources": list(identity[1])})
        lookup[key] = indices[identity]
    return {"keys": lookup, "groups": groups}


def dereference(node, shared, seen=()):
    if node.tag != "r":
        return node
    key = node.get("x")
    if key in seen or key not in shared:
        raise ValueError("Invalid merged tuning reference: " + str(key))
    return dereference(shared[key], shared, seen + (key,))


def unwrap(node, shared):
    node = dereference(node, shared)
    if node.get("o") == "true":
        return None
    if node.tag == "V":
        if not (node.get("t") or "").startswith(("enabled", "optional_display")):
            return None
        return unwrap(node[0], shared) if len(node) == 1 else None
    return node


def text_links(instance, path, shared):
    node = instance
    for part in path.split("."):
        node = unwrap(node, shared)
        if node is None:
            return []
        node = next((child for child in node if child.get("n") == part), None)
        if node is None:
            return []
    node = unwrap(node, shared)
    if node is None:
        return []
    nodes = [(index, unwrap(child, shared)) for index, child in enumerate(node)] if node.tag == "L" else [(None, node)]
    result = []
    for index, scalar in nodes:
        if scalar is None or scalar.tag != "T" or len(scalar):
            continue
        try:
            value = int(scalar.text or "", 0)
        except ValueError:
            continue
        if 0 < value <= 0xFFFFFFFF:
            link = {"hash": "0x{:08X}".format(value), "attribute": path}
            if index is not None:
                link["index"] = index
            result.append(link)
    return result


def extract_tuning(root, source_id):
    shared = {node.get("x"): node for group in root if group.tag == "g" for node in group}
    entries = {}
    for group in root:
        kind = group.get("n")
        if group.tag != "R" or kind not in FIELDS:
            continue
        for instance in group:
            if instance.tag != "I":
                continue
            fields = {role: [link for path in FIELDS[kind].get(role, ()) for link in text_links(instance, path, shared)]
                      for role in ("name", "description", "tooltip")}
            entry = {"tuning_name": instance.get("n"), "class": instance.get("c"), "fields": fields,
                     "field_status": {role: "explicit_links" if links else "no_explicit_link" for role, links in fields.items()},
                     "source_ids": [source_id]}
            entries[kind + ":" + instance.get("s")] = entry
    return entries


def build(game, reference, output, audit=False):
    sys.path.insert(0, str(reference / "tools"))
    from dbpf import read_index, read_resource
    from extract_tuning import CombinedTuning
    config = configparser.ConfigParser()
    config.read(str(game / "Game/Bin/Default.ini"), encoding="utf-8-sig")
    packages, configs = configured_packages(game)
    records, tables, tuning_locations = [], {}, {}
    for package in packages.values():
        path = game / package["path"]
        for entry in read_index(path):
            if entry.type not in (STBL, COMBINED):
                continue
            if entry.type == STBL and path.name != "Strings_CHS_CN.package":
                continue
            data = read_resource(path, entry)
            source_id = "r{:04d}".format(len(records))
            record = {"source_id": source_id, "package": package["path"], "priority": package["priority"],
                      "rules": package["rules"], "type": entry.type,
                      "tgi": "{:08X}:{:08X}:{:016X}".format(entry.type, entry.group, entry.instance),
                      "sha256": digest(data), "offset": entry.offset, "compressed_size": entry.size,
                      "size": len(data), "compression": entry.compression}
            records.append(record)
            if entry.type == STBL:
                tables[source_id] = parse_stbl(data)
            else:
                tuning_locations[source_id] = (path, entry)
    selection = select_resources(records)
    strings, keys = merge_strings(records, selection, tables, audit=audit)
    entries, conflicts = {}, {}
    for record in records:
        if record["type"] != COMBINED or record["source_id"] not in selection[record["tgi"]]["selected"]:
            continue
        data = read_resource(*tuning_locations[record["source_id"]])
        if data.startswith(b"<"):
            root = ET.fromstring(data)
        else:
            stream = io.StringIO()
            CombinedTuning(data).write(stream)
            root = ET.fromstring(stream.getvalue())
        for key, item in extract_tuning(root, record["source_id"]).items():
            previous = entries.get(key)
            if previous and any(previous[k] != item[k] for k in ("tuning_name", "class", "fields")):
                conflicts.setdefault(key, [previous]).append(item)
            elif previous:
                previous["source_ids"].extend(item["source_ids"])
            else:
                entries[key] = item
    for key in conflicts:
        entries.pop(key, None)
    provenance = {"game_version": config.get("Version", "gameversion"), "language": "CHS_CN",
                  "scope": "installed_official_resources", "runtime_pack_entitlement_not_verified": True,
                  "third_party_mods": "not_scanned", "configs": configs,
                  "builder_sha256": digest(Path(__file__).read_bytes()),
                  "field_map_sha256": digest((ROOT / "src/context_overlay/semantic_fields.py").read_bytes()),
                  "decoder_sha256": {name: digest((reference / "tools" / name).read_bytes())
                                     for name in ("dbpf.py", "extract_tuning.py")}}
    manifest = {"format": "official_resource_sources_v1", "provenance": provenance,
                "sources": {r["source_id"]: r for r in records}, "resources": selection,
                "counts": {"packages": len(packages), "stbl_resources": len(tables),
                           "strings": len(strings), "string_conflicts": sum(v["status"].endswith("conflict") for v in keys.values()),
                           "overridden_only_keys": sum(v["status"] == "overridden_only" for v in keys.values()),
                           "tuning_entries": len(entries), "tuning_conflicts": len(conflicts)},
                "policy": "Highest cfg Priority for identical TGI; tied different payloads unresolved. Distinct active TGI with differing text for a string key remain unresolved. No path-order tie breaker."}
    catalog = {"format": "typed_resource_semantics_v2", "provenance": provenance,
               "entries": entries, "conflicts": conflicts,
               "sources": manifest["sources"],
               "limits": ["No explicit XML field is not proof of no game text: defaults, inheritance and client-only fields are not expanded.",
                          "Static alternatives do not identify the runtime-selected variant or historical dynamic tokens."]}
    output.mkdir(parents=True, exist_ok=True)
    strings_hash = write_json(output / "strings_zh.json", strings)
    metadata = {"format": "string_sources_v1", "provenance": provenance, "sources": manifest["sources"],
                **compact_sources(keys), "strings_sha256": strings_hash}
    manifest["outputs"] = {"strings_zh.json": strings_hash,
                           "string_sources.json": write_json(output / "string_sources.json", metadata),
                           "resource_catalog.json": write_json(output / "resource_catalog.json", catalog)}
    audit_path = output / "string-candidates.json"
    if audit:
        manifest["outputs"][audit_path.name] = write_json(audit_path, keys)
    write_json(output / "manifest.json", manifest)
    if not audit and audit_path.exists():
        audit_path.unlink()
    print(json.dumps(manifest["counts"], sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, default=Path("D:/Games/The Sims 4"))
    parser.add_argument("--reference", type=Path, default=Path("C:/sources/sims4-python"))
    parser.add_argument("--output", type=Path, default=ROOT / ".local/resource-semantics")
    parser.add_argument("--audit", action="store_true", help="Also export all candidate strings for offline auditing")
    args = parser.parse_args()
    for source in (args.game.resolve(), args.reference.resolve()):
        if source == args.output.resolve() or source in args.output.resolve().parents:
            raise SystemExit("Output must be outside game and reference inputs")
    build(args.game, args.reference, args.output, audit=args.audit)


if __name__ == "__main__":
    main()
