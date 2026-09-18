"""Localization failures must remain visible after compaction, without guessing."""

import unittest

from support import ROOT
from experience_labels import resolve_label
from experience_policy import compact
from experience_recap import value_text


class LabelTests(unittest.TestCase):
    def test_no_display_name_retains_identity_and_status(self):
        raw = {"tuning_id": "190163", "tuning_name": "Sim_RainStart_Reaction", "name": {
            "text": "Sim_RainStart_Reaction", "status": "no_display_name", "hash": "0x00000000"}}
        small = compact(raw)
        result = resolve_label(small, "interaction", game_version="1.126.73.1030")
        self.assertEqual(small["name_status"], "no_display_name")
        self.assertEqual(result["text"], "对开始下雨作出反应")
        self.assertEqual(result["observed"], raw["name"]["text"])
        self.assertEqual(result["basis"], "reviewed_exact_identity")
        self.assertEqual(len(result["source"]["source_sha256"]), 64)

    def test_mapping_requires_kind_id_name_and_compatible_version(self):
        value = {"id": "190163", "tuning_name": "Sim_RainStart_Reaction", "name": "Sim_RainStart_Reaction"}
        for change, kind, version in (({"id": "new"}, "interaction", None),
                                      ({"tuning_name": "Sim_RainStart_Reaction_Changed"}, "interaction", None),
                                      ({}, "buff", None), ({}, "interaction", "future-version")):
            with self.subTest(change=change, kind=kind, version=version):
                self.assertEqual(resolve_label(dict(value, **change), kind, game_version=version)["basis"], "unresolved")

    def test_parameter_gap_does_not_fill_from_other_roles(self):
        small = compact({"tuning_id": "27173", "tuning_name": "Idle_Chatting_STC", "name": {
            "status": "unresolved_tokens", "text": "和〈未解析：0.String〉闲聊", "unresolved": [{"reason": "token_type_mismatch"}]}})
        result = resolve_label(small, "interaction")
        self.assertEqual(result["text"], "闲聊（名称参数缺失）")
        self.assertEqual(result["basis"], "reviewed_partial")
        # If the actual runtime template resolves, its observed target is retained.
        small.update(name="和甲闲聊", name_status="resolved", name_unresolved=False)
        self.assertEqual(resolve_label(small, "interaction")["text"], "和甲闲聊")

    def test_future_failures_are_marked_without_a_per_resource_patch(self):
        for value in ({"id": "new", "name": "New_Internal_Name"},
                      {"id": "new", "name": "中文回退", "name_status": "unmapped"},
                      {"id": "new", "name": "和{0.String}说话"},
                      {"id": "new", "name": "", "name_status": "empty_display_name"},
                      {"id": "new", "name": "English fallback", "name_status": "future_failure_status"}):
            result = resolve_label(value, "interaction")
            self.assertEqual(result["basis"], "unresolved")
            self.assertEqual(result["text"], "活动名称未解析（new）")
            self.assertEqual(result["observed"], value["name"] or None)

    def test_english_and_custom_sim_names_are_not_forced_to_chinese(self):
        self.assertEqual(resolve_label({"name": "Play chess", "status": "resolved"})["text"], "Play chess")
        self.assertEqual(resolve_label({"key": "sim:1", "name": "Alex_One"})["text"], "Alex_One")

    def test_resource_failures_inside_nested_results_are_detected(self):
        payload = compact({"_known_traits": [{"id": "new", "resource_kind": "trait", "tuning_name": "new_trait",
                                               "name": {"text": "new_trait", "status": "no_display_name"}}]})
        self.assertIn("资源名称未解析", value_text(payload))
        self.assertNotIn("new_trait", value_text(payload))


if __name__ == "__main__":
    unittest.main()
