"""Verify game-data resolution, evidence preservation and conservative gaps."""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from context_overlay.ea_adapter import EAAdapter
from context_overlay.localization import FORMAT_PROFILE, Localizer, snapshot_localized, MAX_TEXT
from context_overlay.semanticizer import display



class LocalizationChecks(unittest.TestCase):
    def setUp(self):
        self.localizer = Localizer({
            "0x482BA41C": "和{1.SimFirstName}聊天",
            "0x4105AA3F": "吃{1.ObjectName}",
            "0x98041977": "和{0.String}闲聊",
            "0x00000001": "汉堡蛋糕", "0x00000002": "{0.String}、{1.String}",
            "0x00000003": "{0.SimFullName}",
            "0x00000004": "{M0.他}{F0.她}收到{1.Number}份礼物",
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

    def test_absent_name_and_missing_dictionary_entry_are_distinct(self):
        self.assertEqual(self.localizer.name(None, "internal")["status"], "no_display_name")
        self.assertEqual(self.localizer.name(0xFF, "missing")["reason"], "string_key_missing")

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

    def test_currency_and_clock_use_explicit_chinese_profile(self):
        localizer = Localizer({"0x00000001": "报酬：{0.Money}；{1.DayOfWeekLong} {1.TimeShort}"})
        evidence = {"hash": 1, "tokens": [{"type": "NUMBER", "number": -1234},
            {"type": "DATE_AND_TIME", "date_and_time": {
                "hours": 0, "minutes": 5, "date": 7, "month": 0, "full_year": 0}}]}
        before = copy.deepcopy(evidence)
        result = localizer.from_evidence(evidence)
        self.assertEqual(result["text"], "报酬：-1234 模拟币；星期日 00:05")
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["format_profile"], FORMAT_PROFILE)
        self.assertEqual(evidence, before)

    def test_unsupported_date_formats_and_fractional_money_stay_unresolved(self):
        for template, token in (
            ("{0.Money}", {"type": "NUMBER", "number": 1.25}),
            ("{0.TimeShort}", {"type": "DATE_AND_TIME", "date_and_time": {
                "hours": 9, "minutes": 30, "date_and_time_format_hash": 77}}),
            ("{0.DayOfWeekLong}", {"type": "DATE_AND_TIME", "date_and_time": {
                "date": 2, "month": 9, "full_year": 2026}})):
            with self.subTest(template=template, token=token):
                result = Localizer({"0x00000001": template}).from_evidence({"hash": 1, "tokens": [token]})
                self.assertEqual(result["status"], "unresolved_tokens")

    def test_missing_tokens_and_unverified_age_selectors_are_distinct(self):
        localizer = Localizer({"0x00000001": "{1.SimFirstName}{T0.胡闹}{DAE0.嘿咻}"})
        result = localizer.from_evidence({"hash": 1, "tokens": [{"type": "SIM", "age_flags": 16}]})
        self.assertEqual(result["status"], "unresolved_tokens")
        self.assertEqual([gap["category"] for gap in result["unresolved"]],
                         ["parameter_evidence", "grammar_support", "grammar_support"])
        self.assertEqual(result["unresolved"][0]["reason"], "missing_token")
