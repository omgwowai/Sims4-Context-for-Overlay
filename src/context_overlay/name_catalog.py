"""Offline enrichment of recorded names from typed tuning links and STBL.

Historical packets retain their original facts. Enrichment is a separate view,
and missing old token evidence is never filled from today's game objects.
"""

import hashlib
import json

from context_overlay.localization import hash_key
from context_overlay.model import copy_data
from context_overlay.profiles import resource_name


class NameCatalog:
    def __init__(self, data, localizer):
        if data.get("format") != "typed_tuning_names_v1":
            raise ValueError("Unsupported name catalog")
        self.data = data
        self.localizer = localizer
        # Offline-only fingerprints of normalized inputs, independent of JSON
        # indentation or whether callers obtained the mappings from files.
        self.provenance = {
            key: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
            for key, value in (("catalog_content_sha256", data), ("strings_content_sha256", localizer.strings))}

    def resolve(self, kind, identifier, tuning_name, recorded):
        if not isinstance(recorded, dict) or recorded.get("status") in ("resolved", "raw_text"):
            return recorded
        raw = recorded.get("raw_name", recorded)
        if raw.get("reason") == "label_read_failed":
            return recorded
        evidence = raw.get("localization")
        if evidence:
            result = self.localizer.from_evidence(evidence, tuning_name)
            for key in ("source", "visible", "get_name_error"):
                if key in raw:
                    result[key] = raw[key]
            # Runtime evidence, including explicit absence or truncation, is
            # authoritative. Static tuning must not replace its missing facts.
            return resource_name(identifier, tuning_name, result)
        # An old nonzero hash is direct observation and takes precedence over a
        # static catalog, even if the current dictionary no longer contains it.
        key = hash_key(raw.get("hash"))
        if key and key != "0x00000000":
            result = self.localizer.from_evidence({"hash": key, "tokens": []}, tuning_name)
            result["source"] = {"kind": "recorded_hash", "tokens": "not_captured_in_old_record"}
            return result
        entry = self.data["entries"].get(str(kind) + ":" + str(identifier))
        if entry is None or entry["tuning_name"] != tuning_name:
            return recorded
        if entry["names"]:
            link = entry["names"][0]
            result = self.localizer.from_evidence({"hash": link["hash"], "tokens": []}, tuning_name)
            result["source"] = {"kind": "static_tuning_reference", "resource_kind": kind,
                                "attribute": link["attribute"], "source_file": entry["source_file"],
                                "runtime_override_not_verified": True}
        else:
            result = {"text": tuning_name, "status": "no_display_name", "reason": "no_name_in_reference_tuning",
                      "source": {"kind": "static_tuning_reference", "source_file": entry["source_file"],
                                 "runtime_override_not_verified": True}}
        result["visible"] = entry.get("visible")
        return resource_name(identifier, tuning_name, result)

    def enrich(self, packet):
        result = copy_data(packet)

        def walk(value, kind=None):
            if isinstance(value, list):
                for child in value:
                    walk(child, kind)
            elif isinstance(value, dict):
                event_kind = value.get("field")
                inferred = "buff" if event_kind == "buffs" else "relbit" if event_kind == "relationship.bits" else "object_state" if str(event_kind).startswith("object_states.") else kind
                if "tuning_id" in value and "tuning_name" in value:
                    value["name"] = self.resolve("interaction", value["tuning_id"], value["tuning_name"], value.get("name"))
                elif "tuning_name" in value and "id" in value:
                    resource_kind = value.get("resource_kind") or inferred
                    value["name"] = self.resolve(resource_kind, value["id"], value["tuning_name"], value.get("name"))
                for key, child in list(value.items()):
                    if key in ("name", "rendered", "localization", "raw_name", "semantic_view"):
                        continue
                    child_kind = {"buffs": "buff", "bits": "relbit", "object_states": "object_state", "resource": "statistic"}.get(key, inferred)
                    walk(child, child_kind)
        walk(result)
        return result
