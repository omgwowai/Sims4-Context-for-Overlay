"""Read-only adapter for the verified EA Python interfaces.

Game objects are resolved and copied on the simulation thread only.
"""

import math
import re

from context_overlay.model import entity, field, number
from context_overlay.profiles import OBJECT_STATES, PROFILE_VERSION, resource_name
from context_overlay.localization import Localizer
from context_overlay.event_policy import internal_interaction
from context_overlay.semantic_fields import RUNTIME_FIELDS


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

    def household_members(self):
        household = self.services.active_household()
        return [self.event_reference(info) for info in household.sim_info_gen()] if household is not None else []

    def nearby_objects(self):
        return self.services.object_manager().get_valid_objects_gen()

    def nearby_eligible(self, obj):
        if not self.in_scope(obj):
            return False
        # A child of an inventory object is not a separately placed world item.
        current, seen = obj, set()
        while current is not None:
            marker = id(current)
            if marker in seen or len(seen) >= 32:
                raise ValueError("Unresolved object parenting")
            seen.add(marker)
            if current.is_in_inventory():
                return False
            current = getattr(current, "parent", None)
        return True

    @staticmethod
    def nearby_spatial(obj):
        result = {"room": field(status="not_requested")}
        try:
            position = obj.position
            value = {axis: float(getattr(position, axis)) for axis in ("x", "y", "z")}
            if not all(math.isfinite(item) for item in value.values()):
                raise ValueError("Nonfinite position")
            result["position"] = field(value, source="GameObject.position (world transform)")
        except Exception:
            result["position"] = field(status="error", reason="position_read_failed")
        try:
            level = obj.level
            if not isinstance(level, int) or isinstance(level, bool):
                raise ValueError("No game level")
            result["level"] = field(level, source="GameObject/Sim.level")
        except Exception:
            result["level"] = field(status="unsupported", reason="level_unavailable")
        try:
            surface = obj.routing_surface
            value = {"primary_id": str(surface.primary_id), "secondary_id": int(surface.secondary_id),
                     "type": enum_name(surface.type)}
            result["routing_surface"] = field(value, source="GameObject/Sim.routing_surface")
        except Exception:
            result["routing_surface"] = field(status="unsupported", reason="routing_surface_unavailable")
        return result

    @staticmethod
    def nearby_room(obj, spatial):
        source = "build_buy.get_room_id(zone_id, position, level)"
        if any(spatial[key]["status"] != "available" for key in ("position", "level")):
            return field(status="unsupported", source=source, reason="position_or_level_unavailable")
        try:
            import build_buy
            import sims4.math
            position = spatial["position"]["value"]
            zone_id = obj.zone_id
            room_id = build_buy.get_room_id(zone_id,
                sims4.math.Vector3(position["x"], position["y"], position["z"]), spatial["level"]["value"])
            # Python only exposes the native entry. Zero/negative/None sentinel
            # semantics (including outdoor areas) have not been game-verified.
            if not isinstance(room_id, int) or isinstance(room_id, bool) or room_id <= 0:
                result = field(status="unsupported", source=source, reason="room_id_unverified")
                result["raw_id"] = str(room_id) if isinstance(room_id, int) else None
                return result
            return field({"zone_id": str(zone_id), "id": str(room_id)}, source=source)
        except Exception:
            return field(status="error", source=source, reason="room_read_failed")

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

    def event_reference(self, value):
        """Identity only: permits SimInfo and an event's out-of-scope participant."""
        if value is None:
            return None
        if hasattr(value, "sim_id"):
            return entity("sim", value.sim_id, " ".join(str(part) for part in
                (getattr(value, "first_name", ""), getattr(value, "last_name", "")) if part))
        if getattr(value, "id", None):
            # Services, broadcasters, definitions and interactions also have ids.
            # Only actual world objects belong in the entity index.
            from objects.base_object import BaseObject
            if isinstance(value, BaseObject):
                return self.reference(value)
        return None

    def event_local(self, value):
        if value is None:
            return False
        if hasattr(value, "get_sim_instance") and hasattr(value, "sim_id"):
            value = value.get_sim_instance()
        if self.in_scope(value):
            return True
        # Inventory operations can have a local owner even when the item is hidden.
        component = getattr(value, "inventoryitem_component", None)
        getter = getattr(component, "get_inventory", None)
        inventory = getter() if getter else None
        return self.in_scope(getattr(inventory, "owner", None))

    def _bind_buff_owner(self, detail, tokens):
        """Bind only token-zero Sim fields in an otherwise unbound Buff text.

        This is observed owner context, not a claim that the tuned proto already
        contained tokens. Keep that proto's snapshot alongside the binding.
        """
        evidence = detail.get("localization", {})
        gaps = detail.get("unresolved", [])
        if (len(tokens) != 1 or detail.get("status") != "unresolved_tokens" or not gaps
                or evidence.get("tokens") or evidence.get("error")
                or any(gap.get("reason") != "missing_token" or not re.fullmatch(
                    r"0\.Sim(?:FirstName|LastName|Name|FullName)|[MFmf]0\.[^{}]+", gap.get("expression", ""))
                       for gap in gaps)):
            return detail
        try:
            owner = tokens[0]
            owner_info = getattr(owner, "sim_info", owner)
            owner_id = getattr(owner_info, "sim_id", None)
            if (not isinstance(owner_id, int) or isinstance(owner_id, bool) or owner_id <= 0
                    or not callable(getattr(owner, "populate_localization_token", None))):
                return detail
            from sims4.localization import _create_localized_string
            bound = self.localizer.name(_create_localized_string(int(detail["hash"], 16), owner))
            captured = bound.get("localization", {}).get("tokens", [])
            if len(captured) != 1 or captured[0].get("type") != "SIM" or captured[0].get("error"):
                return detail
            bound["source"] = {"token_binding": {"basis": "buff_owner_at_read", "owner_id": str(owner_id),
                "token_index": 0, "unbound_localization": evidence}}
            return bound
        except Exception as exc:
            detail.setdefault("source", {})["token_binding_error"] = str(exc)
            return detail

    def interaction_name(self, interaction, source_kind="runtime_interaction"):
        """Read an action name, then its exact UI record if generic text fails."""
        target, context = getattr(interaction, "target", None), getattr(interaction, "context", None)
        fallback = getattr(interaction, "__name__", type(interaction).__name__)
        name_error = None
        try:
            localized = interaction.get_name(target=target, context=context)
        except Exception as exc:
            localized, name_error = None, str(exc)
        name = self.localizer.name(localized, fallback)
        name["source"] = {"kind": source_kind, "attribute": "get_name"}
        if name_error:
            try:
                factory = getattr(interaction, "display_name_in_queue", None) or interaction.display_name
                tokens = interaction.get_localization_tokens(target=target, context=context)
                name = self.localizer.name(factory(*tokens) if factory else None, fallback)
                name["source"] = {"kind": "runtime_tuning_fallback", "attribute": "display_name_in_queue/display_name"}
            except Exception as exc:
                name.update(status="unmapped", reason="label_read_failed", fallback_error=str(exc))
            name["get_name_error"] = name_error
        if name.get("status") not in ("resolved", "raw_text"):
            try:
                manager = getattr(getattr(interaction, "sim", None), "ui_manager", None)
                finder = getattr(manager, "_find_interaction", None)
                identifier = getattr(interaction, "id", None)
                info = finder(identifier)[0] if callable(finder) and identifier is not None else None
                if info is not None and info.interaction_id == identifier:
                    ref = getattr(info, "interaction_weakref", None)
                    if ref is None or callable(ref) and ref() is interaction:
                        candidate = self.localizer.name(info.display_name, fallback)
                        if candidate["status"] in ("resolved", "raw_text"):
                            candidate["source"] = {"kind": "runtime_ui_queue",
                                "attribute": "UIManager._find_interaction.display_name", "interaction_id": str(identifier),
                                "fallback_from": name}
                            name = candidate
            except Exception as exc:
                name["source"]["ui_name_read_error"] = str(exc)
        name["visible"] = getattr(interaction, "visible", None)
        return name

    def resource(self, resource, resource_kind=None, tokens=(), intensity=None):
        if resource is None:
            return None
        cls = resource if isinstance(resource, type) else type(resource)
        identifier = getattr(resource, "guid64", getattr(cls, "guid64", None))
        tuning_name = getattr(resource, "__name__", cls.__name__)
        if resource_kind is None:
            # These are verified EA resource fields, not English-name guesses.
            for kind, marker in (("buff", "buff_name"), ("statistic", "stat_name"),
                                 ("mood", "mood_names"), ("recipe", "get_recipe_name"),
                                 ("trait", "trait_type")):
                if hasattr(resource, marker):
                    resource_kind = kind
                    break
        fields = RUNTIME_FIELDS.get(resource_kind, {})
        attribute = fields.get("name")
        if resource_kind is None and hasattr(resource, "display_name"):
            attribute = "display_name"
        interaction_label = resource_kind == "interaction" and attribute == "get_name"
        try:
            localized = getattr(resource, attribute, None) if attribute else None
            if resource_kind == "mood" and localized is not None:
                level = intensity if intensity is not None else 0
                localized = localized[level] if isinstance(level, int) and 0 <= level < len(localized) else None
                if localized is not None and hasattr(localized, "hash") and tokens:
                    from sims4.localization import _create_localized_string
                    localized = _create_localized_string(localized.hash, *tokens)
            elif interaction_label:
                pass  # Shared path below also reads the actual UI queue name.
            elif callable(localized):
                localized = localized(*tokens)
            name = self.interaction_name(resource, "runtime_tuning") if interaction_label else self.localizer.name(localized, tuning_name)
            if attribute is None:
                name.update(status="unmapped", reason="no_verified_name_accessor")
        except Exception as exc:
            name = {"text": tuning_name, "status": "unmapped", "reason": "label_read_failed", "error": str(exc)}
        if not interaction_label or "source" not in name:
            name["source"] = {"kind": "runtime_tuning", "attribute": attribute}
        if resource_kind == "mood":
            name["source"].update(intensity=intensity, name_basis="observed_intensity" if intensity is not None else "base_mood_name")
        name["visible"] = getattr(resource, "visible", None)
        result = {"id": str(identifier) if identifier is not None else None,
                  "resource_kind": resource_kind, "visible": getattr(resource, "visible", None),
                  "tuning_name": tuning_name, "name": resource_name(identifier, tuning_name, name)}
        for role, detail_attribute in fields.items():
            if role == "name":
                continue
            try:
                if not hasattr(resource, detail_attribute):
                    continue  # A subclass may not provide this optional interface.
                localized = getattr(resource, detail_attribute)
                if resource_kind == "mood":
                    # Mood descriptions may be client-only. Read only an
                    # exposed base variant with a known observed intensity;
                    # never guess the client's age/trait override selection.
                    if type(intensity) is not int or not 0 <= intensity < len(localized):
                        result[role] = {"text": None, "status": "unmapped", "reason": "description_variant_not_selected",
                            "source": {"kind": "runtime_tuning", "attribute": detail_attribute, "role": role}}
                        continue
                    localized = localized[intensity]
                # Explicit tokens remain authoritative. Only the known unbound
                # Buff owner forms receive separately identified owner context.
                detail_factory = callable(localized)
                if detail_factory:
                    localized = localized(*tokens)
                detail = self.localizer.name(localized)
                if resource_kind == "buff" and role == "description" and not detail_factory:
                    detail = self._bind_buff_owner(detail, tokens)
                if detail.get("status") == "no_display_name":
                    detail.update(status="not_present", reason="no_localized_string_key")
            except Exception as exc:
                detail = {"text": None, "status": "unmapped", "reason": "detail_read_failed", "error": str(exc)}
            detail.setdefault("source", {}).update(kind="runtime_tuning", attribute=detail_attribute, role=role)
            if resource_kind == "mood":
                detail["source"].update(intensity=intensity, variant_basis="base_description_at_observed_intensity",
                                        client_overrides_evaluated=False)
            if role == "tooltip":
                detail["source"]["condition_evaluated"] = False
            result[role] = detail
        return result

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
        resolved_name = self.interaction_name(interaction)
        source = context.source
        name = enum_name(source)
        is_main = not internal_interaction(interaction.guid64, type(interaction).__name__) and not isinstance(interaction, AnimationInteraction) and name not in ("POSTURE_GRAPH", "SOCIAL_ADJUSTMENT", "GET_COMFORTABLE",
                                                            "BODY_CANCEL_AOP", "CARRY_CANCEL_AOP", "VEHCILE_CANCEL_AOP")
        continuation = getattr(context, "continuation_id", None)
        source_id = getattr(context, "source_interaction_id", None)
        parent_id = source_id or continuation
        parent_actor = getattr(context, "source_interaction_sim_id", None) or actor.sim_info.sim_id
        participants = [self.reference(actor)]
        roles = [{"entity_key": participants[0]["key"], "role": "actor", "basis": "interaction.sim"}]
        if target is not None and getattr(target, "id", None):
            reference = self.event_reference(target)
            if reference:
                participants.append(reference)
                roles.append({"entity_key": reference["key"], "role": "target", "basis": "interaction.target"})
        from interactions import ParticipantType
        for participant in interaction.get_participants(ParticipantType.AllSims):
            reference = self.event_reference(participant)
            if reference and reference["key"] not in {item["key"] for item in participants}:
                participants.append(reference)
                roles.append({"entity_key": reference["key"], "role": "participant", "basis": "ParticipantType.AllSims"})
        if len(participants) > self.config["max_entities"]:
            raise RuntimeError("Interaction participants exceed entity budget")
        return {"interaction_id": str(interaction.id), "tuning_id": str(interaction.guid64),
                "tuning_name": type(interaction).__name__,
                "name": resource_name(interaction.guid64, type(interaction).__name__, resolved_name),
                "visible": getattr(interaction, "visible", None),
                "actor": self.reference(actor),
                "target": self.event_reference(target), "participants": participants, "roles": roles,
                "outcome_result": enum_name(getattr(interaction, "global_outcome_result", None)),
                "classification": "gameplay_or_unclassified" if is_main else "technical_interaction_source",
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
        identity = self.reference(obj)
        if not getattr(obj, "is_sim", False):
            try:
                from sims4.localization import LocalizationHelperTuning
                description = self.localizer.name(LocalizationHelperTuning.get_object_description(obj))
                if description.get("status") == "no_display_name":
                    description.update(status="not_present", reason="no_localized_string_key")
            except Exception as exc:
                description = {"text": None, "status": "unmapped", "reason": "detail_read_failed", "error": str(exc)}
            description["source"] = {"kind": "runtime_object", "attribute": "LocalizationHelperTuning.get_object_description"}
            identity["description"] = description
        return field(identity, source="SimInfo.sim_id / GameObject.id / Definition.id")

    def read_location(self, obj):
        position = obj.position
        return field(dict(self.scope(), position={"x": number(position.x), "y": number(position.y),
                                                 "z": number(position.z)}), source="GameObject.position; Zone.lot")

    def read_time(self, obj):
        return field(self.clock(), source="TimeService.sim_now")

    def read_interactions(self, obj):
        if not getattr(obj, "is_sim", False):
            return field(status="not_applicable", reason="Interaction queue belongs to a Sim")
        return field([self.interaction(item) for item in self.interaction_objects(obj)], source="Sim.si_state; InteractionQueue")

    def interaction_objects(self, obj):
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
        return list(found.values())

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
