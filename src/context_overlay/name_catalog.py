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
        if data.get("format") not in ("typed_tuning_names_v1", "typed_resource_semantics_v2"):
            raise ValueError("Unsupported name catalog")
        self.data = data
        self.localizer = localizer
        self.is_v2 = data.get("format") == "typed_resource_semantics_v2"
        self._reference_identities = {}
        self._conflicting_reference_ids = {key.rsplit(":", 1)[-1] for key in data.get("conflicts", {})}
        if self.is_v2:
            for key, entry in data["entries"].items():
                identity = (key.rsplit(":", 1)[-1], entry["tuning_name"])
                self._reference_identities.setdefault(identity, []).append(key)
        # Offline-only fingerprints of normalized inputs, independent of JSON
        # indentation or whether callers obtained the mappings from files.
        self.provenance = {
            key: hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":")).encode("utf-8")).hexdigest()
            for key, value in (("catalog_content_sha256", data), ("strings_content_sha256", localizer.strings),
                               ("string_sources_content_sha256", localizer.metadata))}

    def resolve(self, kind, identifier, tuning_name, recorded):
        if not isinstance(recorded, dict) or recorded.get("status") == "raw_text" or (
                recorded.get("status") == "resolved" and not self.is_v2):
            return recorded
        raw = recorded.get("raw_name", recorded)
        if raw.get("reason") in ("label_read_failed", "no_verified_name_accessor"):
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
        links = entry.get("fields", {}).get("name", []) if self.is_v2 else entry["names"]
        if links:
            # A list index or alternative is not evidence of the active variant.
            if self.is_v2 and (len(links) != 1 or "index" in links[0]):
                return dict(recorded, status="unmapped", reason="static_name_variant_not_selected")
            link = links[0]
            result = self.localizer.from_evidence({"hash": link["hash"], "tokens": []}, tuning_name)
            result["source"] = {"kind": "static_tuning_reference", "resource_kind": kind,
                                "attribute": link["attribute"], "source_file": entry.get("source_file"),
                                "source_ids": entry.get("source_ids", []),
                                "runtime_override_not_verified": True}
        else:
            result = {"text": tuning_name, "status": "unmapped", "reason": "no_explicit_name_link",
                      "source": {"kind": "static_tuning_reference", "source_file": entry.get("source_file"),
                                 "source_ids": entry.get("source_ids", []),
                                 "runtime_override_not_verified": True}}
        result["visible"] = entry.get("visible")
        return resource_name(identifier, tuning_name, result)

    def reference_semantics(self, kind, identifier, tuning_name):
        """All explicit static alternatives; never imply a runtime selection."""
        if not self.is_v2:
            return None
        key = str(kind) + ":" + str(identifier)
        kind_basis = "recorded_or_field_type"
        if kind is None:
            if str(identifier) in self._conflicting_reference_ids:
                return None
            candidates = self._reference_identities.get((str(identifier), tuning_name), [])
            if len(candidates) != 1:
                return None
            key = candidates[0]
            kind_basis = "unique_catalog_identity_not_observed"
        if key in self.data.get("conflicts", {}):
            return {"status": "resource_conflict", "resource_key": key}
        entry = self.data["entries"].get(key)
        if entry is None or entry["tuning_name"] != tuning_name:
            return None
        result = {"resource_key": key, "source_ids": entry["source_ids"], "kind_basis": kind_basis,
                  "basis": "static_reference_not_historical_observation", "fields": {}}
        for role in ("name", "description", "tooltip"):
            links = entry["fields"].get(role, [])
            result["fields"][role] = {"status": "explicit_links" if links else "no_explicit_link",
                "alternatives": [dict(link, rendered=self.localizer.from_evidence({"hash": link["hash"], "tokens": []}),
                                      tokens_basis="not_captured_for_this_field") for link in links]}
        return result

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
                    semantics = self.reference_semantics("interaction", value["tuning_id"], value["tuning_name"])
                elif "tuning_name" in value and "id" in value:
                    resource_kind = value.get("resource_kind") or inferred
                    value["name"] = self.resolve(resource_kind, value["id"], value["tuning_name"], value.get("name"))
                    semantics = self.reference_semantics(resource_kind, value["id"], value["tuning_name"])
                else:
                    semantics = None
                    if self.is_v2 and isinstance(value.get("name"), dict):
                        label = value["name"]
                        value["name"] = self.resolve(None, None, label.get("fallback") or label.get("text"), label)
                if semantics is not None:
                    value["reference_semantics"] = semantics
                # Re-render captured details using their own evidence, not name tokens.
                if self.is_v2:
                    for role in ("description", "tooltip"):
                        recorded = value.get(role)
                        if isinstance(recorded, dict) and recorded.get("localization"):
                            value[role] = self.localizer.from_evidence(recorded["localization"], recorded.get("fallback"))
                            if value[role]["status"] == "no_display_name":
                                value[role]["status"] = "not_present"
                            if "source" in recorded:
                                value[role]["source"] = recorded["source"]
                for key, child in list(value.items()):
                    if key in ("name", "description", "tooltip", "reference_semantics", "rendered", "localization", "raw_name", "semantic_view"):
                        continue
                    child_kind = {"buffs": "buff", "bits": "relbit", "object_states": "object_state", "resource": "statistic"}.get(key, inferred)
                    walk(child, child_kind)
        walk(result)
        return result
