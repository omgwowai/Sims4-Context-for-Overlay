"""Descriptions must help readers without fabricating names or old parameters."""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_overlay.ea_adapter import EAAdapter
from context_overlay.localization import Localizer, FORMAT_PROFILE
from context_overlay.name_catalog import NameCatalog
from context_overlay.semanticizer import detail_text, render, resource_details, translate
import test_inspector


def label(text, status="resolved", **extra):
    return dict(text=text, status=status, **extra)


class FormatChecks(unittest.TestCase):
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

    def test_invalid_dates_custom_formats_and_fractional_money_stay_unresolved(self):
        for template, token in (
            ("{0.Money}", {"type": "NUMBER", "number": 1.25}),
            ("{0.Money}", {"type": "NUMBER", "number": True}),
            ("{0.TimeShort}", {"type": "DATE_AND_TIME", "date_and_time": {"hours": 24, "minutes": 0}}),
            ("{0.TimeShort}", {"type": "DATE_AND_TIME", "date_and_time": {"hours": 9}}),
            ("{0.TimeShort}", {"type": "DATE_AND_TIME", "date_and_time": {
                "hours": 9, "minutes": 30, "date_and_time_format_hash": 77}}),
            ("{0.DayOfWeekLong}", {"type": "DATE_AND_TIME", "date_and_time": {
                "date": 2, "month": 9, "full_year": 2026}})):
            with self.subTest(template=template, token=token):
                result = Localizer({"0x00000001": template}).from_evidence({"hash": 1, "tokens": [token]})
                self.assertEqual(result["status"], "unresolved_tokens")

    def test_old_chat_mismatch_is_not_filled_with_the_actor_name(self):
        result = Localizer({"0x00000001": "和{0.String}闲聊"}).from_evidence({"hash": 1,
            "tokens": [{"type": "SIM", "first_name": "Lila"}]})
        self.assertNotIn("Lila", result["text"])
        gap = result["unresolved"][0]
        self.assertEqual(gap["reason"], "token_type_mismatch")
        self.assertEqual(gap["category"], "parameter_evidence")
        self.assertEqual(gap["actual_type"], "SIM")
        self.assertEqual(gap["expected_types"], ["RAW_TEXT", "STRING"])

    def test_missing_tokens_and_unverified_age_selectors_are_distinct(self):
        localizer = Localizer({"0x00000001": "{1.SimFirstName}{T0.胡闹}{DAE0.嘿咻}"})
        result = localizer.from_evidence({"hash": 1, "tokens": [{"type": "SIM", "age_flags": 16}]})
        self.assertEqual([gap["category"] for gap in result["unresolved"]],
                         ["parameter_evidence", "grammar_support", "grammar_support"])
        self.assertEqual(result["unresolved"][0]["reason"], "missing_token")


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
        del data["entries"]["buff:10"]
        data["conflicts"] = {"buff:10": ["a", "b"]}
        self.assertIsNone(NameCatalog(data, localizer).reference_semantics(None, "10", "mood"))

    def test_description_limits_report_truncation_and_keep_partial_templates(self):
        value = [dict(self.resource, id=str(i)) for i in range(8)]
        self.assertTrue(resource_details(value, limit=3)["truncated"])
        self.assertEqual(len(resource_details(value, limit=3)["items"]), 3)
        self.assertTrue(resource_details(value, node_limit=1)["truncated"])
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


class DescriptionInspectorChecks(unittest.TestCase):
    def setUp(self):
        self.fixture = test_inspector.InspectorChecks()
        self.fixture.setUp()

    def test_existing_field_navigation_opens_full_description(self):
        fixture = self.fixture
        fixture.values["buffs"] = [{"id": "42", "name": label("隐藏效果"), "description": label("已有官方说明") }]
        fixture.session.refresh()
        fixture.session.field_page("buffs")
        self.assertNotIn("已有官方说明", fixture.view.windows[-1][2][-1]["label"])
        fixture.view.choose("1 · 隐藏效果")
        self.assertIn("已有官方说明", fixture.view.windows[-1][1])
        self.assertEqual(fixture.view.layouts[-1], "text")


if __name__ == "__main__":
    unittest.main()
