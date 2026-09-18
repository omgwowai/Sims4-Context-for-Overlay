"""Experimental diary-detail filter for a stable, single-session journal.

This is an offline view, not a recording policy or an experience summarizer.
Unrecognized events survive; every omission has a revision reference and reason.
"""

from collections import Counter, defaultdict
import copy
import json



POLICY_VERSION = "diary_detail_v1"
TICKS_PER_MINUTE = 1500
# Exact identities observed in game 1.126.73.1030. Names alone, visibility,
# internal tier and SubActionAutonomy are never sufficient to discard an event.
MICRO_ACTIONS = {
    "31664": "Computer_Use_PlayGame_Mild",
    "31665": "Computer_Use_PlayGame_Intense",
    "31654": "Computer_Use_Troll_Forums",
    "31650": "Computer_Use_Mouse",
    "99858": "Computer_Use_React",
    "31662": "Computer_Use_React_Positive",
    "31663": "Computer_Use_React_Negative",
    "96864": "sleep_Passive",
    "9230": "SleepMixer_Dream",
}
PROVIDER_ACTIONS = {
    "13240": "computer_TrollForum",
    "31740": "computer_PlayGame_SimsForeverRenamed",
    "31746": "computer_PlayGame_Hillock",
    "31743": "computer_PlayGame_REFUGE",
    "13094": "bed_sleep",
}
ROUTERS = {
    "30917": "SocialPickerSI",
    "502981": "aggregate_SocialObservation_Chat",
    "502983": "autonomousSimPicker_SocialObservation_FamiliarChat",
    "270747": "computer_Recreation_Autonomous_PseudoAggregate_PlayGame",
    "178209": "autonomous_ObjectPicker_Art_View",
}
ZERO_STEP_TRIGGERS = {
    ("13983", "sim-stand"): {"POSTURE_GRAPH", "BODY_CANCEL_AOP"},
    ("97431", "social_adjustment"): {"SOCIAL_ADJUSTMENT"},
}
RULES = {
    "zero_duration_technical_step": "已知技术交互没有观测到持续执行，且没有关联结果；不单独叙述。",
    "router_with_observed_continuation": "零时长的已知选择器，有同人物同地块访问的已开始后续交互；保留后续交互。",
    "micro_action_with_recorded_provider": "已知电脑/睡眠小动作正常结束，决策记录能追到实际运行的上层活动。",
    "routine_micro_decision": "同一上层活动中重复的常规小动作选择，完整末级候选均为已知小动作；保留该活动首条决策样本。",
    "buff_handles_only": "Buff 前后除内部 handles 外完全相同；不把这次维护写成新的状态经历。",
    "repeated_lazy_topup": "同一次午睡内重复的微量补值；保留该段首条相同补值和次数、时间范围。",
}


def identity(resource):
    return str(resource.get("tuning_id", resource.get("id"))), resource.get("tuning_name")


def matches(resource, known):
    identifier, name = identity(resource)
    return identifier in known and known[identifier] == name


def tick(value):
    try:
        return int(value["ticks"])
    except (KeyError, TypeError, ValueError):
        return None


def duration(event):
    start, end = tick(event.get("started_time")), tick(event.get("ended_time"))
    return end - start if start is not None and end is not None else None


def ref(event):
    return {"event_id": event["event_id"], "revision": event["revision"]}


def actor(event):
    return (event.get("facts", {}).get("actor") or {}).get("key")


def target(event):
    return (event.get("facts", {}).get("target") or {}).get("key")


def same_actor_visit(left, right):
    return (actor(left) is not None and actor(left) == actor(right)
            and left.get("zone_visit") is not None and left["zone_visit"] == right.get("zone_visit"))


def compact_size(events):
    # Per-event encoding avoids one long C-encoder section holding the GIL over
    # an entire journal. Exact byte count is unchanged.
    if isinstance(events, list):
        return 2 + max(0, len(events) - 1) + sum(compact_size(event) for event in events)
    return len(json.dumps(events, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))


def normal_end(event, results=(None, "NONE", "SUCCESS")):
    facts = event.get("facts", {})
    return (event.get("outcome") == "completed" and event.get("stage") == "ended"
            and facts.get("finishing_type") == "NATURAL" and facts.get("outcome_result") in results)


def routine_decision(event):
    payload = event.get("payload", {})
    execution = payload.get("execution_result") or {}
    stages = [s for s in payload.get("stages", []) if s.get("kind") == "interaction"]
    return (payload.get("mode") == "SubActionAutonomy" and payload.get("context_source") == "AUTONOMY"
            and payload.get("is_script_request") is False and payload.get("mixer_rejected_attempts") == 0
            and payload.get("retention_gate") == "queue_success" and execution.get("returned_success") is True
            and execution.get("exception_type") is None and len(stages) == 1
            and stages[0].get("omitted_count") == 0 and bool(stages[0].get("candidates"))
            and all(c.get("action", {}).get("resource_kind") == "interaction"
                    and matches(c["action"], MICRO_ACTIONS) for c in stages[0]["candidates"]))


def micro_provider(event, decision, instances):
    """Use the recorded mixer-provider instance, never infer a parent by time."""
    facts, payload = event.get("facts", {}), decision.get("payload", {})
    if not (matches(facts, MICRO_ACTIONS) and facts.get("visible") is False
            and normal_end(event, (None, "NONE")) and duration(event) is not None
            and 0 <= duration(event) <= 15 * TICKS_PER_MINUTE
            and actor(event) is not None and event.get("zone_visit") is not None
            and decision.get("event_type") == "game_event" and decision.get("origin") == "game"
            and decision.get("category") == "autonomy.decision"
            and decision.get("zone_visit") == event.get("zone_visit")
            and payload.get("interaction_event_id") == event["event_id"]
            and (payload.get("actor") or {}).get("key") == actor(event)
            and identity((payload.get("selected") or {}).get("action", {})) == identity(facts)):
        return None
    providers = [c for s in payload.get("stages", []) if s.get("kind") == "mixer_provider"
                 for c in s.get("candidates", []) if c.get("selected") is True]
    if len(providers) != 1:
        return None
    selected = providers[0]
    key = (event.get("zone_visit"), actor(event), selected.get("interaction_id"))
    choices = instances.get(key, [])
    if len(choices) != 1:
        return None
    provider = choices[0]
    start = tick(provider.get("started_time"))
    end = tick(provider.get("ended_time"))
    if (not matches(provider.get("facts", {}), PROVIDER_ACTIONS)
            or identity(provider["facts"]) != identity(selected.get("action", {}))
            or start is None or start > tick(event["started_time"])
            or (end is not None and end < tick(event["ended_time"]))
            or target(event) is None or target(event) != target(provider)
            or target(provider) != (selected.get("target") or {}).get("key")):
        return None
    return provider


def handles_only(event):
    if event.get("event_type") != "game_event" or event.get("category") != "buff.refreshed":
        return False
    payload = event.get("payload", {})
    before, after = payload.get("before"), payload.get("after")
    return (set(payload) <= {"before", "after", "scope_evidence"}
            and isinstance(before, dict) and isinstance(after, dict)
            and isinstance(before.get("buff"), dict) and bool(before["buff"].get("id"))
            and isinstance(before.get("handles"), list) and bool(before["handles"])
            and isinstance(after.get("handles"), list) and bool(after["handles"])
            and before["handles"] != after["handles"]
            and {k: v for k, v in before.items() if k != "handles"}
            == {k: v for k, v in after.items() if k != "handles"})


def lazy_topup_key(event, by_id):
    if (event.get("origin") != "game" or event.get("event_type") != "game_event"
            or event.get("category") != "statistic.direct"):
        return None
    payload, cause = event.get("payload", {}), event.get("cause") or {}
    stat = payload.get("statistic") or {}
    before, after = payload.get("before"), payload.get("after")
    parent = by_id.get(cause.get("event_id"), {})
    subjects = [r.get("entity_key") for r in event.get("roles", []) if r.get("role") == "subject"]
    at = tick(event.get("first_observed_time"))
    start, end = tick(parent.get("started_time")), tick(parent.get("ended_time"))
    if (set(payload) - {"statistic", "before", "after", "scope_evidence"}
            or identity(stat) != ("29111", "commodity_Trait_Autonomy_Lazy") or stat.get("visible") is not False
            or type(before) not in (int, float) or type(after) not in (int, float)
            or not (99 <= before < after == 100) or (cause.get("operation") or {}).get("type") != "StatisticSetMaxOp"
            or parent.get("origin") != "game" or parent.get("event_type") != "interaction"
            or identity(parent.get("facts", {})) != ("14305", "sofa_Nap")
            or len(subjects) != 1 or subjects[0] != actor(parent)
            or event.get("zone_visit") is None or event["zone_visit"] != parent.get("zone_visit")
            or at is None or start is None or end is None or not start <= at <= end):
        return None
    return (event["zone_visit"], subjects[0], parent["event_id"], before, after)


def filter_events(events, session_id, entity_key=None, copy_result=True):
    """Filter flat latest revisions; all source events remain available for links."""
    by_id, instances, children, protected = {}, defaultdict(list), defaultdict(list), set()
    for event in events:
        identifier = event.get("event_id")
        if (not isinstance(identifier, str) or not identifier.startswith(session_id + ":")
                or identifier in by_id or type(event.get("revision")) is not int or event["revision"] < 1
                or "effects" in event):
            raise ValueError("Expected unique flat latest revisions from one session")
        by_id[identifier] = event
        # External payloads are opaque, even if they resemble game data.
        if event.get("origin") != "game" or event.get("event_type") == "external_event":
            continue
        if event.get("event_type") == "interaction":
            instances[(event.get("zone_visit"), actor(event), event["facts"].get("interaction_id"))].append(event)
            children[event["facts"].get("parent_event_id")].append(event)
        if event.get("category") != "autonomy.decision":
            protected.add((event.get("cause") or {}).get("event_id"))

    omitted, folds = {}, {}

    def omit(event, rule, related=(), **details):
        omitted[event["event_id"]] = dict(ref(event), rule=rule,
            related=[ref(item) for item in related], **details)

    for event in events:
        if (event.get("origin") != "game" or event.get("event_type") == "external_event"
                or event["event_id"] in protected):
            continue
        if handles_only(event):
            omit(event, "buff_handles_only")
        if event.get("event_type") != "interaction":
            continue
        facts = event.get("facts", {})
        resource = identity(facts)
        result = facts.get("outcome_result")
        technical = (event.get("tier") == "internal" and facts.get("classification") == "technical_interaction_source"
                     and facts.get("visible") is False and result in (None, "NONE") and event.get("stage") == "ended")
        animation = (resource == ("0", "AnimationInteraction") and not event.get("started_time")
                     and tick(event.get("first_observed_time")) is not None
                     and tick(event["first_observed_time"]) == tick(event.get("ended_time"))
                     and facts.get("finishing_type") == "AUTO_EXIT")
        zero_step = (resource in ZERO_STEP_TRIGGERS and duration(event) == 0
                     and (facts.get("trigger") or {}).get("name") in ZERO_STEP_TRIGGERS[resource]
                     and facts.get("finishing_type") in ("NATURAL", "DISPLACED", "INTERACTION_INCOMPATIBILITY"))
        if technical and (animation or zero_step):
            omit(event, "zero_duration_technical_step")
            continue
        continuations = [child for child in children[event["event_id"]]
                         if same_actor_visit(event, child) and tick(child.get("started_time")) is not None]
        if matches(facts, ROUTERS) and duration(event) == 0 and normal_end(event) and continuations:
            omit(event, "router_with_observed_continuation", continuations, link_basis="recorded_parent_event_id")
            continue
        decision = by_id.get(facts.get("decision_event_id"), {})
        provider = micro_provider(event, decision, instances)
        if provider:
            omit(event, "micro_action_with_recorded_provider", [provider, decision],
                 link_basis="recorded_mixer_provider_instance_not_parent_event_id")

    first_provider_decision = {}
    for event in sorted(events, key=lambda e: (tick(e.get("first_observed_time")) or 0, e["event_id"])):
        if event.get("origin") != "game" or event.get("event_type") != "game_event":
            continue
        if event.get("category") == "autonomy.decision" and event["event_id"] not in protected:
            interaction_id = event.get("payload", {}).get("interaction_event_id")
            detail = omitted.get(interaction_id, {})
            if (detail.get("rule") == "micro_action_with_recorded_provider"
                    and by_id[interaction_id]["facts"].get("decision_event_id") == event["event_id"]):
                provider_id = detail["related"][0]["event_id"]
                representative = first_provider_decision.setdefault(provider_id, event)
                if representative is not event and routine_decision(event):
                    omit(event, "routine_micro_decision", [by_id[interaction_id], representative],
                         scope="routine_mixer_choice_only_provider_scores_remain_in_source")

    # Only identical small refills within one actual nap are folded. The large
    # initial transition and the first small refill remain separate raw events.
    previous = {}
    for event in sorted(events, key=lambda e: (tick(e.get("first_observed_time")) or 0, e["event_id"])):
        if (event.get("origin") != "game" or event.get("event_type") != "game_event"
                or event.get("category") != "statistic.direct"
                or str((event.get("payload", {}).get("statistic") or {}).get("id")) != "29111"):
            continue
        subjects = tuple(r.get("entity_key") for r in event.get("roles", []) if r.get("role") == "subject")
        series = (event.get("zone_visit"), subjects)
        key = lazy_topup_key(event, by_id)
        if key is None or event["event_id"] in protected:
            previous.pop(series, None)
            continue
        at = tick(event["first_observed_time"])
        prior = previous.get(series)
        if prior and prior["key"] == key and 0 < at - prior["last_tick"] <= 2 * TICKS_PER_MINUTE:
            representative = prior["representative"]
            omit(event, "repeated_lazy_topup", [representative, by_id[key[2]]],
                 representative=ref(representative))
            group = folds.setdefault(representative["event_id"], {
                "rule": "repeated_lazy_topup", "representative": ref(representative),
                "cause": ref(by_id[key[2]]), "observed_count": 1, "omitted_count": 0,
                "first_time": representative["first_observed_time"],
                "before_each": key[3], "after_each": key[4], "not_a_net_statistic_delta": True})
            group.update(observed_count=group["observed_count"] + 1, omitted_count=group["omitted_count"] + 1,
                         last_time=event["first_observed_time"])
        else:
            previous[series] = {"representative": event, "key": key}
        previous[series]["last_tick"] = at

    selected = [e for e in events if entity_key is None or entity_key in e.get("entities", [])]
    selected_ids = {e["event_id"] for e in selected}
    kept = [e for e in selected if e["event_id"] not in omitted]
    audit = [omitted[e["event_id"]] for e in selected if e["event_id"] in omitted]
    counts = dict(sorted(Counter(row["rule"] for row in audit).items()))
    metrics = {"source_latest_events": len(events), "selected_latest_events": len(selected),
               "retained_events": len(kept), "omitted_events": len(audit), "omitted_by_rule": counts,
               "selected_events_json_bytes": compact_size(selected), "retained_events_json_bytes": compact_size(kept)}
    result = {"kind": "history", "session_id": session_id,
        "history": {"events": kept, "scope": "experimental_filtered_latest_revisions"},
        "filtering": {"policy_version": POLICY_VERSION, "experimental": True, "entity_key": entity_key,
            "rules": RULES, "metrics": metrics, "omitted": audit,
            "folds": [f for key, f in folds.items() if key in selected_ids],
            "limitations": ["Not an experience summary or verified player perception.",
                "Unrecognized, long-running technical, failed and open activities are retained.",
                "Omitted evidence and decision scores require the original journal; this output is not lossless.",
                "Entity membership is an index, not evidence of observation or knowledge."]}}
    return copy.deepcopy(result) if copy_result else result
