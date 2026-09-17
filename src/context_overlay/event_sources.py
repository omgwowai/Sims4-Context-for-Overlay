"""Event adapters for the verified EA senders; no periodic value collection.

Game imports are deferred to install(). Frames are synchronous call evidence,
not time-window guesses. Only JSON values cross into the recorder.
"""

import importlib
import inspect
import math
import weakref
from collections import OrderedDict

from context_overlay.ea_adapter import enum_name
from context_overlay.hooks import arg
from context_overlay.model import copy_data, entity, new_id
from context_overlay.event_policy import suppressed_statistic, completion_tier


# category, payload keys. These are actual sender arguments, not all fields
# that happen to be available from the Sim at query time.
NATIVE = {
    "MoodChange": ("mood.changed", ("old_mood", "new_mood")),
    "SkillLevelChange": ("skill.level", ("skill", "new_level")),
    "TraitAddEvent": ("trait.added", ("trait_guid", "trait_type")),
    "TraitRemoveEvent": ("trait.removed", ("trait_guid", "trait_type")),
    "SpouseEvent": ("relationship.spouse", ("ex_spouse_sim_id", "spouse_sim_id")),
    "CareerEvent": ("career.changed", ("career", "track", "level", "test_event_origin")),
    "CareerPromoted": ("career.promoted", ("career", "track", "level", "test_event_origin")),
    "WorkdayStart": ("career.work_started", ("career",)),
    "WorkdayComplete": ("career.work_completed", ("career", "time_worked", "money_made")),
    "OffspringCreated": ("life.offspring_created", ("offspring_created",)),
    "ChildAdopted": ("life.adopted", ()),
    "ItemCrafted": ("crafting.completed", ("skill", "quality", "masterwork")),
    "CollectedItem": ("collection.acquired", ("collection_id", "collected_item_id", "stack_count")),
    "UnlockEvent": ("progress.unlocked", ("unlocked",)),
    "UnlockTrackerItemUnlocked": ("progress.item_unlocked", ()),
    "AspirationGoalComplete": ("aspiration.goal_completed", ()),
    "MilestoneCompleted": ("aspiration.stage_completed", ()),
}

def resolved(resolver, key):
    try:
        return resolver.get_resolved_arg(key)
    except (KeyError, AttributeError):
        return None


class EventSources:
    def __init__(self, runtime):
        self.runtime = runtime
        self.adapter = runtime.adapter
        self.recorder = runtime.recorder
        self.frames = []
        self.coverage = {}
        self._broadcasts = OrderedDict()
        self._spouses = OrderedDict()
        self._hooked = set()
        self.callback_counts = {}
        self.suppressed_counts = {}

    def status(self):
        return copy_data(self.coverage)

    def diagnostics(self):
        return {"callbacks": dict(self.callback_counts), "suppressed_statistics": dict(self.suppressed_counts),
                "suppression_policy": "elapsed_time_counter", "timing": "not_measured"}

    def roles(self, values, names, basis):
        return [{"entity_key": ref["key"], "role": role, "basis": basis}
                for value, role in zip(values, names)
                for ref in (self.adapter.event_reference(value),) if ref]

    def scalar(self, value, depth=0):
        if depth > 3:
            return {"status": "unavailable", "reason": "nested_payload"}
        if value is None or isinstance(value, (bool, str)):
            return value
        if isinstance(value, (int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                return None
            return enum_name(value) if hasattr(value, "name") else value
        reference = self.adapter.event_reference(value)
        if reference:
            return reference
        if isinstance(value, (list, tuple, set, frozenset, dict)):
            if len(value) > self.runtime.config["max_entities"]:
                raise RuntimeError("Event payload exceeds entity budget")
            if isinstance(value, dict):
                return {str(self.scalar(key, depth + 1)): self.scalar(item, depth + 1) for key, item in value.items()}
            items = [self.scalar(item, depth + 1) for item in value]
            return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
        if hasattr(value, "guid64") or hasattr(type(value), "guid64"):
            return self.adapter.resource(value)
        if hasattr(value, "in_ticks"):
            return {"ticks": str(value.in_ticks())}
        if hasattr(value, "hash"):
            return self.adapter.localizer.name(value, "LocalizedString")
        return {"type": type(value).__name__, "status": "unavailable", "reason": "no_verified_serializer"}

    def info(self, identifier):
        if not identifier:
            return None
        return self.adapter.services.sim_info_manager().get(int(identifier))

    def refs(self, values):
        result = {}
        for value in values:
            ref = self.adapter.event_reference(value)
            if ref:
                result[ref["key"]] = ref
        return list(result.values())

    def local(self, values):
        return any(self.adapter.event_local(value) for value in values if value is not None)

    def cause(self, resolver=None, interaction=None, basis="resolver.interaction"):
        if interaction is None and resolver is not None:
            interaction = getattr(resolver, "interaction", None)
        if interaction is not None and getattr(interaction, "sim", None) is not None:
            actor = self.adapter.event_reference(interaction.sim)
            return {"event_id": self.recorder.interaction_event_id(actor["id"], interaction.id),
                    "actor": actor, "basis": basis, "source_tuning": self.adapter.resource(interaction, resource_kind="interaction")}
        for frame in reversed(self.frames):
            if frame.get("cause"):
                return copy_data(frame["cause"])
        return None

    def emit(self, category, values, payload, source, roles=None, cause=None, evidence="notification", event_id=None, tier="main"):
        if self.recorder.paused or not self.recorder.enabled:
            return None
        local = self.local(values) or any(frame["local"] for frame in self.frames)
        if not local or any(frame.get("from_load") for frame in self.frames):
            return None
        references = self.refs(values)
        if not references:
            return None
        payload = dict(payload, scope_evidence="local_at_call" if any(f["local"] for f in self.frames) else "local_at_notification")
        return self.recorder.fact(category, references, payload, self.adapter.clock(), source,
                                  roles=roles, cause=cause or self.cause(), evidence=evidence, event_id=event_id, tier=tier)

    def native(self, sim_info, name, resolver):
        if name not in NATIVE:
            return False
        if self.recorder.paused or not self.recorder.enabled or any(f["from_load"] for f in self.frames):
            return True
        source = "TestEvent." + name
        self.callback_counts[source] = self.callback_counts.get(source, 0) + 1
        if name == "SkillLevelChange" and any(f["kind"] == "skill" for f in self.frames):
            return True  # The method adapter checks the actual discrete levels.
        category, keys = NATIVE[name]
        values = [sim_info]
        observed = {key: resolved(resolver, key) for key in (*keys, "crafted_object", "adopted_sim_info")}
        payload = {key: (str(observed[key]) if observed[key] is not None and key.endswith(("_id", "_guid"))
                         else self.scalar(observed[key])) for key in keys}
        for key, resource_kind in (("skill", "statistic"), ("track", "career_track")):
            if observed.get(key) is not None:
                payload[key] = self.adapter.resource(observed[key], resource_kind, tokens=(sim_info,))
        roles = []
        cause = self.cause(resolved(resolver, "resolver"))
        if name == "MoodChange":
            frame = next((f for f in reversed(self.frames) if f["kind"] == "mood_context" and
                          f["mood_owner"].owner is sim_info), None)
            old_level = frame["old_intensity"] if frame and frame["old_mood"] is observed["old_mood"] else None
            new_level = (getattr(frame["mood_owner"], "_active_mood_intensity", None) if frame and
                         getattr(frame["mood_owner"], "_active_mood", None) is observed["new_mood"] else None)
            for key, level in (("old_mood", old_level), ("new_mood", new_level)):
                payload[key] = self.adapter.resource(observed[key], resource_kind="mood", tokens=(sim_info,), intensity=level)
            payload.update(old_intensity=old_level, new_intensity=new_level,
                change_kind="mood" if observed["old_mood"] is not observed["new_mood"] else
                            ("intensity" if old_level is not None and new_level is not None and old_level != new_level else "notification"))
        if name == "SkillLevelChange":
            payload["interpretation"] = "notification_only_initialization_not_excluded"
        if name == "WorkdayComplete":
            payload["money_made_basis"] = "game_reported_calculation_not_bank_delta"
        if sim_info is not None:
            roles.append({"entity_key": self.adapter.event_reference(sim_info)["key"], "role": "subject", "basis": "TestEvent.sim_info"})
        for key, role in (("crafted_object", "product"), ("adopted_sim_info", "adopted_child")):
            value = observed[key]
            if value is not None:
                values.append(value)
                roles.append({"entity_key": self.adapter.event_reference(value)["key"], "role": role, "basis": key})
                payload[key] = self.scalar(value)
        if name == "OffspringCreated":
            for value in resolved(resolver, "offspring_infos") or ():
                values.append(value)
                roles.append({"entity_key": self.adapter.event_reference(value)["key"], "role": "child", "basis": "offspring_infos"})
        if name == "SpouseEvent":
            if sim_info is None or not (self.local(values) or any(f["local"] for f in self.frames)):
                return True
            for key, married in (("ex_spouse_sim_id", False), ("spouse_sim_id", True)):
                identifier = observed[key]
                if not identifier:
                    continue
                if observed["ex_spouse_sim_id"] == observed["spouse_sim_id"]:
                    continue
                pair = tuple(sorted((str(sim_info.sim_id), str(identifier))))
                previous = self._spouses.get(pair)
                if previous is not None and previous == married:
                    continue
                other = self.info(identifier)
                refs = self.refs([sim_info, other])
                if other is None:
                    refs.append(entity("sim", identifier))
                event = self.recorder.fact(category, refs, {"spouses": list(pair), "married": married,
                    "reporting_sim_id": str(sim_info.sim_id)}, self.adapter.clock(), "TestEvent.SpouseEvent",
                    roles=[{"entity_key": ref["key"], "role": "spouse", "basis": "SpouseEvent"} for ref in refs],
                    cause=self.cause())
                if event:
                    self._spouses[pair] = married
                    self._spouses.move_to_end(pair)
                    if len(self._spouses) > self.recorder.capacity:
                        self._spouses.popitem(last=False)
            return True
        if name in ("TraitAddEvent", "TraitRemoveEvent"):
            import sims4.resources
            trait = self.adapter.services.get_instance_manager(sims4.resources.Types.TRAIT).get(observed["trait_guid"])
            if trait is not None:
                payload["trait"] = self.adapter.resource(trait, "trait", tokens=(sim_info,))
        if name == "ItemCrafted":
            product = observed["crafted_object"]
            component = getattr(product, "crafting_component", None)
            process = getattr(component, "_crafting_process", None)
            payload["recipe"] = self.adapter.resource(getattr(process, "recipe", None), resource_kind="recipe", tokens=(sim_info,))
            payload["masterwork"] = self.adapter.resource(observed["masterwork"], resource_kind="object_state")
            payload["quality"] = self.adapter.resource(observed["quality"], resource_kind="object_state")
            interaction = getattr(process, "_current_crafting_interaction", None)
            cause = self.cause(interaction=interaction, basis="crafting_process.current_interaction") or cause
            payload["record_origin"] = "item_crafted_notification"
        if name in ("AspirationGoalComplete", "MilestoneCompleted", "UnlockTrackerItemUnlocked"):
            payload["missing_fields"] = ["objective_or_milestone_identity"]
            for frame in reversed(self.frames):
                if frame.get("identities"):
                    payload.update(frame["identities"])
                    payload.pop("missing_fields", None)
                    break
        tier = completion_tier(payload) if name in ("AspirationGoalComplete", "MilestoneCompleted") else "main"
        self.emit(category, values, payload, source, roles, cause, tier=tier)
        return True

    def hook(self, module, class_name, method, kind, optional=False):
        source = module + "." + class_name + "." + method
        try:
            owner = getattr(importlib.import_module(module), class_name)
            self.wrap(owner, method, kind, source)
            self.coverage[source] = {"state": "installed", "kind": kind, "validation": "offline_only"}
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            self.coverage[source] = {"state": "unavailable", "reason": str(exc), "optional": optional}

    def wrap(self, owner, method, kind, source):
        if (owner, method) in self._hooked:
            return
        original = getattr(owner, method)
        if inspect.isgeneratorfunction(original):
            raise AttributeError("Generator hook requires a dedicated adapter")
        signature = inspect.signature(original)
        self.runtime.hooks.around(owner, method,
            lambda args, kwargs: self.before(kind, source, args, dict(signature.bind_partial(*args, **kwargs).arguments)),
            lambda frame, args, kwargs, result, error: self.after(frame, args, kwargs, result, error))
        self._hooked.add((owner, method))

    def subclasses(self, module, class_name, method, kind):
        try:
            root = getattr(importlib.import_module(module), class_name)
            pending, seen, count, unavailable = [root], set(), 0, []
            while pending:
                owner = pending.pop()
                if owner in seen:
                    continue
                seen.add(owner)
                pending.extend(owner.__subclasses__())
                if method in owner.__dict__:
                    source = owner.__module__ + "." + owner.__name__ + "." + method
                    try:
                        self.wrap(owner, method, kind, source)
                        count += 1
                    except (AttributeError, TypeError, ValueError) as exc:
                        unavailable.append({"source": source, "reason": str(exc)})
            self.coverage[module + "." + class_name + "." + method] = {
                "state": "installed", "implementations": count, "validation": "offline_only",
                "unavailable_implementations": unavailable,
                "limitation": "subclasses_loaded_at_install"}
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            self.coverage[module + "." + class_name + "." + method] = {"state": "unavailable", "reason": str(exc)}

    def install(self):
        for name in NATIVE:
            try:
                from event_testing.test_events import TestEvent
                event = getattr(TestEvent, name)
                self.runtime.manager.register_single_event(self.runtime, event)
                self.runtime.events.append(event)
                self.runtime.event_names[event] = name
                self.coverage[name] = {"state": "installed", "validation": "offline_only"}
            except AttributeError as exc:
                self.coverage[name] = {"state": "unavailable", "reason": str(exc)}
        for module, cls, method, kind in HOOKS:
            self.hook(module, cls, method, kind)
        for name in KNOWLEDGE_METHODS:
            self.hook("relationships.sim_knowledge", "SimKnowledge", name, "knowledge")
        self.subclasses("interactions.utils.loot_basic_op", "BaseLootOperation", "_apply_to_subject_and_target", "operation")
        self.subclasses("broadcasters.broadcaster_effect", "_BroadcasterEffect", "_apply_broadcaster_effect", "broadcast")
        self.subclasses("broadcasters.broadcaster_effect", "_BroadcasterEffect", "remove_broadcaster_effect", "broadcast_remove")
        self.subclasses("interactions.payment.payment_cost", "_Payment", "on_payment", "payment_context")

    def before(self, kind, source, args, kwargs):
        frame = {"kind": kind, "source": source, "local": False, "values": [], "before": None,
                 "cause": self.cause(), "from_load": bool(kwargs.get("from_load", False)), "numeric": []}
        if self.recorder.paused or not self.recorder.enabled:
            return frame
        self.callback_counts[source] = self.callback_counts.get(source, 0) + 1
        obj = args[0]
        if kind == "numeric" and suppressed_statistic(type(obj).__name__):
            key = type(obj).__name__
            if key not in self.suppressed_counts and len(self.suppressed_counts) >= 128:
                key = "other_elapsed_time_counters"
            self.suppressed_counts[key] = self.suppressed_counts.get(key, 0) + 1
            return frame
        frame["owner_id"] = id(obj)
        if len(self.frames) >= 128:
            raise RuntimeError("Event source call depth exceeds observation budget")
        if kind == "operation":
            frame["values"] = [arg(args, kwargs, 1, "subject"), arg(args, kwargs, 2, "target")]
            resolver = arg(args, kwargs, 3, "resolver")
            frame["cause"] = self.cause(resolver) or {"basis": "loot_operation_call"}
            frame["cause"]["operation"] = {"type": type(obj).__name__}
            frame["cause"]["operation_id"] = new_id()
        elif kind in ("broadcast", "broadcast_remove"):
            broadcaster = arg(args, kwargs, 1, "broadcaster")
            affected = arg(args, kwargs, 2, "affected_object")
            frame["values"] = [affected, getattr(broadcaster, "broadcasting_object", None)]
            frame["cause"] = self.cause(interaction=getattr(broadcaster, "interaction", None))
            try:
                broadcaster_identity = weakref.ref(broadcaster)
                hash(broadcaster_identity)
            except TypeError:
                broadcaster_identity = new_id()  # Cannot safely coalesce unknown identities.
            frame["broadcast_key"] = (broadcaster_identity, id(obj), getattr(affected, "id", None))
            frame["payload"] = {"broadcaster": {"instance_id": str(getattr(broadcaster, "id", "")),
                "resource": self.adapter.resource(broadcaster, resource_kind="broadcaster")}, "effect": type(obj).__name__}
            frame["roles"] = self.roles(frame["values"], ("subject", "broadcast_source"), "broadcaster_callback")
        elif kind == "numeric":
            # _notify_change receives the true old value; _value is already clamped.
            # No get_value() call: ContinuousStatistic.get_value can integrate decay.
            context = next((f for f in reversed(self.frames) if f["kind"] == "operation"), None)
            if context and not frame["from_load"]:
                tracker = getattr(obj, "tracker", None)
                rel = getattr(tracker, "rel_data", None)
                if rel is not None:
                    frame["values"] = [self.info(rel.sim_id_a), self.info(rel.sim_id_b)]
                else:
                    frame["values"] = [getattr(tracker, "owner", None)]
                frame["roles"] = self.roles(frame["values"], ("subject", "target"), "statistic_tracker_owner")
                frame["before"] = arg(args, kwargs, 1, "old_value")
                frame["after"] = getattr(obj, "_value", None)
                frame["statistic"] = self.adapter.resource(obj, "statistic")
                frame["cause"] = copy_data(context["cause"])
                frame["operation_frame"] = context
        else:
            frame.update(capture_before(self, kind, args, kwargs))
        frame["local"] = self.local(frame["values"]) or any(f["local"] for f in self.frames)
        self.frames.append(frame)
        frame["entered"] = True
        return frame

    def after(self, frame, args, kwargs, result, error):
        if not frame.get("entered"):
            return
        try:
            if error is not None or self.recorder.paused or any(f["from_load"] for f in self.frames):
                return
            kind = frame["kind"]
            if kind == "numeric":
                actual_values = all(isinstance(value, (int, float)) and not isinstance(value, bool)
                                    and math.isfinite(value) for value in (frame.get("before"), frame.get("after")))
                if frame.get("statistic") and frame["local"] and actual_values and frame["before"] != frame["after"]:
                    if len(frame["operation_frame"]["numeric"]) >= self.runtime.config["writer_capacity"]:
                        raise RuntimeError("Operation effects exceed observation buffer budget")
                    frame["operation_frame"]["numeric"].append((frame["values"], {
                        "statistic": frame["statistic"], "before": frame["before"], "after": frame["after"]}, frame["cause"], frame["roles"]))
            elif kind == "operation":
                for values, payload, cause, roles in frame["numeric"]:
                    self.emit("statistic.direct", values, payload, frame["source"], cause=cause, roles=roles, evidence="scoped_actual_change")
            elif kind in ("broadcast", "broadcast_remove"):
                self.broadcast_after(frame)
            else:
                capture_after(self, frame, args, kwargs, result)
        finally:
            self.frames.pop()

    def broadcast_after(self, frame):
        if not frame["local"]:
            return
        key = frame["broadcast_key"]
        previous = self._broadcasts.get(key)
        removing = frame["kind"] == "broadcast_remove"
        if removing and previous is None:
            return
        count = previous[1] + (0 if removing else 1) if previous else 1
        identifier = previous[0] if previous and previous[0] in self.recorder.events else None
        payload = dict(frame["payload"], observed_callbacks=count,
                       phase="remove_callback_returned" if removing else "apply_callback_returned",
                       effect_success="not_inferred", perception="not_inferred")
        event = self.emit("broadcast.effect", frame["values"], payload, frame["source"],
                          cause=frame["cause"], roles=frame["roles"], event_id=identifier, evidence="tested_effect_callback")
        if removing:
            self._broadcasts.pop(key, None)
        elif event:
            self._broadcasts[key] = (event["event_id"], count)
            self._broadcasts.move_to_end(key)
            if len(self._broadcasts) > self.recorder.capacity:
                self._broadcasts.popitem(last=False)

# Method adapters are kept separate below; all snapshots are event-bound and
# discrete. Numeric snapshots are exclusively scoped _notify_change arguments.
HOOKS = [
    ("objects.components.buff_component", "BuffComponent", "_update_current_mood", "mood_context"),
    ("crafting.crafting_process", "CraftingProcess", "pay_for_item", "craft_payment_context"),
    ("crafting.crafting_interactions", "CraftingPhaseSuperInteractionMixin", "_go_to_next_phase", "interaction_context"),
    ("interactions.payment.payment_cost", "_Payment", "make_payment", "payment_context"),
    ("objects.components.inventory_elements", "InventoryTransfer", "_do_behavior", "element_context"),
    ("carry.carry_elements", "CarryElementHelper", "_do_enter_carry", "element_context"),
    ("carry.carry_elements", "CarryElementHelper", "_do_exit_carry", "element_context"),
    ("carry.carry_postures", "CarrySystemInventoryTarget", "carry_event_callback", "inventory_carry_context"),
    ("statistics.base_statistic", "BaseStatistic", "_notify_change", "numeric"),
    ("statistics.skill", "Skill", "set_value", "skill"),
    ("statistics.skill", "Skill", "_update_value", "skill"),
    ("careers.career_base", "CareerBase", "_demote_within_track", "demotion"),
    ("careers.career_tracker", "CareerTracker", "retire_career", "retirement"),
    ("careers.career_tracker", "CareerTracker", "end_retirement", "retirement"),
    ("objects.components.buff_component", "BuffComponent", "add_buff", "buff_add"),
    ("objects.components.buff_component", "BuffComponent", "remove_buff", "buff_remove"),
    ("objects.components.buff_component", "BuffComponent", "remove_buff_entry", "buff_remove"),
    ("sims.pregnancy.pregnancy_tracker", "PregnancyTracker", "start_pregnancy", "pregnancy"),
    ("sims.pregnancy.pregnancy_tracker", "PregnancyTracker", "clear_pregnancy", "pregnancy"),
    ("sims.pregnancy.pregnancy_tracker", "PregnancyTracker", "complete_pregnancy", "local_context"),
    ("sims.aging.aging_mixin", "AgingMixin", "advance_age", "age"),
    ("interactions.utils.death", "DeathTracker", "_set_death_type", "death"),
    ("interactions.utils.death", "DeathTracker", "clear_death_type", "death"),
    ("sims.sim_info", "SimInfo", "assign_to_household", "household"),
    ("developmental_milestones.developmental_milestone_tracker", "DevelopmentalMilestoneTracker", "unlock_milestone", "milestone"),
    ("relationships.sentiment_track_tracker", "SentimentTrackTracker", "add_statistic", "sentiment"),
    ("relationships.sentiment_track_tracker", "SentimentTrackTracker", "remove_statistic", "sentiment"),
    ("objects.components.inventory", "InventoryComponent", "_insert_item", "inventory_insert"),
    ("objects.components.inventory", "InventoryComponent", "try_remove_object_by_id", "inventory_remove"),
    ("objects.components.inventory", "InventoryComponent", "try_split_object_from_stack_by_id", "inventory_split"),
    ("objects.components.inventory", "InventoryComponent", "try_move_object_to_hidden_inventory", "inventory_move"),
    ("objects.components.inventory", "InventoryComponent", "add_from_load", "load_guard"),
    ("sims.funds", "FamilyFunds", "add", "money"),
    ("sims.funds", "FamilyFunds", "try_remove_amount", "money"),
    ("aspirations.aspirations", "AspirationTracker", "complete_objective", "objective_context"),
    ("aspirations.aspirations", "AspirationTracker", "complete_milestone", "objective_context"),
    ("sims.unlock_tracker", "UnlockTracker", "add_unlock", "unlock_context"),
]

KNOWLEDGE_METHODS = (
    "add_known_trait", "remove_known_trait", "add_knows_romantic_preference", "remove_knows_romantic_preference",
    "add_knows_woohoo_preference", "remove_knows_woohoo_preference", "add_knows_relationship_expectations",
    "remove_knows_relationship_expectations", "add_knows_relationship_status", "add_knows_career",
    "remove_knows_career", "set_known_net_worth", "add_known_stat", "add_known_rel_track",
    "add_knows_major", "remove_knows_major", "set_unconfronted_secret", "make_secret_known")

KNOWLEDGE_FIELDS = ("_known_traits", "_knows_career", "_known_stats", "_known_rel_tracks", "_knows_major",
    "_knows_rel_status", "_knows_romantic_preference", "_knows_woohoo_preference", "_known_romantic_genders",
    "_known_woohoo_genders", "_known_exploring_sexuality", "_known_net_worth", "_confronted_secrets",
    "_unconfronted_secret", "_known_relationship_expectations")


# The same discrete reader is used before and after a method call.
TRANSITIONS = {
    "knowledge": ("relationship.knowledge", lambda s, obj: {key: s.scalar(getattr(obj, key, None)) for key in KNOWLEDGE_FIELDS}),
    "sentiment": ("relationship.sentiment", lambda s, obj: {str(stat.guid64): s.scalar(stat) for stat in obj}),
    "skill": ("skill.level", lambda s, obj: obj.convert_to_user_value(obj._value)),
    "demotion": ("career.demoted", lambda s, obj: obj.level),
    "retirement": ("career.retirement", lambda s, obj: str(obj.retired_career_uid) if obj.retired_career_uid else None),
    "pregnancy": ("life.pregnancy", lambda s, obj: {
        "is_pregnant": bool(obj.is_pregnant), "parent_ids": [str(v) for v in obj._parent_ids]}),
    "death": ("life.death", lambda s, obj: s.scalar(obj.death_type)),
    "age": ("life.age", lambda s, obj: s.scalar(obj.age)),
    "household": ("life.household", lambda s, obj: str(obj.household_id) if obj.household_id else None),
    "money": ("payment.completed", lambda s, obj: obj._funds),
}


def buff_snapshot(sources, component):
    buffs = component._active_buffs
    if len(buffs) > sources.runtime.config["max_buffs_per_sim"]:
        raise RuntimeError("Buff observation exceeds configured budget")
    return {str(buff_type.guid64): {
        "buff": sources.adapter.resource(buff_type, "buff", tokens=(component.owner,)),
        "handles": sorted(str(handle) for handle in buff.handle_ids),
        "reason": sources.scalar(getattr(buff, "buff_reason", None)),
        "mood": sources.adapter.resource(getattr(buff, "mood_type", None), resource_kind="mood"),
        "mood_weight": sources.scalar(getattr(buff, "mood_weight", None))}
        for buff_type, buff in buffs.items()}


def inventory_snapshot(sources, item):
    component = getattr(item, "inventoryitem_component", None)
    if component is None:
        return None
    inventory = component.get_inventory()
    return {"container": sources.adapter.event_reference(getattr(inventory, "owner", None)),
            "stack_count": component.stack_count(), "hidden": bool(getattr(component, "is_hidden", False))}


def capture_before(sources, kind, args, kwargs):
    obj = args[0]
    values, before, extra = [], None, {}
    if kind == "mood_context":
        values = [obj.owner]
        extra.update(mood_owner=obj, old_mood=getattr(obj, "_active_mood", None), old_intensity=getattr(obj, "_active_mood_intensity", None))
    elif kind in ("interaction_context", "element_context", "payment_context", "craft_payment_context"):
        interaction = obj if kind == "interaction_context" else getattr(obj, "interaction", None)
        if kind == "craft_payment_context":
            interaction = getattr(obj, "_current_crafting_interaction", None)
            values = [getattr(obj, "_paying_sim", None)]
            extra["payment_recipe"] = sources.adapter.resource(getattr(obj, "recipe", None), resource_kind="recipe", tokens=tuple(v for v in values if v is not None))
        resolver = kwargs.get("resolver")
        if interaction is None and resolver is not None:
            interaction = getattr(resolver, "interaction", None)
        values.extend([kwargs.get("sim"), getattr(interaction, "sim", None)])
        values = [value for value in values if value is not None]
        extra["cause"] = sources.cause(resolver, interaction, basis=kind)
    elif kind == "inventory_carry_context":
        # The carry target proves the carrying Sim, but has no interaction ID.
        values = [obj._sim, obj._obj, obj._inventory_owner]
        if not sources.cause():
            extra["cause"] = {"actor": sources.adapter.event_reference(obj._sim), "basis": "carry_system_target._sim"}
    elif kind in ("buff_add", "buff_remove"):
        values = [obj.owner]
        if sources.local(values):
            before = buff_snapshot(sources, obj)
        extra["from_load"] = kwargs.get("from_load", False) or getattr(obj, "load_in_progress", False)
        buff_source = kwargs.get("buff_source")
        if buff_source is not None:
            extra["explicit_source"] = sources.scalar(buff_source)
            cause = sources.cause(interaction=buff_source if hasattr(buff_source, "sim") else None)
            if cause:
                extra["cause"] = cause
    elif kind in ("knowledge", "sentiment"):
        rel = obj._rel_data if kind == "knowledge" else obj.rel_data
        values = [sources.info(rel.sim_id_a), sources.info(rel.sim_id_b)]
    elif kind == "skill":
        values = [getattr(getattr(obj, "tracker", None), "owner", None)]
        if sources.local(values):
            extra["skill"] = sources.adapter.resource(obj, "statistic")
    elif kind in ("demotion", "retirement", "pregnancy", "death", "milestone", "local_context"):
        values = [obj._sim_info]
        if kind == "demotion":
            extra.update(career=sources.scalar(obj), track=sources.scalar(obj.current_track_tuning),
                         reason=sources.scalar(kwargs.get("reason")))
        elif kind == "pregnancy":
            values.extend(sources.info(v) for v in obj._parent_ids)
        elif kind == "milestone":
            milestone = kwargs.get("milestone")
            data = obj._active_milestones_data.get(milestone)
            before = enum_name(data.state) if data else None
            extra.update(milestone=milestone, milestone_data=data,
                         telemetry_context=sources.scalar(kwargs.get("telemetry_context")))
    elif kind in ("age", "household"):
        values = [obj]
    elif kind in ("inventory_insert", "inventory_remove", "inventory_split", "inventory_move"):
        if kind == "inventory_insert":
            item = kwargs.get("obj")
        else:
            identifier = kwargs.get("obj_id")
            item = kwargs.get("obj") or next((storage[identifier] for storage in (obj._storage, obj._hidden_storage) if identifier in storage), None)
        values = [getattr(obj, "owner", None), item]
        extra["item"] = item
        if sources.local(values):
            before = inventory_snapshot(sources, item)
            component = getattr(item, "inventoryitem_component", None)
            inventory = component.get_inventory() if component is not None else None
            values.append(getattr(inventory, "owner", None))
    elif kind == "money":
        values = [kwargs.get("sim")]
        if not any(values):
            values = next((f["values"] for f in reversed(sources.frames) if f["kind"] in
                           ("operation", "craft_payment_context", "payment_context")), [])
        extra["reason"] = sources.scalar(kwargs.get("reason"))
        extra["requested_amount"] = sources.scalar(kwargs.get("amount"))
        extra["payment_recipe"] = next((f["payment_recipe"] for f in reversed(sources.frames) if f.get("payment_recipe")), None)
    elif kind == "load_guard":
        extra["from_load"] = True
    elif kind in ("objective_context", "unlock_context"):
        values = [getattr(obj, "owner_sim_info", None) or getattr(obj, "_sim_info", None)]
        extra["identities"] = {key: sources.scalar(kwargs.get(key)) for key in
            ("objective_instance", "aspiration", "tuning_class", "name") if kwargs.get(key) is not None}
        if kwargs.get("aspiration") is not None:
            extra["identities"]["aspiration_type"] = enum_name(getattr(kwargs["aspiration"], "aspiration_type", None))
    if kind in TRANSITIONS and (kind not in ("knowledge", "sentiment", "skill") or sources.local(values)):
        before = TRANSITIONS[kind][1](sources, obj)
    extra.update(values=values, before=before)
    return extra


def capture_after(sources, frame, args, kwargs, result):
    kind, obj = frame["kind"], args[0]
    if not frame["local"]:
        return
    def same_snapshot(other):
        if other.get("owner_id") != id(obj):
            return False
        if kind in ("buff_add", "buff_remove"):
            return other["kind"] in ("buff_add", "buff_remove")
        if other["kind"] != kind:
            return False
        if kind == "milestone":
            return other.get("milestone") is frame.get("milestone")
        if kind.startswith("inventory_"):
            return other.get("item") is frame.get("item")
        return True
    if any(same_snapshot(other) for other in sources.frames[:-1]):
        return
    before = frame["before"]
    if kind in ("knowledge", "sentiment", "skill") and before is None:
        return
    category, snapshot = TRANSITIONS.get(kind, (None, None))
    after = snapshot(sources, obj) if snapshot else None
    payload = {}
    if kind in ("buff_add", "buff_remove"):
        if before is None:
            return
        after = buff_snapshot(sources, obj)
        references = sources.refs(frame["values"])
        for identifier in sorted(set(before) | set(after)):
            old, new = before.get(identifier), after.get(identifier)
            if old == new:
                continue
            metadata = {"details_before": old, "details_after": new,
                        "explicit_source": frame.get("explicit_source"), "classification": "unclassified"}
            if old is None or new is None:
                sources.recorder.change(references, "buffs", old["buff"] if old else None,
                    new["buff"] if new else None, sources.adapter.clock(), frame["source"],
                    metadata=metadata, cause=frame["cause"])
            else:
                sources.emit("buff.refreshed", frame["values"], {"before": old, "after": new},
                             frame["source"], cause=frame["cause"], roles=sources.roles(frame["values"], ("subject",), "buff_component.owner"), evidence="observed_handle_change")
        return
    if kind == "knowledge":
        changed = [key for key in after if after[key] != before[key]]
        if not changed:
            return
        payload["changed_fields"] = changed
        before, after = ({key: before[key] for key in changed}, {key: after[key] for key in changed})
    elif kind == "skill":
        payload["skill"] = frame["skill"]
    elif kind == "demotion":
        payload.update(career=frame["career"], track=frame["track"], reason=frame["reason"])
    elif kind == "pregnancy":
        frame["values"].extend(sources.info(v) for v in obj._parent_ids)
        payload["reason"] = "state_transition_only_not_inferred"
    elif kind == "milestone":
        data = frame["milestone_data"]
        after = enum_name(data.state) if data else None
        if after != "UNLOCKED" or before == after:
            return
        category = "life.milestone"
        origin = {"NEW_SIM": "initialization", "LOD_UP": "lod_recovery", "AGE_UP": "age_transition", "CHEAT": "cheat"}.get(
            frame["telemetry_context"], "observed_unlock")
        payload.update(milestone=sources.scalar(frame["milestone"]), telemetry_context=frame["telemetry_context"],
                       age_completed=sources.scalar(getattr(data, "age_completed", None)),
                       record_origin=origin, life_event_time="not_inferred_from_unlock_time")
    elif kind in ("inventory_insert", "inventory_remove", "inventory_split", "inventory_move"):
        if (kind != "inventory_split" and result is not True) or before is None:
            return
        after = inventory_snapshot(sources, frame["item"])
        category = "inventory.transfer"
        payload["operation"] = kind
        payload["item"] = sources.scalar(frame["item"])
        if kind == "inventory_split" and result is not None:
            frame["values"].append(result)
            payload["split_product"] = sources.scalar(result)
            payload["split_product_state"] = inventory_snapshot(sources, result)
    elif kind == "money":
        payload.update(actual_amount=after - before, reason=frame["reason"], requested_amount=frame["requested_amount"])
        if frame.get("payment_recipe"):
            payload["recipe"] = frame["payment_recipe"]
    if category and before != after:
        payload.update(before=before, after=after)
        roles = sources.roles(frame["values"], ("subject",), "event_source_owner")
        if category == "inventory.transfer":
            roles = sources.roles([frame["item"]], ("item",), "inventory_operation.item")
            if payload.get("split_product"):
                roles.append({"entity_key": payload["split_product"]["key"], "role": "split_product", "basis": "inventory_operation.result"})
            for snapshot, role in ((before, "source_container"), (after, "destination_container")):
                container = (snapshot or {}).get("container")
                if container:
                    roles.append({"entity_key": container["key"], "role": role, "basis": "inventory_snapshot"})
        if category in ("relationship.knowledge", "relationship.sentiment"):
            roles = sources.roles(frame["values"], ("subject", "target"), "relationship_data_direction")
        sources.emit(category, frame["values"], payload, frame["source"], roles=roles, cause=frame["cause"], evidence="observed_transition")
