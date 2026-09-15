"""Read-only adapter for the verified EA Python interfaces.

Game objects are resolved and copied on the simulation thread only.
"""

from context_overlay.model import entity, field, number
from context_overlay.profiles import OBJECT_STATES, PROFILE_VERSION, resource_name
from context_overlay.localization import Localizer


NEEDS = {"hunger": "motive_Hunger", "energy": "motive_Energy", "fun": "motive_Fun",
         "social": "motive_Social", "hygiene": "motive_Hygiene", "bladder": "motive_Bladder"}
RELATIONSHIP_TRACKS = {"friendship": "LTR_Friendship_Main", "romance": "LTR_Romance_Main"}


def enum_name(value):
    if value is None:
        return None
    return str(getattr(value, "name", value)).rsplit(".", 1)[-1]


class EAAdapter:
    def __init__(self, config):
        import services
        import sims4.resources
        self.services = services
        self.config = config
        self.localizer = Localizer()
        manager = services.get_instance_manager(sims4.resources.Types.STATISTIC)
        self.statistics = {getattr(value, "__name__", ""): value for value in manager.types.values()}
        state_manager = services.get_instance_manager(sims4.resources.Types.OBJECT_STATE)
        self.state_profile_errors = [identifier for identifier, entry in OBJECT_STATES.items()
                                     if getattr(state_manager.get(int(identifier)), "__name__", None) != entry[0]]

    def clock(self):
        now = self.services.time_service().sim_now
        return {"ticks": str(now.absolute_ticks()), "display": str(now)}

    def scope(self):
        zone = self.services.current_zone()
        return {"kind": "active_lot_instantiated", "zone_id": str(zone.id),
                "lot_id": str(zone.lot.lot_id), "off_lot": "excluded"}

    def in_scope(self, obj):
        if obj is None or not getattr(obj, "id", 0):
            return False
        if getattr(obj, "_hidden_flags", 0):
            return False
        if not self.services.current_zone().is_zone_running:
            return False
        if getattr(obj, "is_sim", False):
            if obj.sim_info.get_sim_instance() is not obj:
                return False
        checker = getattr(obj, "is_on_active_lot", None)
        return bool(checker and checker())

    def live_objects(self):
        return list(self.services.object_manager().valid_objects())

    def local_sims(self):
        return [obj for obj in self.live_objects() if getattr(obj, "is_sim", False) and self.in_scope(obj)]

    def reference(self, obj):
        if getattr(obj, "is_sim", False):
            info = obj.sim_info
            return entity("sim", info.sim_id, " ".join(value for value in (info.first_name, info.last_name) if value))
        definition = getattr(obj, "definition", None)
        fallback = getattr(definition, "name", None) or type(obj).__name__
        try:
            from sims4.localization import LocalizationHelperTuning
            # Uses the actual object's localization token, including name
            # component overrides, instead of only its catalog definition.
            localized = LocalizationHelperTuning.get_object_name(obj)
            name = self.localizer.name(localized, str(fallback))
            name["source"] = {"kind": "runtime_object", "attribute": "LocalizationHelperTuning.get_object_name"}
        except Exception as exc:
            try:
                localized = getattr(obj, "custom_name", None)
                if not localized and definition is not None:
                    import build_buy
                    localized = build_buy.get_object_catalog_name(definition.id)
                name = self.localizer.name(localized, str(fallback))
            except Exception as fallback_exc:
                name = {"text": str(fallback), "status": "unmapped", "reason": "label_read_failed",
                        "error": str(fallback_exc)}
            name["source"] = {"kind": "catalog_fallback", "object_name_error": str(exc)}
        return entity("object", obj.id, name, getattr(definition, "id", None))

    def resource(self, resource, label_attribute=None, resource_kind=None, tokens=()):
        if resource is None:
            return None
        cls = resource if isinstance(resource, type) else type(resource)
        identifier = getattr(resource, "guid64", getattr(cls, "guid64", None))
        tuning_name = getattr(resource, "__name__", cls.__name__)
        attribute = label_attribute or {"buff": "buff_name", "relbit": "display_name",
                                       "statistic": "stat_name", "object_state": "display_name"}.get(resource_kind)
        try:
            localized = getattr(resource, attribute, None) if attribute else None
            if callable(localized):
                localized = localized(*tokens)
            name = self.localizer.name(localized, tuning_name)
        except Exception as exc:
            name = {"text": tuning_name, "status": "unmapped", "reason": "label_read_failed", "error": str(exc)}
        name["source"] = {"kind": "runtime_tuning", "attribute": attribute}
        name["visible"] = getattr(resource, "visible", None)
        return {"id": str(identifier) if identifier is not None else None,
                "resource_kind": resource_kind, "visible": getattr(resource, "visible", None),
                "tuning_name": tuning_name, "name": resource_name(identifier, tuning_name, name)}

    def object_for(self, target):
        if target["kind"] == "sim":
            info = self.services.sim_info_manager().get(int(target["id"]))
            return info.get_sim_instance() if info is not None else None
        return self.services.object_manager().get(int(target["id"]))

    def resolve(self, kind, identifier):
        if kind == "sim" and (identifier is None or str(identifier) in ("0", "active")):
            sim = self.services.get_active_sim()
            if sim is None:
                raise ValueError("No active Sim")
            return self.reference(sim)
        candidate = entity(kind, identifier)
        obj = self.object_for(candidate)
        return self.reference(obj) if obj is not None else candidate

    def interaction(self, interaction):
        from animation.animation_interaction import AnimationInteraction
        actor = interaction.sim
        target = interaction.target
        context = interaction.context
        localized = None
        name_error = None
        try:
            localized = interaction.get_name(target=target, context=context)
        except Exception as exc:
            name_error = str(exc)
        resolved_name = self.localizer.name(localized, type(interaction).__name__)
        resolved_name["source"] = {"kind": "runtime_interaction", "attribute": "get_name"}
        # If a queue/name wrapper fails, use the same tuned token provider as EA,
        # not assumed actor/target positions. Keep the failed read as evidence.
        if name_error:
            try:
                factory = getattr(interaction, "display_name_in_queue", None) or interaction.display_name
                tokens = interaction.get_localization_tokens(target=target, context=context)
                resolved_name = self.localizer.name(factory(*tokens) if factory else None, type(interaction).__name__)
                resolved_name["source"] = {"kind": "runtime_tuning_fallback", "attribute": "display_name_in_queue/display_name"}
            except Exception as fallback_exc:
                resolved_name["status"] = "unmapped"
                resolved_name["reason"] = "label_read_failed"
                resolved_name["fallback_error"] = str(fallback_exc)
            resolved_name["get_name_error"] = name_error
        resolved_name["visible"] = getattr(interaction, "visible", None)
        source = context.source
        name = enum_name(source)
        is_main = bool(interaction.is_super) and not isinstance(interaction, AnimationInteraction) and name not in ("POSTURE_GRAPH", "SOCIAL_ADJUSTMENT", "GET_COMFORTABLE",
                                                            "BODY_CANCEL_AOP", "CARRY_CANCEL_AOP", "VEHCILE_CANCEL_AOP")
        continuation = getattr(context, "continuation_id", None)
        source_id = getattr(context, "source_interaction_id", None)
        parent_id = source_id or continuation
        parent_actor = getattr(context, "source_interaction_sim_id", None) or actor.sim_info.sim_id
        return {"interaction_id": str(interaction.id), "tuning_id": str(interaction.guid64),
                "tuning_name": type(interaction).__name__,
                "name": resource_name(interaction.guid64, type(interaction).__name__, resolved_name),
                "visible": getattr(interaction, "visible", None),
                "actor": self.reference(actor),
                "target": self.reference(target) if target is not None and self.in_scope(target) else None,
                "target_scope": "in_scope" if target is not None and self.in_scope(target) else "unavailable_or_out_of_scope",
                "tier": "main" if is_main else "internal", "is_super": bool(interaction.is_super),
                "immediate": bool(interaction.immediate),
                "trigger": {"name": name, "value": int(source)},
                "finishing_type": enum_name(interaction.finishing_type),
                "pipeline_progress": enum_name(interaction.pipeline_progress),
                "parent_interaction_id": str(parent_id) if parent_id else None,
                "parent_actor_id": str(parent_actor) if parent_id else None,
                "parent_basis": "source_interaction_id" if source_id else ("continuation_id" if continuation else None)}

    def read(self, target, name):
        obj = self.object_for(target)
        if not self.in_scope(obj):
            return field(status="out_of_scope", reason="Only instantiated entities on the active lot are supported")
        try:
            return getattr(self, "read_" + name)(obj)
        except Exception as exc:
            return field(status="error", source="EAAdapter.read_" + name,
                         reason="{}: {}".format(type(exc).__name__, exc))

    def read_identity(self, obj):
        return field(self.reference(obj), source="SimInfo.sim_id / GameObject.id / Definition.id")

    def read_location(self, obj):
        position = obj.position
        return field(dict(self.scope(), position={"x": number(position.x), "y": number(position.y),
                                                 "z": number(position.z)}), source="GameObject.position; Zone.lot")

    def read_time(self, obj):
        return field(self.clock(), source="TimeService.sim_now")

    def read_interactions(self, obj):
        if not getattr(obj, "is_sim", False):
            return field(status="not_applicable", reason="Interaction queue belongs to a Sim")
        found = {}
        for item in obj.si_state:
            found[str(item.id)] = item
        for item in obj.queue:
            found[str(item.id)] = item
        running = getattr(obj.queue, "running", None)
        if running is not None:
            found[str(running.id)] = running
        found = {identifier: item for identifier, item in found.items()
                 if not getattr(item, "_context_overlay_tool", False)}
        if len(found) > self.config["max_interactions_per_sim"]:
            raise RuntimeError("Interaction count exceeds configured read budget")
        return field([self.interaction(item) for item in found.values()], source="Sim.si_state; InteractionQueue")

    def read_needs(self, obj):
        if not getattr(obj, "is_sim", False):
            return field(status="not_applicable", reason="Needs belong to a Sim")
        values = {}
        tracker = obj.sim_info.commodity_tracker
        for label, tuning_name in NEEDS.items():
            stat_type = self.statistics.get(tuning_name)
            if stat_type is None:
                values[label] = field(status="unsupported", reason="Statistic tuning not loaded: " + tuning_name)
                continue
            statistic = tracker.get_statistic(stat_type, add=False)
            if statistic is None:
                values[label] = field(status="not_present", reason="No instantiated statistic; no default substituted")
            else:
                values[label] = field({"value": number(statistic.get_value()), "unit": "game_statistic_units",
                                       "resource": self.resource(stat_type, resource_kind="statistic")}, source="BaseStatisticTracker.get_statistic(add=False)")
        return field(values, source="SimInfo.commodity_tracker")

    def read_buffs(self, obj):
        if not getattr(obj, "is_sim", False):
            return field(status="not_applicable", reason="Buffs belong to a Sim")
        buffs = list(obj.sim_info.Buffs)
        if len(buffs) > self.config["max_buffs_per_sim"]:
            raise RuntimeError("Buff count exceeds configured read budget")
        return field([self.resource(buff.buff_type, resource_kind="buff", tokens=(obj,)) for buff in buffs], source="SimInfo.Buffs.__iter__")

    def read_relationships(self, obj):
        if not getattr(obj, "is_sim", False):
            return field(status="not_applicable", reason="Relationships belong to a Sim")
        tracker = obj.sim_info.relationship_tracker
        values = []
        for other in self.local_sims():
            other_id = other.sim_info.sim_id
            if other is obj or not tracker.has_relationship(other_id):
                continue
            tracks = {}
            for label, tuning_name in RELATIONSHIP_TRACKS.items():
                stat_type = self.statistics.get(tuning_name)
                statistic = tracker.get_relationship_track(other_id, track=stat_type, add=False) if stat_type else None
                tracks[label] = field(number(statistic.get_value()), source=tuning_name) if statistic else field(
                    status="not_present", reason="No instantiated relationship track")
            bits = tracker.get_all_bits(other_id)
            values.append({"target": self.reference(other), "tracks": tracks,
                           "bits": [self.resource(bit, resource_kind="relbit", tokens=(obj, other)) for bit in bits]})
        return field(values, source="RelationshipTracker; existing local relationships only")

    @staticmethod
    def common_state(state):
        entry = OBJECT_STATES.get(str(getattr(state, "guid64", None)))
        return entry is not None and getattr(state, "__name__", None) == entry[0]

    def read_object_states(self, obj):
        if getattr(obj, "is_sim", False):
            return field(status="not_applicable", reason="Object state profile is for non-Sim objects")
        component = getattr(obj, "state_component", None)
        if component is None:
            return field(status="not_applicable", reason="Object has no state component")
        if self.state_profile_errors:
            return field(status="unsupported", reason="Object-state profile does not match loaded tuning: " + ",".join(self.state_profile_errors))
        values = [{"state": self.resource(value.state, resource_kind="object_state"),
                   "value": self.resource(value, resource_kind="object_state")}
                  for value in component.values() if self.common_state(value.state)]
        result = field(values, source="StateComponent.values; " + PROFILE_VERSION)
        result["profile"] = {"name": PROFILE_VERSION, "state_ids": sorted(OBJECT_STATES), "other_states": "excluded"}
        return result

    def continuous(self, obj):
        result = {}
        # This switch applies only to history sampling. Context still reads
        # current needs through read_needs when explicitly requested.
        if self.config.get("record_need_changes", False):
            needs = self.read_needs(obj)
            if needs["status"] == "available":
                for name, item in needs["value"].items():
                    if item["status"] == "available":
                        result["needs." + name] = item["value"]["value"]
        relationships = self.read_relationships(obj)
        for item in relationships["value"]:
            # These two main tracks are bidirectional; sample a pair only once.
            if int(obj.sim_info.sim_id) >= int(item["target"]["id"]):
                continue
            for name, track in item["tracks"].items():
                if track["status"] == "available":
                    result["relationship.{}.{}".format(item["target"]["id"], name)] = track["value"]
        return result
