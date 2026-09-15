"""Narrow event policies backed by observed tuning and explicit user scope."""

import re


def suppressed_statistic(tuning_name):
    """Elapsed-time bookkeeping is neither a life event nor a retained effect.

    Apply only to statistic.direct, never to the current Context or to actions.
    Other unknown statistics remain observable. No sampling/filter switch.
    """
    return bool(re.search(r"(?:^|_)TimeSince[A-Z_]", tuning_name or ""))


INTERNAL_INTERACTIONS = {
    "13377": "Food_Eat_Active",
    "13378": "Food_Eat_Passive",
    # BASE tuning: hidden one-shot / looping animation beneath motion gaming.
    "13744": "MotionGameRig_Family_Active",
    "13745": "MotionGameRig_Family_Passive",
}


def internal_interaction(identifier, tuning_name):
    return INTERNAL_INTERACTIONS.get(str(identifier)) == tuning_name


def completion_tier(payload):
    if payload.get("aspiration_type") in ("WHIM_SET", "NOTIFICATION"):
        return "internal"
    aspiration = payload.get("aspiration") or {}
    if (aspiration.get("tuning_name") or "").startswith("aspiration_Utility_"):
        return "internal"
    return "main"
