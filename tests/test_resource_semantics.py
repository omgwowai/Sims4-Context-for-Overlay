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

from support import ROOT
from context_overlay.ea_adapter import EAAdapter
from context_overlay.semanticizer import display, detail_text, render, resource_details
from context_overlay.localization import Localizer, snapshot_localized
from offline import NameCatalog, translate

spec = importlib.util.spec_from_file_location("build_resources", str(ROOT / "scripts/build_resource_catalog.py"))
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def label(text, status="resolved", **extra):
    return dict(text=text, status=status, **extra)


def record(identifier, tgi, priority, sha):
    return {"source_id": identifier, "tgi": tgi, "priority": priority, "sha256": sha, "type": builder.STBL}


class ResourceBuildChecks(unittest.TestCase):
    def test_candidate_dump_is_opt_in_and_manifest_lists_only_current_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            game, reference = root / "game", root / "reference"
            (game / "Game/Bin").mkdir(parents=True)
            (game / "Game/Bin/Default.ini").write_text("[Version]\ngameversion = test\n")
            (reference / "tools").mkdir(parents=True)
            for name in ("dbpf.py", "extract_tuning.py"):
                (reference / "tools" / name).write_text("# synthetic decoder")
            entry = SimpleNamespace(type=builder.STBL, group=0, instance=1, offset=0, size=4, compression=0)
            modules = {"dbpf": SimpleNamespace(read_index=lambda path: [entry], read_resource=lambda *args: b"STBL"),
                       "extract_tuning": SimpleNamespace(CombinedTuning=None)}
            old_path = list(sys.path)
            self.addCleanup(lambda: sys.path.__setitem__(slice(None), old_path))
            packages = {"strings": {"path": "Strings_CHS_CN.package", "priority": 0, "rules": []}}
            with patch.dict(sys.modules, modules), patch.object(builder, "configured_packages", return_value=(packages, [])), \
                    patch.object(builder, "parse_stbl", return_value={"K": "text"}):
                output = root / "output"
                for audit in (False, True, False):
                    builder.build(game, reference, output, audit=audit)
                    self.assertEqual((output / "string-candidates.json").exists(), audit)
                    manifest = json.loads((output / "manifest.json").read_text())
                    self.assertEqual("string-candidates.json" in manifest["outputs"], audit)
                    self.assertEqual(len(manifest["outputs"]), 4 if audit else 3)
                    self.assertEqual(json.loads((output / "strings_zh.json").read_text()), {"K": "text"})
                    self.assertEqual(manifest["outputs"], {name: builder.digest((output / name).read_bytes())
                                                          for name in manifest["outputs"]})

    def test_cfg_priorities_are_local_and_conditionals_are_not_guessed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Resource.cfg"
            path.write_text('Priority -30\nPackedFile "Strings_*.package"\nSelect CONSOLE\nPriority 900\nPackedFile console.package\nEnd\nPriority -9\nPackedFile delta.package\n', encoding="utf-8")
            rules = builder.parse_config(path)
            self.assertEqual([(x["pattern"], x["priority"]) for x in rules], [("Strings_*.package", -30), ("delta.package", -9)])

    def test_whole_resource_override_precedes_string_key_merge(self):
        records = [record("base", "A", -30, "old"), record("delta", "A", -9, "new")]
        for audit in (False, True):
            strings, keys = builder.merge_strings(records, builder.select_resources(records),
                {"base": {"K": "old", "removed": "obsolete"}, "delta": {"K": "new"}}, audit=audit)
            self.assertEqual(strings, {"K": "new"})
            self.assertEqual(keys["removed"]["status"], "overridden_only")
            self.assertEqual(len(keys["K"].get("candidates", [])), 2 if audit else 0)
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

    def test_stbl_reads_utf8_names(self):
        encoded = "名称".encode("utf-8")
        data = b"STBL" + struct.pack("<HBQHI", 5, 0, 1, 0, 0)
        data += struct.pack("<IBH", 1, 0, len(encoded)) + encoded
        self.assertEqual(builder.parse_stbl(data), {"0x00000001": "名称"})

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


class SemanticRuntimeChecks(unittest.TestCase):
    def test_empty_pronoun_slots_fall_back_but_custom_and_neutral_do_not(self):
        localizer = Localizer({"0x00000001": "{m0.孙子}{f0.孙女}"})
        token = {"type": 1, "is_female": True, "packed_pronouns": "|||||"}
        self.assertEqual(localizer.name({"hash": 1, "tokens": [token]})["text"], "孙女")
        for changed in (dict(token, packed_pronouns="they|them|||"), dict(token, gender_flags=0x3000)):
            self.assertEqual(localizer.name({"hash": 1, "tokens": [changed]})["status"], "unresolved_tokens")

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
        from test_translate import report
        self.assertIn("目标：新名称", report.markdown_report(packet, Path("packet.json"), catalog=catalog))
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


class ResourceAccessChecks(unittest.TestCase):
    def setUp(self):
        self.adapter = EAAdapter.__new__(EAAdapter)
        self.adapter.localizer = Localizer({"0x00000001": "紧张", "0x00000002": "非常紧张", "0x00000003": "食物", "0x00000004": ""})

    def test_service_id_is_not_a_world_entity(self):
        class BaseObject:
            id = 3
        self.adapter.reference = lambda obj: {"kind": "object", "id": str(obj.id)}
        with patch.dict(sys.modules, {"objects.base_object": SimpleNamespace(BaseObject=BaseObject)}):
            broadcaster = SimpleNamespace(id=193, guid64=444)
            self.assertIsNone(self.adapter.event_reference(broadcaster))
            self.assertIsNone(self.adapter.event_reference(SimpleNamespace(id=4)))
            self.assertEqual(self.adapter.event_reference(BaseObject())["id"], "3")
        self.assertEqual(self.adapter.event_reference(SimpleNamespace(sim_id=8))["kind"], "sim")

    def test_recipe_mood_and_source_action_use_native_accessors(self):
        mood = type("Mood_Tense", (), {"guid64": 20, "mood_names": [1, 2]})
        self.assertEqual(self.adapter.resource(mood, resource_kind="mood", intensity=1)["name"]["text"], "非常紧张")
        base = self.adapter.resource(mood, resource_kind="mood")
        self.assertEqual(base["name"]["text"], "紧张")
        self.assertEqual(base["name"]["source"]["name_basis"], "base_mood_name")
        self.assertEqual(self.adapter.resource(mood, resource_kind="mood", intensity=99)["name"]["status"], "no_display_name")
        recipe = type("Recipe", (), {"guid64": 30, "get_recipe_name": staticmethod(lambda *tokens: 3)})
        self.assertEqual(self.adapter.resource(recipe, resource_kind="recipe")["name"]["text"], "食物")
        trait = type("Trait", (), {"guid64": 31, "trait_type": 1, "display_name": staticmethod(lambda *tokens: 3)})
        self.assertEqual(self.adapter.resource(trait)["resource_kind"], "trait")
        self.assertEqual(self.adapter.resource(trait)["name"]["text"], "食物")
        unknown = type("Unknown", (), {"guid64": 32})
        self.assertEqual(self.adapter.resource(unknown)["name"]["reason"], "no_verified_name_accessor")
        action = SimpleNamespace(guid64=40, target="food", context="context", get_name=lambda target, context: 3)
        self.assertEqual(self.adapter.resource(action, resource_kind="interaction")["name"]["text"], "食物")

    def test_mood_proto_name_uses_the_observed_level_and_native_tokens(self):
        mood = type("Mood_Tense", (), {"guid64": 20, "mood_names": [SimpleNamespace(hash=1), SimpleNamespace(hash=2)]})
        seen = []
        def create(key, *tokens):
            seen.append((key, tokens))
            return {"hash": key, "tokens": []}
        with patch.dict(sys.modules, {"sims4.localization": SimpleNamespace(_create_localized_string=create)}):
            value = self.adapter.resource(mood, resource_kind="mood", intensity=1, tokens=("subject",))
        self.assertEqual(value["name"]["text"], "非常紧张")
        self.assertEqual(seen, [(2, ("subject",))])

    def test_empty_localized_string_keeps_evidence_and_readable_fallback(self):
        value = self.adapter.localizer.name(4, "MoodBuff_Internal")
        self.assertEqual(value["status"], "empty_display_name")
        self.assertEqual(value["localization"]["hash"], "0x00000004")
        self.assertEqual(display(value), "MoodBuff_Internal（显示文本为空）")


class DescriptionChecks(unittest.TestCase):
    def setUp(self):
        self.resource = {"id": "42", "resource_kind": "buff", "tuning_name": "hidden",
            "name": label("hidden", "no_display_name"), "description": label("环境会影响心情。"),
            "tooltip": label("需要满足条件。", source={"condition_evaluated": False})}

    def test_description_deduplication_retains_evidence_without_renaming(self):
        packet = {"snapshot": {"buffs": {"status": "available", "value": [self.resource, copy.deepcopy(self.resource)]}}}
        before = copy.deepcopy(packet)
        result = render(packet)["resource_details"]
        self.assertEqual(len(result["items"]), 2)
        description = result["items"][0]
        self.assertEqual(description["evidence_ref"], "snapshot.buffs.value[0].description")
        self.assertEqual(description["text"], "环境会影响心情。")
        self.assertIn("未判断触发条件", detail_text(self.resource))
        self.assertEqual(packet, before)
        self.assertFalse(result["truncated"])

    def test_reference_identity_fallback_is_static_and_ambiguous_types_are_rejected(self):
        entry = {"tuning_name": "mood", "source_ids": ["official"], "fields": {
            "name": [{"attribute": "mood_names", "hash": "0x00000001", "index": 0}],
            "description": [{"attribute": "descriptions", "hash": "0x00000002", "index": 0}]}}
        data = {"format": "typed_resource_semantics_v2", "entries": {"mood:10": entry}}
        localizer = Localizer({"0x00000001": "高兴", "0x00000002": "{0.SimFirstName}感觉很好。"})
        catalog = NameCatalog(data, localizer)
        old = {"id": "10", "tuning_name": "mood", "name": label("mood", "no_display_name",
               localization={"hash": None, "tokens": []})}
        packet = {"target": old}
        result = translate(packet, catalog)
        self.assertEqual(result["target"], old)
        details = result["rendered"]["resource_details"]["items"]
        self.assertEqual(details[0]["text"], "高兴")
        self.assertEqual(details[0]["evidence_ref"], "semantic_view.target.reference_semantics.fields.name.alternatives[0].rendered")
        self.assertTrue(all(x["basis"] == "static_reference_not_historical_observation" for x in details))
        self.assertEqual(details[1]["status"], "unresolved_tokens")
        self.assertEqual(result["semantic_view"]["target"]["reference_semantics"]["kind_basis"],
                         "unique_catalog_identity_not_observed")
        data["entries"]["buff:10"] = copy.deepcopy(entry)
        self.assertIsNone(NameCatalog(data, localizer).reference_semantics(None, "10", "mood"))

    def test_description_limits_report_truncation_and_keep_partial_templates(self):
        value = [dict(self.resource, id=str(i)) for i in range(8)]
        result = resource_details(value, limit=3)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["items"]), 3)
        self.resource["description"] = label("关于〈未解析：0.String〉", "unresolved_tokens")
        self.assertIn("未完整解析", detail_text(self.resource))

    def test_mood_base_description_never_guesses_intensity_or_client_overrides(self):
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.localizer = Localizer({"0x00000001": "高兴", "0x00000002": "基础说明", "0x00000003": "更强的基础说明"})
        mood = type("mood", (), {"guid64": 10, "mood_names": [1, 1], "descriptions": [2, 3]})
        unknown = adapter.resource(mood, resource_kind="mood")
        self.assertEqual(unknown["description"]["reason"], "description_variant_not_selected")
        known = adapter.resource(mood, resource_kind="mood", intensity=1)
        self.assertEqual(known["description"]["text"], "更强的基础说明")
        self.assertFalse(known["description"]["source"]["client_overrides_evaluated"])
        self.assertIn("未判断年龄或特征覆盖", detail_text(known))


class ResourceNameChecks(unittest.TestCase):
    def setUp(self):
        self.adapter = EAAdapter.__new__(EAAdapter)
        self.adapter.localizer = Localizer({"0x00000001": "Buff名称",
            "0x00000002": "{0.SimFirstName}感觉很好，{M0.他}{F0.她}正在享受音乐。",
            "0x98041977": "和{0.String}闲聊"})
        self.owner = SimpleNamespace(sim_id=42, populate_localization_token=lambda token: token.update(
            type=1, first_name="Alice", is_female=True))

    @staticmethod
    def create(key, *owners):
        result = {"hash": key, "tokens": []}
        for owner in owners:
            token = {}
            owner.populate_localization_token(token)
            result["tokens"].append(token)
        return result

    def buff(self, description=None):
        return type("Buff_Music", (), {"guid64": 55, "buff_name": 1,
            "buff_description": description if description is not None else {"hash": 2, "tokens": []}})

    def test_plain_buff_description_binds_observed_owner_and_retains_original(self):
        raw = {"hash": 2, "tokens": []}
        original = copy.deepcopy(raw)
        with patch.dict(sys.modules, {"sims4.localization": SimpleNamespace(_create_localized_string=self.create)}):
            result = self.adapter.resource(self.buff(raw), resource_kind="buff", tokens=(self.owner,))
        detail = result["description"]
        self.assertEqual(detail["text"], "Alice感觉很好，她正在享受音乐。")
        self.assertEqual(detail["status"], "resolved")
        binding = detail["source"]["token_binding"]
        self.assertEqual(binding["owner_id"], "42")
        self.assertEqual(binding["unbound_localization"], {"hash": "0x00000002", "tokens": []})
        self.assertEqual(raw, original)
        self.assertEqual(resource_details(result)["items"][0]["basis"], "observed_buff_owner_context")
        self.assertIn("按持有者解析", detail_text(result))

    def test_missing_buff_owner_keeps_description_unresolved(self):
        result = self.adapter.resource(self.buff(), resource_kind="buff")
        self.assertEqual(result["description"]["status"], "unresolved_tokens")
        self.assertNotIn("token_binding", result["description"]["source"])

    def test_owner_capture_failure_keeps_original_description_failure(self):
        def fail(*args):
            raise ValueError("Owner token unavailable")
        with patch.dict(sys.modules, {"sims4.localization": SimpleNamespace(_create_localized_string=fail)}):
            result = self.adapter.resource(self.buff(), resource_kind="buff", tokens=(self.owner,))
        self.assertEqual(result["description"]["status"], "unresolved_tokens")
        self.assertEqual(result["description"]["localization"]["tokens"], [])
        self.assertIn("Owner token unavailable", result["description"]["source"]["token_binding_error"])
        self.assertEqual(result["name"]["status"], "resolved")

    def test_factory_output_is_authoritative_even_when_it_has_no_tokens(self):
        buff = self.buff(staticmethod(lambda *args: {"hash": 2, "tokens": []}))
        with patch.dict(sys.modules, {"sims4.localization": SimpleNamespace(
                _create_localized_string=lambda *args: self.fail("Factory output must not be rebound"))}):
            result = self.adapter.resource(buff, resource_kind="buff", tokens=(self.owner,))
        self.assertEqual(result["description"]["status"], "unresolved_tokens")
        self.assertNotIn("token_binding", result["description"]["source"])

    def chat(self):
        raw = {"hash": 0x98041977, "tokens": [{"type": 1, "first_name": "Alice", "is_female": True}]}
        return SimpleNamespace(id=77, guid64=27173, sim=SimpleNamespace(id=42), target=None,
            context=SimpleNamespace(source=1), visible=True, get_name=lambda **kwargs: raw), raw

    def test_exact_ui_name_repairs_both_action_and_cause_resource(self):
        action, raw = self.chat()
        ui_text = {"hash": 0x98041977, "tokens": [{"type": 3, "raw_text": "Bob和Carol"}]}
        before = copy.deepcopy((raw, ui_text))
        info = SimpleNamespace(interaction_id=77, display_name=ui_text, interaction_weakref=lambda: action)
        calls = []
        action.sim.ui_manager = SimpleNamespace(_find_interaction=lambda identifier: (calls.append(identifier) or info, 1))
        direct = self.adapter.interaction_name(action)
        cause = self.adapter.resource(action, resource_kind="interaction")["name"]
        for name in (direct, cause):
            self.assertEqual(name["text"], "和Bob和Carol闲聊")
            self.assertEqual(name["source"]["kind"], "runtime_ui_queue")
            self.assertEqual(name["source"]["interaction_id"], "77")
            self.assertEqual(name["source"]["fallback_from"]["status"], "unresolved_tokens")
            self.assertNotIn("Alice", name["text"])
        self.assertEqual(calls, [77, 77])
        self.assertEqual((raw, ui_text), before)
        # Offline refresh uses the captured UI proto and retains its provenance.
        catalog = NameCatalog({"format": "typed_resource_semantics_v2", "entries": {}}, self.adapter.localizer)
        refreshed = catalog.resolve("interaction", "27173", "Chat", direct)
        self.assertEqual(refreshed["text"], direct["text"])
        self.assertEqual(refreshed["source"], direct["source"])

    def test_missing_stale_or_unresolved_ui_record_does_not_guess_chat_target(self):
        action, raw = self.chat()
        for info in (None,
                SimpleNamespace(interaction_id=78, display_name="Wrong interaction"),
                SimpleNamespace(interaction_id=77, display_name="Stale", interaction_weakref=lambda: object()),
                SimpleNamespace(interaction_id=77, display_name={"hash": 0x98041977, "tokens": []})):
            action.sim.ui_manager = SimpleNamespace(_find_interaction=lambda identifier: (info, 1))
            result = self.adapter.interaction_name(action)
            self.assertEqual(result["status"], "unresolved_tokens")
            self.assertEqual(result["localization"]["tokens"][0]["first_name"], "Alice")
            self.assertNotIn("Alice", result["text"])

    def test_ui_lookup_failure_is_isolated_and_resolved_name_skips_lookup(self):
        action, _ = self.chat()
        def failed_lookup(identifier):
            raise ValueError("UI unavailable")
        action.sim.ui_manager = SimpleNamespace(_find_interaction=failed_lookup)
        result = self.adapter.interaction_name(action)
        self.assertEqual(result["status"], "unresolved_tokens")
        self.assertEqual(result["source"]["ui_name_read_error"], "UI unavailable")
        action.get_name = lambda **kwargs: "已知动作"
        action.sim.ui_manager._find_interaction = lambda identifier: self.fail("Unnecessary UI read")
        self.assertEqual(self.adapter.interaction_name(action)["text"], "已知动作")


if __name__ == "__main__":
    unittest.main()
