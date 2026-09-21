"""Read runtime outputs, re-interpret resource text and compose offline reports."""

import hashlib
import json

from tool_support import ROOT
from context_overlay.localization import Localizer
from context_overlay.model import copy_data
from context_overlay.profiles import resource_name
from context_overlay.semanticizer import render
from context_overlay.view_source import LINE_LIMIT, record_digest, validate_record


def read_json(path):
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8-sig")), hashlib.sha256(raw).hexdigest()


def read_journal(path, event_scope="retained", include_observations=True):
    """Strictly replay records; retained applies FIFO only after source validation."""
    events, metadata = {}, {}
    digests, revisions = {}, {}
    observations, errors = [], []
    session_id, next_sequence = None, 1
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for line_number, line in enumerate(iter(lambda: stream.readline(LINE_LIMIT + 1), b""), 1):
            digest.update(line)
            try:
                record = validate_record(line, session_id, next_sequence, digests.get, revisions.get)
                if record is None:
                    continue
                digests[next_sequence] = record_digest(record)
                session_id = record["session_id"]
                next_sequence += 1
                for key in ("module_version", "schema_version", "recorded_at"):
                    if key in record and key not in metadata:
                        metadata[key] = record[key]
                if record["kind"] == "event_revision":
                    event = record["event"]
                    # Validate every revision even after the visible FIFO evicts it.
                    revisions[event["event_id"]] = event["revision"]
                    if event_scope == "retained":
                        for removed in record.get("evicted_event_ids", []):
                            events.pop(removed, None)
                    events[event["event_id"]] = event
                elif include_observations:
                    observations.append(record)
            except (ValueError, KeyError, TypeError) as exc:
                errors.append({"line": line_number, "error": str(exc)})
                break
        # Hash the complete input even when a damaged record ends validation.
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if session_id is None and not errors:
        errors.append({"error": "Empty journal"})
    return {"session_id": session_id, "events": list(events.values()), "observations": observations,
            "errors": errors, "complete": not errors, "event_scope": event_scope,
            "metadata": metadata, "sha256": digest.hexdigest(), "last_sequence": next_sequence - 1}


def read_packet(path, event_scope="all"):
    if path.suffix.lower() == ".jsonl":
        loaded = read_journal(path, event_scope, include_observations=False)
        if not loaded["complete"]:
            raise ValueError("Invalid journal: " + str(loaded["errors"]))
        return dict(loaded["metadata"], kind="history", session_id=loaded["session_id"],
                    history={"events": loaded["events"], "scope": event_scope + "_recorded_events_latest_revision"}), loaded["sha256"]
    return read_json(path)


def load_catalog(strings, catalog, string_sources=None):
    if bool(strings) != bool(catalog) or (string_sources and not strings):
        raise ValueError("Use --strings and --catalog together; --string-sources requires both")
    if catalog is None:
        return None, {}
    texts, texts_hash = read_json(strings)
    data, catalog_hash = read_json(catalog)
    metadata, metadata_hash = read_json(string_sources) if string_sources else (None, None)
    if metadata is not None and metadata.get("strings_sha256") != texts_hash:
        raise ValueError("String source metadata does not match dictionary")
    return NameCatalog(data, Localizer(texts, metadata)), {
        "strings_sha256": texts_hash, "catalog_sha256": catalog_hash, "string_sources_sha256": metadata_hash}


class NameCatalog:
    def __init__(self, data, localizer):
        if data.get("format") != "typed_resource_semantics_v2":
            raise ValueError("Unsupported name catalog")
        self.data = data
        self.localizer = localizer
        self._reference_identities = {}
        self._conflicting_reference_ids = {key.rsplit(":", 1)[-1] for key in data.get("conflicts", {})}
        for key, entry in data["entries"].items():
            identity = (key.rsplit(":", 1)[-1], entry["tuning_name"])
            self._reference_identities.setdefault(identity, []).append(key)

    def resolve(self, kind, identifier, tuning_name, recorded):
        if not isinstance(recorded, dict) or recorded.get("status") == "raw_text":
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
        entry = self.data["entries"].get(str(kind) + ":" + str(identifier))
        if entry is None or entry["tuning_name"] != tuning_name:
            return recorded
        links = entry.get("fields", {}).get("name", [])
        if links:
            # A list index or alternative is not evidence of the active variant.
            if len(links) != 1 or "index" in links[0]:
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
                if value.get("event_type") == "external_event":
                    return
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
                    if isinstance(value.get("name"), dict):
                        label = value["name"]
                        value["name"] = self.resolve(None, None, label.get("fallback") or label.get("text"), label)
                if semantics is not None:
                    value["reference_semantics"] = semantics
                # Re-render captured details using their own evidence, not name tokens.
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


def translate(packet, catalog=None):
    result = copy_data(packet)
    view = catalog.enrich(packet) if catalog is not None else packet
    result["rendered"] = render(view)
    if catalog is not None:
        result["semantic_view"] = {key: value for key, value in view.items() if key in ("snapshot", "history", "target")}
        for item in result["rendered"]["resource_details"]["items"]:
            if item["evidence_ref"].split(".", 1)[0] in result["semantic_view"]:
                item["evidence_ref"] = "semantic_view." + item["evidence_ref"]
        resolution = {"catalog_format": catalog.data["format"],
            "catalog_inputs": catalog.data.get("inputs", {}),
            "catalog_provenance": catalog.data.get("provenance", {}),
            "historical_facts_preserved": True}
        for name, data in (("catalog", catalog.data), ("strings", catalog.localizer.strings),
                           ("string_sources", catalog.localizer.metadata)):
            resolution[name + "_content_sha256"] = hashlib.sha256(json.dumps(data, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        result["rendered"]["name_resolution"] = resolution
    return result
