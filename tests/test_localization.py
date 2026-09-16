"""Verify game-data resolution, evidence preservation and conservative gaps."""

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from context_overlay.ea_adapter import EAAdapter
from context_overlay.localization import Localizer, snapshot_localized, MAX_NODES, MAX_TEXT, MAX_TOKENS
from context_overlay.name_catalog import NameCatalog
from context_overlay.semanticizer import display, translate

spec = importlib.util.spec_from_file_location("build_name_catalog", str(ROOT / "scripts/build_name_catalog.py"))
catalog_builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog_builder)


class LocalizationChecks(unittest.TestCase):
    def setUp(self):
        self.localizer = Localizer({
            "0x482BA41C": "和{1.SimFirstName}聊天",
            "0x4105AA3F": "吃{1.ObjectName}",
            "0x98041977": "和{0.String}闲聊",
            "0x00000001": "汉堡蛋糕", "0x00000002": "{0.String}、{1.String}",
            "0x00000003": "{0.SimFullName}",
            "0x00000004": "{M0.他}{F0.她}收到{1.Number}份礼物",
            "0x00000005": "{0.UnverifiedFormat} / {2.String}",
        })

    def test_dynamic_sim_and_object_use_exact_token_positions(self):
        sim = {"type": 1, "first_name": "Nyssa", "last_name": "Landry", "is_female": True}
        value = {"hash": 0x482BA41C, "tokens": [{"type": 0}, sim]}
        result = self.localizer.name(value, "sim_Chat")
        self.assertEqual(result["text"], "和Nyssa聊天")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["localization"]["tokens"][1]["first_name"], "Nyssa")
        result = self.localizer.name({"hash": 0x4105AA3F, "tokens": [sim, {"type": 5, "catalog_name_key": 1}]})
        self.assertEqual(result["text"], "吃汉堡蛋糕")
        result = self.localizer.name({"hash": 0x4105AA3F, "tokens": [sim, {"type": 5, "catalog_name_key": 1, "custom_name": "我的午餐"}]})
        self.assertEqual(result["text"], "吃我的午餐")
        sim["first_name"] = "小明"
        self.assertEqual(self.localizer.name(value)["text"], "和小明聊天")

    def test_nested_text_uses_recorded_proto_not_game_objects(self):
        names = {"hash": 2, "tokens": [
            {"type": 2, "text_string": {"hash": 3, "tokens": [{"type": 1, "first_name": "A", "last_name": "Smith"}]}},
            {"type": 3, "raw_text": "B"}]}
        localized = {"hash": 0x98041977, "tokens": [{"type": 2, "text_string": names}]}
        before = copy.deepcopy(localized)
        result = self.localizer.name(localized)
        self.assertEqual(result["text"], "和A Smith、B闲聊")
        self.assertEqual(result["status"], "resolved")
        json.dumps(result)
        self.assertEqual(localized, before)
        self.assertEqual(self.localizer.from_evidence(result["localization"])["text"], result["text"])

    def test_gender_number_and_custom_pronouns(self):
        value = {"hash": 4, "tokens": [{"type": 1, "is_female": False}, {"type": 4, "number": 3.0}]}
        self.assertEqual(self.localizer.name(value)["text"], "他收到3份礼物")
        value["tokens"][0]["is_female"] = True
        self.assertEqual(self.localizer.name(value)["text"], "她收到3份礼物")
        value["tokens"][0]["packed_pronouns"] = "custom"
        self.assertEqual(self.localizer.name(value)["status"], "unresolved_tokens")

    def test_missing_token_retains_understandable_template_and_reason(self):
        result = self.localizer.name({"hash": 0x482BA41C, "tokens": []}, "sim_Chat")
        self.assertEqual(result["status"], "unresolved_tokens")
        self.assertIn("聊天", result["text"])
        self.assertIn("1.SimFirstName", result["text"])
        self.assertEqual(result["template"], "和{1.SimFirstName}聊天")
        self.assertTrue(result["unresolved"])
        self.assertNotIn("Nyssa", result["text"])

    def test_unsupported_grammar_does_not_become_success(self):
        result = self.localizer.name({"hash": 5, "tokens": [{"type": 4, "number": 30}]})
        self.assertEqual(result["status"], "unresolved_tokens")
        self.assertEqual(len(result["unresolved"]), 2)
        for value in ({"hash": 0}, None):
            self.assertEqual(self.localizer.name(value, "internal")["status"], "no_display_name")
        self.assertEqual(self.localizer.name(0xFF, "missing")["reason"], "string_key_missing")

    def test_recursion_is_bounded_and_raw_user_text_not_interpreted(self):
        self.assertEqual(self.localizer.name("{1.SimName}")["text"], "{1.SimName}")
        value = {"hash": 0x98041977, "tokens": []}
        value["tokens"].append({"type": 2, "text_string": value})
        result = self.localizer.name(value)
        self.assertNotEqual(result["status"], "resolved")
        json.dumps(result)

    def test_protobuf_uses_present_fields_and_runtime_enum_names(self):
        # Simulates a newer descriptor with a different numeric enum value.
        token = SimpleNamespace(type=42, first_name="Lina", last_name="", is_female=False)
        token.DESCRIPTOR = SimpleNamespace(fields_by_name={"type": SimpleNamespace(enum_type=SimpleNamespace(
            values_by_number={42: SimpleNamespace(name="SIM")}))})
        token.ListFields = lambda: [(SimpleNamespace(name=key), value) for key, value in (
            ("type", 42), ("first_name", "Lina"), ("is_female", False))]
        result = snapshot_localized(SimpleNamespace(hash=0x482BA41C, tokens=[SimpleNamespace(type=0), token]))
        self.assertEqual(result["tokens"][1]["type"], "SIM")
        self.assertFalse(result["tokens"][1]["is_female"])
        self.assertNotIn("last_name", result["tokens"][1])
        self.assertEqual(self.localizer.from_evidence(result)["text"], "和Lina聊天")

    def test_truncated_evidence_never_claims_resolution(self):
        value = {"hash": 0x98041977, "tokens": [{"type": 3, "raw_text": "A" * (MAX_TEXT + 1)}]}
        result = self.localizer.name(value)
        self.assertEqual(result["status"], "unresolved_tokens")
        self.assertEqual(result["localization"]["tokens"][0]["error"], "localization_text_limit")
        self.assertNotEqual(self.localizer.name({"hash": 1, "tokens": [{"type": 0}] * (MAX_TOKENS + 1)})["status"], "resolved")
        # The final literal suffix also counts toward the rendered output cap.
        localizer = Localizer({"0x00000001": "{0.String}" + "B" * 8000})
        result = localizer.name({"hash": 1, "tokens": [{"type": 3, "raw_text": "A" * 9000}]})
        self.assertEqual(result["status"], "unresolved_tokens")
        self.assertLess(len(result["text"]), MAX_TEXT)

    def test_repeated_nested_expansion_shares_a_work_budget(self):
        class CountingStrings(dict):
            calls = 0
            def get(self, key):
                self.calls += 1
                return super().get(key)
        strings = CountingStrings({"0x00000001": "{0.String}" * 80})
        evidence = {"raw_text": "x"}
        for _ in range(4):
            evidence = {"hash": 1, "tokens": [{"type": "STRING", "text_string": evidence}]}
        result = Localizer(strings).from_evidence(evidence)
        self.assertEqual(result["status"], "unresolved_tokens")
        self.assertLessEqual(strings.calls, MAX_NODES)
        self.assertLessEqual(len(result["text"]), MAX_TEXT)

    def test_runtime_resources_use_native_name_fields_and_isolate_failures(self):
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.localizer = self.localizer
        seen = []
        relationship = type("relationship_new", (), {"guid64": 999, "visible": False,
            "display_name": staticmethod(lambda *tokens: (seen.append(tokens) or 1))})
        named = adapter.resource(relationship, resource_kind="relbit", tokens=("actor", "target"))
        self.assertEqual(named["name"]["text"], "汉堡蛋糕")
        self.assertEqual(seen, [("actor", "target")])
        statistic = type("stat_new", (), {"guid64": 1000, "stat_name": 1})
        self.assertEqual(adapter.resource(statistic, resource_kind="statistic")["name"]["text"], "汉堡蛋糕")
        hidden = type("hidden_buff", (), {"guid64": 1001, "buff_name": None, "visible": False})
        self.assertIn("隐藏资源", display(adapter.resource(hidden, resource_kind="buff")))
        def failed(*args):
            raise ValueError("unavailable name")
        broken = type("broken_label", (), {"guid64": 1002, "display_name": staticmethod(failed)})
        self.assertEqual(adapter.resource(broken, resource_kind="relbit")["name"]["reason"], "label_read_failed")

    def test_interaction_reads_live_name_and_fallback_uses_tuned_tokens(self):
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.localizer = self.localizer
        adapter.reference = lambda obj: {"kind": "sim", "id": str(obj.id), "key": "sim:" + str(obj.id), "name": "A"}
        adapter.event_reference = lambda obj: adapter.reference(obj) if obj is not None else None
        adapter.config = {"max_entities": 4096}
        adapter.in_scope = lambda obj: True
        actor = SimpleNamespace(id=20, sim_info=SimpleNamespace(sim_id=20))
        target = SimpleNamespace(id=21)
        context = SimpleNamespace(source=1)
        calls = []
        def get_name(**kwargs):
            calls.append(kwargs)
            return {"hash": 0x98041977, "tokens": [{"type": 3, "raw_text": "小明和小红"}]}
        interaction = SimpleNamespace(sim=actor, target=target, context=context, get_name=get_name,
            id=30, guid64=40, is_super=True, immediate=False, finishing_type=None, pipeline_progress=None,
            get_participants=lambda role: (actor, target))
        modules = {"interactions": SimpleNamespace(ParticipantType=SimpleNamespace(AllSims=1)),
                   "animation": SimpleNamespace(), "animation.animation_interaction": SimpleNamespace(AnimationInteraction=type("AnimationInteraction", (), {}))}
        with patch.dict(sys.modules, modules):
            result = adapter.interaction(interaction)
            self.assertEqual(result["name"]["text"], "和小明和小红闲聊")
            self.assertEqual(calls, [{"target": target, "context": context}])
            def fail(**kwargs):
                raise ValueError("name override failed")
            interaction.get_name = fail
            interaction.get_localization_tokens = lambda **kwargs: ("调好的名称参数",)
            interaction.display_name_in_queue = lambda *tokens: {"hash": 0x98041977, "tokens": [{"type": 3, "raw_text": tokens[0]}]}
            result = adapter.interaction(interaction)
        self.assertEqual(result["name"]["text"], "和调好的名称参数闲聊")
        self.assertEqual(result["name"]["source"]["kind"], "runtime_tuning_fallback")
        self.assertIn("name override failed", result["name"]["get_name_error"])

    def test_object_reference_uses_name_component_and_isolates_label_error(self):
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.localizer = Localizer({"0x00000001": "{0.ObjectName}", "0x00000002": "蛋糕"})
        obj = SimpleNamespace(id=20, definition=SimpleNamespace(id=30, name="food_object"))
        def live_name(item):
            self.assertIs(item, obj)
            return {"hash": 1, "tokens": [{"type": 5, "custom_name": "我的蛋糕", "catalog_name_key": 2}]}
        helper = SimpleNamespace(get_object_name=live_name)
        modules = {"sims4": SimpleNamespace(), "sims4.localization": SimpleNamespace(LocalizationHelperTuning=helper)}
        with patch.dict(sys.modules, modules):
            result = adapter.reference(obj)
            self.assertEqual(result["name"]["text"], "我的蛋糕")
            def failed(*args):
                raise ValueError("catalog unavailable")
            helper.get_object_name = failed
            with patch.dict(sys.modules, {"build_buy": SimpleNamespace(get_object_catalog_name=failed)}):
                result = adapter.reference(obj)
        self.assertEqual(result["id"], "20")
        self.assertEqual(result["definition_id"], "30")
        self.assertEqual(result["name"]["reason"], "label_read_failed")


class CatalogChecks(unittest.TestCase):
    def test_merged_refs_display_mixin_types_and_old_snapshot_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / "combined_tuning_BASE.xml"
            path.write_text('''<combined><g s="merged"><T x="1">0x00000010</T><U x="2"><V n="instance_display_name" t="enabled"><r n="enabled" x="1"/></V></U></g>
                <R n="relbit"><I s="10" n="rel_one" c="RelationshipBit"><r n="display_name" x="1"/></I></R>
                <R n="buff"><I s="10" n="buff_other" c="Buff"><T n="visible">False</T></I></R>
                <R n="object_state"><I s="20" n="state_one" c="ObjectStateValue"><V n="_display_data" t="enabled"><r n="enabled" x="2"/></V></I></R></combined>''', encoding="utf-8")
            old = base / "combined_tuning_BASEFull.xml"
            old.write_text("invalid-2014-not-read", encoding="utf-8")
            result = catalog_builder.extract([path, old])
        self.assertEqual(result["entries"]["object_state:20"]["names"][0]["hash"], "0x00000010")
        self.assertFalse(result["entries"]["buff:10"]["visible"])
        self.assertEqual(result["entries"]["buff:10"]["names"], [])
        self.assertNotIn(old.name, result["inputs"])

    def test_offline_enrichment_preserves_facts_and_never_guesses_tokens(self):
        data = {"format": "typed_tuning_names_v1", "inputs": {"BASE.xml": "sha"}, "entries": {
            "relbit:42": {"tuning_name": "rel_one", "names": [{"hash": "0x00000010", "attribute": "display_name"}], "source_file": "BASE.xml", "visible": True}}}
        catalog = NameCatalog(data, Localizer({"0x00000010": "相识", "0x482BA41C": "和{1.SimFirstName}聊天"}))
        raw = {"text": "rel_one", "status": "unmapped", "hash": None}
        self.assertEqual(catalog.resolve("relbit", "42", "rel_one", raw)["text"], "相识")
        self.assertIs(catalog.resolve("buff", "42", "rel_one", raw), raw)
        self.assertIs(catalog.resolve("relbit", "42", "renamed", raw), raw)
        packet = {"snapshot": {"relationships": {"status": "available", "value": [{"bits": [
            {"id": "42", "tuning_name": "rel_one", "name": raw}]}]}}, "history": {"events": []}}
        original = copy.deepcopy(packet)
        translated = translate(packet, catalog)
        self.assertEqual(packet, original)
        self.assertEqual(translated["snapshot"], original["snapshot"])
        self.assertIn("相识", translated["rendered"]["current"][0]["text"])
        self.assertIn("semantic_view", translated)
        self.assertEqual(len(translated["rendered"]["name_resolution"]["strings_content_sha256"]), 64)
        old_name = {"text": "sim_Chat", "status": "unresolved_tokens", "hash": "0x482BA41C"}
        resolved = catalog.resolve("interaction", "13998", "sim_Chat", old_name)
        self.assertEqual(resolved["status"], "unresolved_tokens")
        self.assertIn("聊天", resolved["text"])
        self.assertEqual(resolved["source"]["tokens"], "not_captured_in_old_record")
        absent = {"text": "rel_one", "status": "no_display_name", "localization": {"hash": None, "tokens": []},
                  "source": {"kind": "runtime_tuning"}, "visible": False}
        resolved = catalog.resolve("relbit", "42", "rel_one", absent)
        self.assertEqual(resolved["status"], "no_display_name")
        self.assertEqual(resolved["source"]["kind"], "runtime_tuning")
        self.assertFalse(resolved["visible"])
        failed = dict(absent, status="unmapped", reason="label_read_failed")
        self.assertIs(catalog.resolve("relbit", "42", "rel_one", failed), failed)
