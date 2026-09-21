"""Experimental offline semantic roles; never used by the runtime recorder."""

import hashlib
import json

from .filter_events import handles_only, identity


from .resources import resource_bytes

RESOURCE_BYTES = resource_bytes("experience_resources.json")
CATALOG = json.loads(RESOURCE_BYTES.decode("utf-8"))
RESOURCE_SHA256 = hashlib.sha256(RESOURCE_BYTES).hexdigest()
RESOURCES = {(kind, identifier, name): role for kind, identifier, name, role in CATALOG["resources"]}
ACTIVITY_RULES = {(r["id"], r["tuning_name"]): r for r in CATALOG.get("reviewed_activity_rules", [])}
RESOURCES.update({("interaction", identifier, name): row["role"]
                  for (identifier, name), row in ACTIVITY_RULES.items()})

USES = {
    "action": "core", "social_content": "core", "important_result": "core", "knowledge": "core",
    "feeling": "context", "mood": "context", "motivation_numeric": "context",
    "social_state": "context", "relationship_numeric": "context",
    "conversation": "merge", "activity_phase": "merge", "activity_support": "merge", "need_effect": "merge",
    "object_effect": "merge", "skill_progress": "merge", "activity_marker": "merge", "gesture": "merge",
    "environment": "background", "proximity": "background", "relationship_baseline": "background",
    "holiday_role": "background",
    "micro": "omit", "posture": "omit", "router": "omit", "broadcast": "omit",
    "reaction_callback": "omit", "outside_check": "omit", "mood_mirror": "omit",
    "cooldown": "omit", "timer": "omit", "need_modifier": "omit", "neutral_social": "omit",
    "goal_internal": "omit", "handle_refresh": "omit", "review_hidden": "review", "review_zero": "review",
    "review_other": "review", "unknown": "review", "external": "external",
}

# A parent reference can cross activity boundaries (e.g. cooking -> eating).
# Only these explicit families permit phase folding. Names must match RESOURCES.
FAMILIES = {}
for family, identifiers in {
    "cooking": "13388 13395 13276 13277 269311 13434 13269 13263 13285 13267 13261 13268 14335 13264 14333 14337 14338 13287",
    "eating": "13433 13377 13378 75242",
    "toilet": "14427 13443 40413 14426",
    "trash": "13360 29691",
    "pep_talk": "13642 13644 13646 98860 13643 13645",
    "calm_down": "13609 13610 13611 13612",
    "television": "128726 128727 14488 179829 9109 14543 273821 128871 30307",
    "art": "33623 9844",
    "nap": "14305 28880",
    "sleep": "13094 100082 100083 151428 96864 9230",
    "computer": "13240 31740 31743 31746 31650 31654 31662 31663 31664 31665 99858 31742 226202 381007 13188 13189",
}.items():
    FAMILIES.update({identifier: family for identifier in identifiers.split()})

IMPORTANT = {"skill.level", "crafting.completed", "payment.completed", "relationship.spouse",
             "relationship.sentiment", "trait.added", "trait.removed", "collection.acquired",
             "inventory.transfer", "progress.unlocked", "progress.item_unlocked"}


def kind(event):
    return event.get("category") or event.get("field") or event.get("event_type")


def game_event(event):
    return event.get("origin") == "game" and event.get("event_type") != "external_event"


def resource(event):
    category = kind(event) or ""
    payload = event.get("payload") or {}
    if category == "interaction":
        return event.get("facts", {})
    if category == "autonomy.decision":
        return (payload.get("selected") or {}).get("action") or {}
    if category == "statistic.direct":
        return payload.get("statistic") or {}
    if category in ("buffs", "relationship.bits"):
        return event.get("after") or event.get("before") or {}
    if category in ("trait.added", "trait.removed"):
        return payload.get("trait") or {}
    if category.startswith("aspiration."):
        return payload.get("aspiration") or {}
    return {}


def resource_role(category, value, game_version=None):
    if game_version is not None and game_version != CATALOG["game_version"]:
        return "unknown"
    identifier, name = identity(value)
    return RESOURCES.get((category, identifier, name), "unknown")


def classify(event, game_version=None):
    if not game_event(event):
        return "external"
    category = kind(event) or ""
    if category == "mood.changed":
        return "mood"
    if category == "relationship.knowledge":
        return "knowledge"
    if category == "buff.refreshed":
        return "handle_refresh" if handles_only(event) else "unknown"
    if category in ("trait.added", "trait.removed") or category.startswith("aspiration."):
        mapped = resource_role("trait" if category.startswith("trait.") else "aspiration", resource(event), game_version)
        if mapped != "unknown":
            return mapped
    if category in IMPORTANT or category.startswith(("life.", "career.")):
        return "important_result"
    if category in ("aspiration.goal_completed", "aspiration.stage_completed"):
        return "important_result" if (event.get("payload") or {}).get("aspiration_type") == "FULL_ASPIRATION" else "unknown"
    if category == "broadcast.effect":
        return "broadcast" if (event.get("payload") or {}).get("phase") == "remove_callback_returned" else "unknown"
    if category == "reaction.started":
        return "reaction_callback"  # Suppression additionally requires a valid cause in the assembler.
    role = resource_role("interaction" if category == "autonomy.decision" else category,
                         resource(event), game_version)
    if category == "autonomy.decision" and role == "activity_phase" and identity(resource(event))[0] in ("13388", "100082"):
        return "action"  # These selectors choose cooking/sleeping as an activity direction.
    return role


def family(event, game_version=None):
    value = resource(event)
    if resource_role("interaction", value, game_version) == "unknown":
        return None
    rule = ACTIVITY_RULES.get(identity(value), {})
    return rule.get("family") or FAMILIES.get(identity(value)[0])


def importance(event, game_version=None):
    if not game_event(event) or kind(event) != "interaction" or resource_role("interaction", resource(event), game_version) == "unknown":
        return None
    return ACTIVITY_RULES.get(identity(resource(event)), {}).get("importance")


def label(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("text") or value.get("fallback")
    return None


def compact(value):
    """Keep captured values while removing repeated resource localization metadata."""
    if isinstance(value, list):
        return [compact(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "resource_kind" in value or "tuning_name" in value:
        result = {k: v for k, v in {"id": value.get("id", value.get("tuning_id")),
                "kind": value.get("resource_kind"), "name": label(value.get("name")) or value.get("tuning_name")
                }.items() if v is not None}
        # A fallback tuning identifier is not a successfully localized name.
        # Keep the small quality/identity fields needed by every downstream renderer;
        # the full template, tokens and source remain in raw evidence.
        if value.get("tuning_name"):
            result["tuning_name"] = value["tuning_name"]
        if isinstance(value.get("name"), dict):
            result["name_status"] = value["name"].get("status")
            if value["name"].get("unresolved"):
                result["name_unresolved"] = True
        return result
    if "key" in value and "kind" in value:
        result = {"key": value["key"], "name": label(value.get("name"))}
        if isinstance(value.get("name"), dict):
            result["name_status"] = value["name"].get("status")
            if value["name"].get("unresolved"):
                result["name_unresolved"] = True
        return result
    if "text" in value and ("localization" in value or "status" in value):
        return {"text": value.get("text"), "status": value.get("status")}
    return {key: compact(item) for key, item in value.items() if key != "scope_evidence"}
