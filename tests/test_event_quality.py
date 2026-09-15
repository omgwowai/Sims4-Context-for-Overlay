"""Regressions exposed by the 0.6.0 run; fixtures retain no player data."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_event_expansion as source_fixtures
from context_overlay.ea_adapter import EAAdapter
from context_overlay.event_policy import suppressed_statistic, internal_interaction
from context_overlay.localization import Localizer
from context_overlay.semanticizer import display, explain_event


class EventQualityChecks(unittest.TestCase):
    setUp = source_fixtures.SourceChecks.setUp
    numeric_fixture = source_fixtures.SourceChecks.numeric_fixture

    def test_elapsed_time_counters_are_not_published_or_written(self):
        stat, operation = self.numeric_fixture()
        for name in ("commodity_SimInfo_TimeSinceLastSlept", "commodity_SimInfo_TimeSinceLastSocial",
                     "commodity_IslanderCulture_TimeSinceCooked"):
            type(stat).__name__ = name
            stat._value = 90
            operation._apply_to_subject_and_target(self.sim, None, SimpleNamespace())
        self.assertFalse(self.recorder.events)
        self.assertFalse(self.journal.records)
        self.assertEqual(sum(self.sources.diagnostics()["suppressed_statistics"].values()), 3)
        type(stat).__name__ = "motive_Hunger"
        stat._value = 90
        operation._apply_to_subject_and_target(self.sim, None, SimpleNamespace())
        self.assertEqual(len(self.recorder.events), 1)
        self.assertFalse(suppressed_statistic("commodity_UnknownGameplayCounter"))
        self.assertFalse(suppressed_statistic("TimeSince"))
        self.assertTrue(internal_interaction(13377, "Food_Eat_Active"))
        self.assertFalse(internal_interaction(13377, "mod_custom_social"))
        stat.get_value.assert_not_called()

    def test_effect_owner_is_not_confused_with_the_indexed_initiator(self):
        subject = self.adapter.event_reference(self.other)
        actor = self.adapter.event_reference(self.sim)
        event = self.recorder.change([subject], "buffs", None, {"name": "effect"}, 1, "observed",
            cause={"actor": actor, "event_id": "source:interaction:1:9"})
        self.assertEqual(set(event["entities"]), {"sim:1", "sim:2"})
        text = explain_event(event)["text"]
        self.assertTrue(text.startswith("Sim2新增Buff"))
        self.assertIn("发起者：Sim1", text)
        self.assertNotIn("Sim2、Sim1新增", text)
        # Existing journals with only an appended initiator remain readable.
        event["roles"] = [r for r in event["roles"] if r["role"] == "initiator"]
        self.assertTrue(explain_event(event)["text"].startswith("Sim2新增Buff"))

    def test_mood_intensity_change_and_missing_old_intensity_are_distinct(self):
        sources, sim = self.sources, self.sim
        mood = type("Mood_Tense", (), {"guid64": 21})
        class BuffComponent:
            owner = sim
            _active_mood = mood
            _active_mood_intensity = 0
            def _update_current_mood(self):
                self._active_mood_intensity = 1
                values = {"old_mood": mood, "new_mood": mood}
                sources.native(sim, "MoodChange", SimpleNamespace(get_resolved_arg=values.get))
        self.sources.wrap(BuffComponent, "_update_current_mood", "mood_context", "BuffComponent._update_current_mood")
        BuffComponent()._update_current_mood()
        event = next(iter(self.recorder.events.values()))
        self.assertEqual((event["payload"]["old_intensity"], event["payload"]["new_intensity"]), (0, 1))
        self.assertEqual(event["payload"]["change_kind"], "intensity")
        values = {"old_mood": mood, "new_mood": mood}
        self.sources.native(sim, "MoodChange", SimpleNamespace(get_resolved_arg=values.get))
        latest = list(self.recorder.events.values())[-1]
        self.assertIsNone(latest["payload"]["old_intensity"])
        self.assertEqual(latest["payload"]["change_kind"], "notification")
        self.assertEqual(self.sources.frames, [])

    def test_craft_payment_captures_payer_and_recipe_before_ea_clears_them(self):
        sim, sources = self.sim, self.sources
        class Funds:
            _funds = 100
            def try_remove_amount(self, amount, reason, sim=None):
                if amount > self._funds:
                    return False
                self._funds -= amount
                return True
        funds = Funds()
        class Process:
            recipe = type("recipe_food", (), {"guid64": 41})
            _paying_sim = sim
            _current_crafting_interaction = SimpleNamespace(id=3, sim=sim)
            def pay_for_item(self):
                result = funds.try_remove_amount(4, 1)  # EA omits sim here.
                self._paying_sim = None
                return result
        sources.wrap(Funds, "try_remove_amount", "money", "Funds.try_remove_amount")
        sources.wrap(Process, "pay_for_item", "craft_payment_context", "CraftingProcess.pay_for_item")
        self.assertTrue(Process().pay_for_item())
        event = next(iter(self.recorder.events.values()))
        self.assertEqual(event["payload"]["actual_amount"], -4)
        self.assertEqual(event["payload"]["recipe"]["id"], "41")
        self.assertEqual(event["cause"]["event_id"], "source:interaction:1:3")
        self.assertEqual(event["roles"][0]["entity_key"], "sim:1")
        funds.try_remove_amount(4, 1)
        self.assertEqual(len(self.recorder.events), 1)  # No leaked context.
        self.assertEqual(sources.frames, [])

    def test_native_product_uses_process_interaction_not_nearby_action(self):
        process = SimpleNamespace(recipe=None, _current_crafting_interaction=SimpleNamespace(id=9, sim=self.sim))
        product = SimpleNamespace(object_id=10, local=True, crafting_component=SimpleNamespace(_crafting_process=process))
        values = {"crafted_object": product}
        self.sources.native(self.sim, "ItemCrafted", SimpleNamespace(get_resolved_arg=values.get))
        event = next(iter(self.recorder.events.values()))
        self.assertEqual(event["cause"]["event_id"], "source:interaction:1:9")
        self.assertEqual(event["cause"]["basis"], "crafting_process.current_interaction")
        self.assertEqual(event["payload"]["record_origin"], "item_crafted_notification")
        process._current_crafting_interaction = None
        self.sources.native(self.sim, "ItemCrafted", SimpleNamespace(get_resolved_arg=values.get))
        self.assertIsNone(list(self.recorder.events.values())[-1]["cause"])

    def test_inventory_element_context_and_actual_carrier_keep_sim_queries(self):
        sim, sources = self.sim, self.sources
        container = SimpleNamespace(object_id=20, local=True)
        item = SimpleNamespace(object_id=10, local=False, container=None)
        item.inventoryitem_component = SimpleNamespace(get_inventory=lambda: item.container, stack_count=lambda: 1, is_hidden=False)
        class Inventory:
            owner = container
            def _insert_item(self, obj):
                obj.container = self
                return True
        inventory = Inventory()
        class Transfer:
            interaction = SimpleNamespace(id=7, sim=sim)
            def _do_behavior(self):
                return inventory._insert_item(item)
        sources.wrap(Inventory, "_insert_item", "inventory_insert", "Inventory._insert_item")
        sources.wrap(Transfer, "_do_behavior", "element_context", "InventoryTransfer._do_behavior")
        self.assertTrue(Transfer()._do_behavior())
        event = self.recorder.query_history("sim:1")["events"][0]
        self.assertEqual(event["cause"]["event_id"], "source:interaction:1:7")
        self.assertIn("object:10", event["entities"])
        self.assertIn("object:20", event["entities"])
        self.assertEqual({r["role"] for r in event["roles"]}, {"item", "destination_container", "initiator"})
        class CarryTarget:
            _sim, _obj, _inventory_owner = sim, item, container
            def carry_event_callback(self):
                return inventory._insert_item(item)
        sources.wrap(CarryTarget, "carry_event_callback", "inventory_carry_context", "CarrySystemInventoryTarget.carry_event_callback")
        item.container = None
        CarryTarget().carry_event_callback()
        event = list(self.recorder.events.values())[-1]
        self.assertEqual(event["cause"]["basis"], "carry_system_target._sim")
        self.assertNotIn("event_id", event["cause"])
        self.assertIn("sim:1", event["entities"])
        self.assertEqual(next(r for r in event["roles"] if r["role"] == "initiator")["basis"], "carry_system_target._sim")
        self.assertEqual(sources.frames, [])

    def test_payment_context_does_not_survive_exception_or_deferred_callback(self):
        sources, sim = self.sources, self.sim
        class Payment:
            def make_payment(self, resolver, sim, fail=False):
                if fail:
                    raise ValueError("payment failure")
                return lambda: sources.cause()
        sources.wrap(Payment, "make_payment", "payment_context", "Payment.make_payment")
        resolver = SimpleNamespace(interaction=SimpleNamespace(id=55, sim=sim))
        with self.assertRaisesRegex(ValueError, "payment failure"):
            Payment().make_payment(resolver, sim, True)
        callback = Payment().make_payment(resolver, sim)
        self.assertIsNone(callback())
        self.assertEqual(sources.frames, [])

    def test_whim_completion_has_subtype_and_is_internal(self):
        sources, sim = self.sources, self.sim
        aspiration = type("whimSet_observed", (), {"guid64": 10, "aspiration_type": "WHIM_SET"})
        class Tracker:
            _sim_info = sim
            def complete_milestone(self, aspiration):
                sources.native(sim, "MilestoneCompleted", SimpleNamespace(get_resolved_arg=lambda k: None))
        sources.wrap(Tracker, "complete_milestone", "objective_context", "AspirationTracker.complete_milestone")
        Tracker().complete_milestone(aspiration)
        event = next(iter(self.recorder.events.values()))
        self.assertEqual(event["payload"]["aspiration_type"], "WHIM_SET")
        self.assertEqual(event["tier"], "internal")
        self.assertNotIn("抱负阶段", explain_event(event)["text"])


class ResourceQualityChecks(unittest.TestCase):
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
        self.assertNotIn("hash", display({"text": "", "status": "resolved", "hash": "0x123", "fallback": "MoodBuff_Internal"}))


if __name__ == "__main__":
    unittest.main()
