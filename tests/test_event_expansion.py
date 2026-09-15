"""Event evidence, FIFO retention and grouped history contracts without EA."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_core import MemoryJournal, facts
from context_overlay.event_sources import EventSources
from context_overlay.game_runtime import DEFAULTS
from context_overlay.history import HistoryError
from context_overlay.hooks import Hooks
from context_overlay.model import entity
from context_overlay.recorder import Recorder
from context_overlay.storage import replay, StorageError
from context_overlay.semanticizer import explain_event


class RetentionChecks(unittest.TestCase):
    def test_fifo_is_first_acceptance_not_revision_or_game_timestamp(self):
        recorder = Recorder(MemoryJournal(), "fifo", capacity=2)
        oldest = recorder.interaction("started", facts(1, 111), 100, "native")
        second = recorder.interaction("started", facts(2, 222), 10, "native")
        recorder.interaction("exited", facts(1, 111), 200, "native")
        snapshot = recorder.query_history("object:111")
        recorder.interaction("started", facts(3, 333), 30, "native")
        self.assertNotIn(oldest["event_id"], recorder.events)
        self.assertIn(second["event_id"], recorder.events)
        self.assertNotIn("object:111", recorder.index.entities)
        self.assertNotIn("object:111", recorder.references)
        self.assertEqual(recorder.index.reference_count, 4)
        self.assertEqual(recorder.history_page(snapshot["cursor"])["events"][0]["revision"], 2)
        recorder.close_query(snapshot["cursor"])
        self.assertEqual(recorder.index.status()["snapshot_references"], 0)

    def test_failed_journal_acceptance_does_not_evict_and_replay_applies_fifo(self):
        journal = MemoryJournal()
        recorder = Recorder(journal, "fifo", capacity=1)
        old = recorder.interaction("started", facts(1), 1, "native")
        with patch.object(journal, "append", side_effect=StorageError("disk failed")):
            self.assertIsNone(recorder.interaction("started", facts(2), 2, "native"))
        self.assertIn(old["event_id"], recorder.events)
        self.assertEqual(recorder.evicted, 0)
        recorder.paused = False
        newest = recorder.interaction("started", facts(3), 3, "native")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.jsonl"
            path.write_text("\n".join(json.dumps(dict(record, sequence=i + 1)) for i, record in enumerate(journal.records)), encoding="utf-8")
            result = replay(path)
        self.assertTrue(result["complete"])
        self.assertEqual([e["event_id"] for e in result["events"]], [newest["event_id"]])

    def test_grouped_queries_preserve_effects_filters_snapshots_and_orphans(self):
        recorder = Recorder(MemoryJournal(), "group", capacity=3)
        action = recorder.interaction("started", facts(1), 1, "native")
        effect = recorder.fact("mood.changed", [facts()["actor"]], {"new_mood": "happy"}, 2, "native",
                               cause={"event_id": action["event_id"], "basis": "resolver.interaction"})
        grouped = recorder.query_history(facts()["actor"]["key"], group_effects=True)
        self.assertEqual(grouped["total_matches"], 1)
        self.assertEqual(grouped["events"][0]["effects"][0]["event_id"], effect["event_id"])
        flat = recorder.query_history(facts()["actor"]["key"], event_types=["game_event"], group_effects=True)
        self.assertEqual(flat["events"][0]["event_id"], effect["event_id"])
        recorder.interaction("started", facts(2), 3, "native")
        recorder.interaction("started", facts(3), 4, "native")
        current = recorder.query_history(facts()["actor"]["key"], group_effects=True)
        self.assertIn(effect["event_id"], [event["event_id"] for event in current["events"]])
        frozen = recorder.history_page(grouped["cursor"])
        self.assertEqual(frozen["events"], grouped["events"])
        for query in (grouped, flat, current):
            recorder.close_query(query["cursor"])
        self.assertEqual(recorder.index.status()["snapshot_references"], 0)


class SourceChecks(unittest.TestCase):
    def setUp(self):
        self.sim = SimpleNamespace(id=1, sim_id=1, local=True)
        self.other = SimpleNamespace(id=2, sim_id=2, local=False)
        self.now = 10
        self.journal = MemoryJournal()
        self.recorder = Recorder(self.journal, "source")
        def ref(value):
            if value is not None and hasattr(value, "sim_id"):
                return entity("sim", value.sim_id, "Sim" + str(value.sim_id))
            if value is not None and hasattr(value, "object_id"):
                return entity("object", value.object_id)
        def resource(value, *args, **kwargs):
            return {"id": str(getattr(value, "guid64", 90)), "tuning_name": getattr(value, "__name__", type(value).__name__)}
        self.adapter = SimpleNamespace(event_reference=ref, event_local=lambda v: getattr(v, "local", False),
            resource=resource, clock=lambda: {"ticks": str(self.now)},
            services=SimpleNamespace(sim_info_manager=lambda: SimpleNamespace(get=lambda i: {1:self.sim, 2:self.other}.get(i))))
        self.runtime = SimpleNamespace(adapter=self.adapter, recorder=self.recorder, config=dict(DEFAULTS),
                                       hooks=Hooks(self.recorder.fail))
        self.sources = EventSources(self.runtime)
        self.addCleanup(self.runtime.hooks.remove)

    def numeric_fixture(self):
        owner = self.sim
        class Stat:
            guid64 = 55
            def __init__(self):
                self._value = 90
                self.tracker = SimpleNamespace(owner=owner)
                self.get_value = Mock(side_effect=AssertionError("continuous reads forbidden"))
            def _notify_change(self, old_value, unclamped_target_value, from_load=False, notify_watchers=True):
                return "unchanged protocol"
            def set(self, value, from_load=False):
                old = self._value
                self._value = min(100, value)
                self._notify_change(old, value, from_load)
        stat = Stat()
        class Op:
            def _apply_to_subject_and_target(self, subject, target, resolver, fail=False, from_load=False):
                stat.set(120, from_load)
                if fail:
                    raise ValueError("EA exception")
                return 123
        self.sources.wrap(Stat, "_notify_change", "numeric", "BaseStatistic._notify_change")
        self.sources.wrap(Op, "_apply_to_subject_and_target", "operation", "ExampleOp.apply")
        return stat, Op()

    def test_only_explicit_operation_records_actual_clamped_numeric_change(self):
        stat, op = self.numeric_fixture()
        stat.set(95)
        self.assertFalse(self.recorder.events)
        result = op._apply_to_subject_and_target(self.sim, None, SimpleNamespace())
        self.assertEqual(result, 123)
        event = next(iter(self.recorder.events.values()))
        self.assertEqual(event["category"], "statistic.direct")
        self.assertEqual((event["payload"]["before"], event["payload"]["after"]), (95, 100))
        self.assertEqual(event["cause"]["basis"], "loot_operation_call")
        self.assertIn("operation_id", event["cause"])
        stat.get_value.assert_not_called()
        self.assertEqual(self.sources.frames, [])

    def test_noop_restore_failed_operation_and_exception_cleanup(self):
        stat, op = self.numeric_fixture()
        with self.assertRaisesRegex(ValueError, "EA exception"):
            op._apply_to_subject_and_target(self.sim, None, SimpleNamespace(), fail=True)
        self.assertEqual(self.sources.frames, [])
        self.assertFalse(self.recorder.events)
        stat._value = 90
        op._apply_to_subject_and_target(self.sim, None, SimpleNamespace(), from_load=True)
        self.assertFalse(self.recorder.events)
        op._apply_to_subject_and_target(self.sim, None, SimpleNamespace())
        self.assertFalse(self.recorder.events)  # Already at the clamp, no actual effect.

    def test_unknown_old_numeric_value_does_not_become_a_direct_effect(self):
        stat, op = self.numeric_fixture()
        stat._value = None
        op._apply_to_subject_and_target(self.sim, None, SimpleNamespace())
        self.assertFalse(self.recorder.events)

    def test_numeric_effect_links_interaction_and_out_of_scope_participant(self):
        stat, op = self.numeric_fixture()
        stat.tracker = SimpleNamespace(rel_data=SimpleNamespace(sim_id_a=1, sim_id_b=2))
        interaction = SimpleNamespace(id=30, guid64=40, sim=self.sim)
        op._apply_to_subject_and_target(self.sim, self.other, SimpleNamespace(interaction=interaction))
        event = next(iter(self.recorder.events.values()))
        self.assertEqual(event["entities"], ["sim:1", "sim:2"])
        self.assertEqual(event["cause"]["event_id"], "source:interaction:1:30")

    def test_death_is_local_at_entry_even_if_sim_is_gone_at_notification(self):
        sim = self.sim
        class Death:
            _sim_info = sim
            death_type = None
            def _set_death_type(self, death_type):
                sim.local = False
                self.death_type = death_type
        self.sources.wrap(Death, "_set_death_type", "death", "DeathTracker._set_death_type")
        Death()._set_death_type("OLD_AGE")
        event = self.recorder.query_history("sim:1")["events"][0]
        self.assertEqual(event["category"], "life.death")
        self.assertEqual(event["payload"]["after"], "OLD_AGE")
        self.assertEqual(self.recorder.references["sim:1"]["id"], "1")
        Death()._set_death_type("OFF_LOT")
        self.assertEqual(len(self.recorder.events), 1)

    def test_buff_handles_refresh_removal_and_nested_dedup(self):
        sim = self.sim
        Buff = type("SomeBuff", (), {"guid64": 7})
        class Component:
            owner = sim
            def __init__(self):
                self._active_buffs = {}
                self.next_id = 0
            def add_buff(self, buff_type, buff_reason=None, from_load=False):
                self.next_id += 1
                buff = self._active_buffs.setdefault(buff_type, SimpleNamespace(handle_ids=set(), buff_reason=buff_reason))
                buff.handle_ids.add(self.next_id)
                return self.next_id
            def remove_buff(self, handle_id):
                for buff_type, buff in tuple(self._active_buffs.items()):
                    buff.handle_ids.discard(handle_id)
                    if not buff.handle_ids:
                        self.remove_buff_entry(buff_type)
            def remove_buff_entry(self, buff_type):
                self._active_buffs.pop(buff_type, None)
        for name, kind in (("add_buff", "buff_add"), ("remove_buff", "buff_remove"), ("remove_buff_entry", "buff_remove")):
            self.sources.wrap(Component, name, kind, "BuffComponent." + name)
        component = Component()
        first, second = component.add_buff(Buff, "food"), component.add_buff(Buff, "food")
        component.remove_buff(first)
        component.remove_buff(second)
        events = list(self.recorder.events.values())
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0]["after"]["id"], "7")
        self.assertEqual(events[1]["category"], "buff.refreshed")
        self.assertIsNone(events[-1]["after"])

    def test_native_birth_keeps_child_reference_and_crafting_has_product_role(self):
        child = self.other
        payload = {"offspring_infos": [child], "offspring_created": 1}
        self.sources.native(self.sim, "OffspringCreated", SimpleNamespace(get_resolved_arg=payload.get))
        born = self.recorder.query_history("sim:2")["events"][0]
        self.assertEqual(born["roles"][-1]["role"], "child")
        product = SimpleNamespace(object_id=10, local=True)
        payload = {"crafted_object": product, "quality": "normal", "masterwork": False}
        self.sources.native(self.sim, "ItemCrafted", SimpleNamespace(get_resolved_arg=payload.get))
        crafted = self.recorder.query_history("object:10")["events"][0]
        self.assertEqual(crafted["roles"][-1]["role"], "product")
        self.assertIn("recipe", crafted["payload"])
        self.assertTrue(explain_event(crafted)["text"])

    def test_knowledge_records_changed_fields_and_noop_does_not_emit(self):
        class Knowledge:
            _rel_data = SimpleNamespace(sim_id_a=1, sim_id_b=2)
            _known_traits = None
            def add_known_trait(self, trait):
                self._known_traits = {trait}
        self.sources.wrap(Knowledge, "add_known_trait", "knowledge", "SimKnowledge.add_known_trait")
        knowledge = Knowledge()
        knowledge.add_known_trait("kind")
        knowledge.add_known_trait("kind")
        self.assertEqual(len(self.recorder.events), 1)
        event = next(iter(self.recorder.events.values()))
        self.assertEqual(event["payload"]["changed_fields"], ["_known_traits"])
        self.assertEqual(event["roles"][1]["entity_key"], "sim:2")

    def test_skill_records_actual_levels_not_initial_or_requested_values(self):
        sources, sim = self.sources, self.sim
        class Skill:
            guid64 = 33
            tracker = SimpleNamespace(owner=sim)
            _value = 0
            def convert_to_user_value(self, value):
                return value // 10
            def set_value(self, value, from_load=False):
                self._value = min(100, value)
                payload = {"skill": self, "new_level": value // 10}
                sources.native(sim, "SkillLevelChange", SimpleNamespace(get_resolved_arg=payload.get))
        self.sources.wrap(Skill, "set_value", "skill", "Skill.set_value")
        skill = Skill()
        skill.set_value(0)
        skill.set_value(4)
        skill.set_value(20, from_load=True)
        self.assertFalse(self.recorder.events)
        skill.set_value(999)
        skill.set_value(90)
        events = list(self.recorder.events.values())
        self.assertEqual([(e["payload"]["before"], e["payload"]["after"]) for e in events], [(2, 10), (10, 9)])

    def test_spouse_dual_delivery_divorce_and_remarry(self):
        self.other.local = True
        for sim, old, new in ((self.sim, 0, 2), (self.other, 0, 1),
                              (self.sim, 2, 0), (self.other, 1, 0), (self.sim, 0, 2)):
            payload = {"ex_spouse_sim_id": old, "spouse_sim_id": new}
            self.sources.native(sim, "SpouseEvent", SimpleNamespace(get_resolved_arg=payload.get))
        events = list(self.recorder.events.values())
        self.assertEqual([e["payload"]["married"] for e in events], [True, False, True])
        self.assertTrue(all(set(e["entities"]) == {"sim:1", "sim:2"} for e in events))

    def test_inventory_success_noop_load_hidden_split_and_remove(self):
        sim = self.sim
        class Item:
            local = False
            def __init__(self, identifier, count):
                self.id = self.object_id = identifier
                self.count, self.container = count, None
                self.inventoryitem_component = SimpleNamespace(get_inventory=lambda: self.container,
                    stack_count=lambda: self.count, is_hidden=False)
        class Inventory:
            owner = sim
            def __init__(self):
                self._storage, self._hidden_storage = {}, {}
            def _insert_item(self, obj, reject=False):
                if reject:
                    return False
                self._storage[obj.id] = obj
                obj.container = self
                return True
            def add_from_load(self, obj):
                self._insert_item(obj)
            def try_move_object_to_hidden_inventory(self, obj):
                if obj.id not in self._storage:
                    return False
                self._hidden_storage[obj.id] = self._storage.pop(obj.id)
                obj.inventoryitem_component.is_hidden = True
                return True
            def try_split_object_from_stack_by_id(self, obj_id, count=1):
                obj = self._storage.get(obj_id)
                if obj is None or count >= obj.count:
                    return None
                obj.count -= count
                return Item(12, count)
            def try_remove_object_by_id(self, obj_id):
                obj = self._storage.pop(obj_id, None)
                if obj is None:
                    return False
                obj.container = None
                return True
        for method, kind in (("_insert_item", "inventory_insert"), ("add_from_load", "load_guard"),
            ("try_move_object_to_hidden_inventory", "inventory_move"),
            ("try_split_object_from_stack_by_id", "inventory_split"), ("try_remove_object_by_id", "inventory_remove")):
            self.sources.wrap(Inventory, method, kind, "Inventory." + method)
        inventory, item = Inventory(), Item(10, 3)
        inventory._insert_item(item, reject=True)
        inventory.add_from_load(Item(11, 1))
        self.assertFalse(self.recorder.events)
        inventory._insert_item(item)
        inventory._insert_item(item)
        inventory.try_split_object_from_stack_by_id(10, 3)
        inventory.try_split_object_from_stack_by_id(10, 1)
        inventory.try_move_object_to_hidden_inventory(item)
        inventory.try_remove_object_by_id(11)
        events = list(self.recorder.events.values())
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0]["payload"]["after"]["container"]["key"], "sim:1")
        self.assertEqual(events[1]["payload"]["after"]["stack_count"], 2)
        self.assertIn("object:12", events[1]["entities"])
        self.assertTrue(events[2]["payload"]["after"]["hidden"])
        self.assertIsNone(events[3]["payload"]["after"]["container"])

    def test_payments_record_actual_amount_and_require_local_operation(self):
        class Funds:
            _funds = 95
            def add(self, amount, reason, sim=None):
                self._funds = min(100, self._funds + amount)
            def try_remove_amount(self, amount, reason, sim=None):
                if amount > self._funds:
                    return False
                self._funds -= amount
                return True
        for method in ("add", "try_remove_amount"):
            self.sources.wrap(Funds, method, "money", "FamilyFunds." + method)
        funds = Funds()
        funds.add(20, 1, self.sim)
        funds.add(20, 1, self.sim)
        funds.try_remove_amount(200, 2, self.sim)
        funds.try_remove_amount(10, 2, self.sim)
        funds.try_remove_amount(10, 2)  # Unattributed household operation.
        events = list(self.recorder.events.values())
        self.assertEqual([e["payload"]["actual_amount"] for e in events], [5, -10])

    def test_pregnancy_age_retirement_and_demotion_actual_transitions(self):
        sim = self.sim
        class Pregnancy:
            _sim_info = sim
            _parent_ids = ()
            is_pregnant = False
            def start_pregnancy(self):
                self.is_pregnant, self._parent_ids = True, (1, 2)
            def clear_pregnancy(self):
                self.is_pregnant, self._parent_ids = False, ()
        class Career:
            _sim_info = sim
            level = 3
            current_track_tuning = "science"
            retired_career_uid = 0
            def _demote_within_track(self):
                self.level = max(1, self.level - 1)
            def retire_career(self, career_uid):
                self.retired_career_uid = career_uid
            def end_retirement(self):
                self.retired_career_uid = 0
        for owner, name, kind in ((Pregnancy, "start_pregnancy", "pregnancy"), (Pregnancy, "clear_pregnancy", "pregnancy"),
            (Career, "_demote_within_track", "demotion"), (Career, "retire_career", "retirement"), (Career, "end_retirement", "retirement")):
            self.sources.wrap(owner, name, kind, name)
        pregnancy, career = Pregnancy(), Career()
        pregnancy.start_pregnancy()
        pregnancy.start_pregnancy()
        pregnancy.clear_pregnancy()
        career._demote_within_track()
        career.retire_career(50)
        career.retire_career(50)
        career.end_retirement()
        events = list(self.recorder.events.values())
        self.assertEqual([e["category"] for e in events], ["life.pregnancy", "life.pregnancy", "career.demoted", "career.retirement", "career.retirement"])
        self.assertIn("sim:2", events[1]["entities"])
        self.assertEqual(events[-1]["payload"]["before"], "50")

    def test_milestone_rejection_and_sentiment_membership_only(self):
        sim = self.sim
        class Milestones:
            _sim_info = sim
            def __init__(self):
                self._active_milestones_data = {"birthday": SimpleNamespace(state="ACTIVE", age_completed=2)}
            def unlock_milestone(self, milestone, telemetry_context, reject=False):
                if not reject:
                    self._active_milestones_data[milestone].state = "UNLOCKED"
        class Sentiments:
            rel_data = SimpleNamespace(sim_id_a=1, sim_id_b=2)
            def __init__(self):
                self.stats = []
            def __iter__(self):
                return iter(self.stats)
            def add_statistic(self, stat):
                self.stats[:] = [stat]  # EA can replace a sentiment.
            def remove_statistic(self, stat):
                self.stats[:] = []
        self.sources.wrap(Milestones, "unlock_milestone", "milestone", "Milestones.unlock")
        for method in ("add_statistic", "remove_statistic"):
            self.sources.wrap(Sentiments, method, "sentiment", method)
        milestones, sentiments = Milestones(), Sentiments()
        milestones.unlock_milestone("birthday", "current", reject=True)
        milestones.unlock_milestone("birthday", "current")
        milestones.unlock_milestone("birthday", "current")
        first, second = SimpleNamespace(guid64=4), SimpleNamespace(guid64=5)
        sentiments.add_statistic(first)
        sentiments.add_statistic(first)
        sentiments.add_statistic(second)
        sentiments.remove_statistic(second)
        events = list(self.recorder.events.values())
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0]["category"], "life.milestone")
        self.assertEqual(set(events[2]["payload"]["before"]), {"4"})
        self.assertEqual(set(events[2]["payload"]["after"]), {"5"})

    def test_broadcast_only_after_gate_repeated_callbacks_revise_and_remove(self):
        class Broadcaster:
            broadcasting_object = self.sim
            interaction = None
        class Effect:
            def apply_broadcaster_effect(self, broadcaster, affected_object, allowed=True):
                if allowed:
                    self._apply_broadcaster_effect(broadcaster, affected_object)
            def _apply_broadcaster_effect(self, broadcaster, affected_object):
                pass
            def remove_broadcaster_effect(self, broadcaster, affected_object):
                pass
        self.sources.wrap(Effect, "_apply_broadcaster_effect", "broadcast", "Effect.apply")
        self.sources.wrap(Effect, "remove_broadcaster_effect", "broadcast_remove", "Effect.remove")
        effect, broadcaster = Effect(), Broadcaster()
        effect.apply_broadcaster_effect(broadcaster, self.sim, allowed=False)
        self.assertFalse(self.recorder.events)
        for _ in range(19):
            effect.apply_broadcaster_effect(broadcaster, self.sim)
        self.assertEqual(len(self.recorder.events), 1)
        event = next(iter(self.recorder.events.values()))
        self.assertEqual((event["revision"], event["payload"]["observed_callbacks"]), (19, 19))
        effect.remove_broadcaster_effect(broadcaster, self.sim)
        event = next(iter(self.recorder.events.values()))
        self.assertEqual(event["payload"]["observed_callbacks"], 19)
        self.assertEqual(event["payload"]["phase"], "remove_callback_returned")
        self.assertEqual(event["payload"]["effect_success"], "not_inferred")
        effect.apply_broadcaster_effect(broadcaster, self.sim)
        self.assertEqual(len(self.recorder.events), 2)

    def test_nested_distinct_milestones_are_not_swallowed_as_duplicates(self):
        sim = self.sim
        class Milestones:
            _sim_info = sim
            def __init__(self):
                self._active_milestones_data = {name: SimpleNamespace(state="ACTIVE") for name in ("a", "b")}
            def unlock_milestone(self, milestone, telemetry_context):
                self._active_milestones_data[milestone].state = "UNLOCKED"
                if milestone == "a":
                    self.unlock_milestone("b", telemetry_context)
        self.sources.wrap(Milestones, "unlock_milestone", "milestone", "Milestones.unlock")
        Milestones().unlock_milestone("a", "LOD_UP")
        events = list(self.recorder.events.values())
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e["payload"]["record_origin"] == "lod_recovery" for e in events))
        self.assertTrue(all("不代表" in explain_event(e)["text"] for e in events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
