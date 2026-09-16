"""Resource provenance, independent text roles, and conservative re-rendering."""

import copy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from context_overlay.ea_adapter import EAAdapter
from context_overlay.localization import Localizer, snapshot_localized
from context_overlay.name_catalog import NameCatalog
from context_overlay.semanticizer import translate

spec = importlib.util.spec_from_file_location("build_resources", str(ROOT / "scripts/build_resource_catalog.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def record(identifier, tgi, priority, sha):
    return {"source_id": identifier, "tgi": tgi, "priority": priority, "sha256": sha, "type": builder.STBL}


class ResourceBuildChecks(unittest.TestCase):
    def test_cfg_priorities_are_local_and_conditionals_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Resource.cfg"
            path.write_text('Priority -30\nPackedFile "Strings_*.package"\nSelect CONSOLE\nPriority 900\nPackedFile console.package\nEnd\nPriority -9\nPackedFile delta.package\n', encoding="utf-8")
            rules = builder.parse_config(path)
            self.assertEqual([(x["pattern"], x["priority"]) for x in rules], [("Strings_*.package", -30), ("delta.package", -9)])
            path.write_text("PackedFile Strings.package", encoding="utf-8")
            with self.assertRaises(ValueError):
                builder.parse_config(path)

    def test_whole_resource_override_precedes_string_key_merge(self):
        records = [record("base", "A", -30, "old"), record("delta", "A", -9, "new")]
        strings, keys = builder.merge_strings(records, builder.select_resources(records),
                                             {"base": {"K": "old", "removed": "obsolete"}, "delta": {"K": "new"}})
        self.assertEqual(strings, {"K": "new"})
        self.assertEqual(keys["removed"]["status"], "overridden_only")
        self.assertEqual(len(keys["K"]["candidates"]), 2)
        self.assertEqual(keys["K"]["sources"], ["delta"])

    def test_equal_priority_and_cross_resource_conflicts_have_no_path_winner(self):
        for records in ([record("first", "A", 10, "x"), record("last", "A", 10, "y")],
                        [record("first", "A", 10, "x"), record("last", "B", 20, "y")]):
            strings, keys = builder.merge_strings(records, builder.select_resources(records),
                                                  {"first": {"K": "first"}, "last": {"K": "last"}})
            self.assertNotIn("K", strings)
            self.assertTrue(keys["K"]["status"].endswith("conflict"))
        records = [record("a", "A", 1, "same"), record("b", "A", 1, "same")]
        self.assertEqual(builder.select_resources(records)["A"]["selected"], ["a", "b"])

    def test_stbl_requires_complete_utf8_and_preserves_conflicting_key_error(self):
        def stbl(entries):
            header = b"STBL" + struct.pack("<HBQHI", 5, 0, len(entries), 0, 0)
            return header + b"".join(struct.pack("<IBH", key, 0, len(text)) + text for key, text in entries)
        data = stbl([(1, "名称".encode("utf-8"))])
        self.assertEqual(builder.parse_stbl(data), {"0x00000001": "名称"})
        for broken in (data[:-1], data + b"x", stbl([(1, b"\xff")]), stbl([(1, b"a"), (1, b"b")])):
            with self.assertRaises((ValueError, UnicodeError)):
                builder.parse_stbl(broken)

    def test_explicit_roles_shared_references_and_list_indices(self):
        root = ET.fromstring('''<combined><g s="merged"><T x="1">0x00000001</T>
            <U x="2"><V n="instance_display_name" t="enabled_display_name"><r x="1"/></V>
              <V n="instance_display_description" t="enabled_display_description"><T>2</T></V>
              <V n="instance_display_tooltip" t="enabled_display_tooltip"><T>3</T></V></U></g>
            <R n="aspiration"><I s="42" n="milestone" c="Aspiration"><V n="_display_data" t="optional_display_mixin"><r x="2"/></V></I></R>
            <R n="mood"><I s="50" n="mood" c="Mood"><L n="mood_names"><T>1</T><T>2</T></L></I></R>
            <R n="buff"><I s="9" n="hidden" c="Buff"/></R></combined>''')
        entries = builder.extract_tuning(root, "source")
        fields = entries["aspiration:42"]["fields"]
        self.assertEqual([fields[k][0]["hash"] for k in ("name", "description", "tooltip")], ["0x00000001", "0x00000002", "0x00000003"])
        self.assertEqual(entries["mood:50"]["fields"]["name"][1]["index"], 1)
        self.assertEqual(entries["buff:9"]["field_status"]["name"], "no_explicit_link")
        with self.assertRaises(ValueError):
            builder.extract_tuning(ET.fromstring('<c><R n="buff"><I s="1"><r n="buff_name" x="missing"/></I></R></c>'), "s")


class SemanticRuntimeChecks(unittest.TestCase):
    def test_empty_pronoun_slots_fall_back_but_custom_and_neutral_do_not(self):
        localizer = Localizer({"0x00000001": "{m0.孙子}{f0.孙女}"})
        token = {"type": 1, "is_female": True, "packed_pronouns": "|||||"}
        self.assertEqual(localizer.name({"hash": 1, "tokens": [token]})["text"], "孙女")
        for changed in (dict(token, packed_pronouns="they|them|||"), dict(token, gender_flags=0x3000)):
            self.assertEqual(localizer.name({"hash": 1, "tokens": [changed]})["status"], "unresolved_tokens")

    def test_date_evidence_is_preserved_without_guessing_calendar_format(self):
        data = {"hash": 1, "tokens": [{"type": 6, "date_and_time": {"hours": 17, "minutes": 25, "month": 0, "full_year": 0, "date_and_time_format_hash": 2}}]}
        evidence = snapshot_localized(data)
        self.assertEqual(evidence["tokens"][0]["date_and_time"], data["tokens"][0]["date_and_time"])
        self.assertEqual(Localizer({"0x00000001": "{0.TimeShort}"}).from_evidence(evidence)["status"], "unresolved_tokens")

    def test_object_description_is_read_only_on_context_identity(self):
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.localizer = Localizer({"0x00000001": "{0.ObjectDescription}", "0x00000002": "目录说明"})
        adapter.reference = lambda obj: {"id": "10", "name": "物件"}
        helper = SimpleNamespace(get_object_description=lambda obj: {"hash": 1, "tokens": [{"type": 5, "catalog_description_key": 2}]})
        with patch.dict(sys.modules, {"sims4": SimpleNamespace(), "sims4.localization": SimpleNamespace(LocalizationHelperTuning=helper)}):
            value = adapter.read_identity(SimpleNamespace(is_sim=False))["value"]
        self.assertEqual(value["description"]["text"], "目录说明")

    def test_source_conflicts_cannot_be_hidden_by_a_supplied_flat_dictionary(self):
        metadata = {"format": "string_sources_v1", "keys": {"0x00000001": {"status": "cross_resource_conflict", "sources": ["a", "b"]}}}
        localizer = Localizer({"0x00000001": "arbitrary"}, metadata)
        self.assertEqual(localizer.name(1)["reason"], "string_resource_conflict")
        metadata["keys"]["0x00000001"]["status"] = "overridden_only"
        self.assertEqual(localizer.name(1)["reason"], "string_key_overridden")

    def test_compact_sources_are_shared_in_memory_but_output_is_detached(self):
        keys = {"0x00000001": {"status": "selected", "sources": ["a"]}, "0x00000002": {"status": "selected", "sources": ["a"]}}
        metadata = dict(builder.compact_sources(keys), format="string_sources_v1")
        self.assertEqual(len(metadata["groups"]), 1)
        localizer = Localizer({"0x00000001": "A"}, metadata)
        result = localizer.name(1)
        result["string_source"]["sources"].append("corrupted")
        self.assertEqual(localizer.name(1)["string_source"]["sources"], ["a"])

    def test_object_catalog_verbs_ignore_custom_overrides(self):
        localizer = Localizer({"0x00000001": "{0.ObjectName}/{0.ObjectCatalogName}/{0.ObjectDescription}/{0.ObjectCatalogDescription}",
                               "0x00000002": "目录名", "0x00000003": "目录说明"})
        result = localizer.name({"hash": 1, "tokens": [{"type": 5, "catalog_name_key": 2, "catalog_description_key": 3,
                                                       "custom_name": "自定义名", "custom_description": "自定义说明"}]})
        self.assertEqual(result["text"], "自定义名/目录名/自定义说明/目录说明")
        self.assertEqual(result["status"], "resolved")

    def test_sim_objectname_and_subtoken_evidence(self):
        localizer = Localizer({"0x00000001": "{0.ObjectName}"})
        self.assertEqual(localizer.name({"hash": 1, "tokens": [{"type": 1, "first_name": "A"}]})["text"], "A")
        evidence = snapshot_localized({"hash": 1, "tokens": [{"type": 9, "sim_list": [{"first_name": "A", "age_flags": 8}]}]})
        self.assertEqual(evidence["tokens"][0]["sim_list"][0], {"type": "SIM", "first_name": "A", "age_flags": 8})
        self.assertEqual(localizer.from_evidence(evidence)["status"], "unresolved_tokens")

    def test_runtime_descriptions_use_own_tokens_and_isolate_failure(self):
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.localizer = Localizer({"0x00000001": "名称", "0x00000002": "说明：{0.String}"})
        buff = type("buff", (), {"guid64": 1, "buff_name": 1,
                                 "buff_description": {"hash": 2, "tokens": [{"type": 3, "raw_text": "自身参数"}]}})
        resource = adapter.resource(buff, resource_kind="buff", tokens=("unrelated name token",))
        self.assertEqual(resource["description"]["text"], "说明：自身参数")
        self.assertEqual(resource["name"]["text"], "名称")
        def fail(*args):
            raise ValueError("detail unavailable")
        broken = type("buff", (), {"guid64": 1, "buff_name": 1, "buff_description": staticmethod(fail)})
        resource = adapter.resource(broken, resource_kind="buff")
        self.assertEqual(resource["description"]["reason"], "detail_read_failed")
        self.assertEqual(resource["name"]["status"], "resolved")

    def test_v2_refreshes_text_without_rewriting_history_or_reusing_name_tokens(self):
        root = ET.fromstring('<c><R n="buff"><I s="42" n="buff"><T n="buff_name">1</T><T n="buff_description">2</T></I></R></c>')
        data = {"format": "typed_resource_semantics_v2", "entries": builder.extract_tuning(root, "s")}
        catalog = NameCatalog(data, Localizer({"0x00000001": "新名称", "0x00000002": "关于{0.SimFirstName}"}))
        old = {"id": "42", "resource_kind": "buff", "tuning_name": "buff", "name": {
            "text": "旧名称", "status": "resolved", "localization": {"hash": 1, "tokens": [{"type": "SIM", "first_name": "Alice"}]}}}
        packet = {"target": old, "snapshot": {}, "history": {"events": []}}
        before = copy.deepcopy(packet)
        result = translate(packet, catalog)
        self.assertEqual(packet, before)
        self.assertEqual(result["target"], before["target"])
        enriched = catalog.enrich(old)
        self.assertEqual(enriched["name"]["text"], "新名称")
        reference = enriched["reference_semantics"]["fields"]["description"]["alternatives"][0]
        self.assertEqual(reference["rendered"]["status"], "unresolved_tokens")
        self.assertNotIn("Alice", reference["rendered"]["text"])
        self.assertEqual(reference["tokens_basis"], "not_captured_for_this_field")

    def test_absent_static_field_does_not_claim_no_game_name_and_variants_not_selected(self):
        root = ET.fromstring('<c><R n="mood"><I s="1" n="m"><L n="mood_names"><T>1</T><T>2</T></L></I></R><R n="buff"><I s="2" n="b"/></R></c>')
        catalog = NameCatalog({"format": "typed_resource_semantics_v2", "entries": builder.extract_tuning(root, "s")}, Localizer({}))
        self.assertEqual(catalog.resolve("buff", "2", "b", {"text": "b", "status": "unmapped"})["reason"], "no_explicit_name_link")
        self.assertEqual(catalog.resolve("mood", "1", "m", {"text": "m", "status": "unmapped"})["reason"], "static_name_variant_not_selected")
        explicit_absence = {"text": "b", "status": "no_display_name", "localization": {"hash": None, "tokens": []}}
        self.assertEqual(catalog.resolve("buff", "2", "b", explicit_absence)["status"], "no_display_name")


if __name__ == "__main__":
    unittest.main()
