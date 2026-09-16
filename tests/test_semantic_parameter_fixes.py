"""Live parameter repairs must retain raw evidence and respect identity."""

import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from context_overlay.ea_adapter import EAAdapter
from context_overlay.localization import Localizer
from context_overlay.name_catalog import NameCatalog
from context_overlay.semanticizer import detail_text, resource_details


class ParameterFixChecks(unittest.TestCase):
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
        detail["localization"]["tokens"][0]["first_name"] = "Changed"
        self.assertEqual(raw, original)

    def test_existing_or_unverified_parameters_are_never_replaced(self):
        inputs = [
            ({"hash": 2, "tokens": [{"type": 1, "first_name": "Bob", "is_female": False}]}, (self.owner,)),
            ({"hash": 2, "tokens": [], "error": "localization_token_limit"}, (self.owner,)),
            ({"hash": 2, "tokens": []}, ()),
            ({"hash": 2, "tokens": []}, (self.owner, self.owner)),
            ({"hash": 2, "tokens": []}, (SimpleNamespace(id=42),))]
        with patch.dict(sys.modules, {"sims4.localization": SimpleNamespace(
                _create_localized_string=lambda *args: self.fail("Unexpected binding"))}):
            for evidence, owners in inputs:
                result = self.adapter.localizer.from_evidence(evidence)
                self.assertIs(self.adapter._bind_buff_owner(result, owners), result)
            for template in ("{0.String}", "{1.SimFirstName}", "{T0.胡闹}{DAE0.嘿咻}", "{0.ObjectName}"):
                self.adapter.localizer.strings["0x00000003"] = template
                result = self.adapter.localizer.name(3)
                self.assertIs(self.adapter._bind_buff_owner(result, (self.owner,)), result)

    def test_unicode_owner_evidence_is_preserved(self):
        self.owner.populate_localization_token = lambda token: token.update(type=1, first_name="阿岚", is_female=False)
        with patch.dict(sys.modules, {"sims4.localization": SimpleNamespace(_create_localized_string=self.create)}):
            result = self.adapter.resource(self.buff(), resource_kind="buff", tokens=(self.owner,))
        self.assertEqual(result["description"]["text"], "阿岚感觉很好，他正在享受音乐。")
        self.assertEqual(result["description"]["localization"]["tokens"][0]["first_name"], "阿岚")

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

    def test_invalid_owner_token_never_becomes_observed_sim_context(self):
        with patch.dict(sys.modules, {"sims4.localization": SimpleNamespace(_create_localized_string=lambda *args:
                {"hash": 2, "tokens": [{"type": 5, "custom_name": "Object"}]})}):
            result = self.adapter.resource(self.buff(), resource_kind="buff", tokens=(self.owner,))
        self.assertEqual(result["description"]["localization"]["tokens"], [])
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

    def test_unavailable_name_accessor_is_not_reclassified_as_absent_text(self):
        recorded = {"text": "Broadcaster", "status": "unmapped", "reason": "no_verified_name_accessor",
                    "localization": {"hash": None, "tokens": []}}
        catalog = NameCatalog({"format": "typed_resource_semantics_v2", "entries": {}}, self.adapter.localizer)
        self.assertIs(catalog.resolve("broadcaster", "123", "Broadcaster", recorded), recorded)


if __name__ == "__main__":
    unittest.main()
