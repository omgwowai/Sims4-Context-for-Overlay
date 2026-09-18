"""Deterministic compact presentation of organized evidence, without story inference."""

import json
import re


# Exact identities checked against installed tuning; raw localization remains visible.
ACTION_LABELS = {
    ("381007", "computer_Research_Thanatology"): "研究死亡学",
    ("13471", "guitar_Practice"): "练习吉他",
}


def name(value):
    if isinstance(value, dict):
        return value.get("name") or value.get("text") or value.get("key") or value.get("id")
    return value


def at(value):
    if not value:
        return None
    display = value.get("display")
    match = re.match(r"(\d\d:\d\d):[^ ]+ (day:\d+) (week:\d+)", display or "")
    return "{} {} {}".format(match[3], match[2], match[1]) if match else display or "tick:" + value["ticks"]


def people(roles):
    return [[row["role"], row["entity_key"]] for row in roles]


def scores(value, native=True):
    contributions = value.get("nonzero_commodity_contributions", [])
    result = {"contributions": [{k: row[k] for k in ("commodity", "score", "weighted_score", "autonomy_weight", "modified_desire")
                                  if k in row} for row in contributions]}
    for row in result["contributions"]:
        row["commodity"] = name(row.get("commodity"))
    if value.get("view_omitted_contributions"):
        result["omitted_contributions"] = value["view_omitted_contributions"]
    # Structured contributions carry the usable scoring evidence. Keep repeated native
    # dumps in organized/audit; retain the excerpt here only when it is the only score.
    if native and value.get("native_text") and not contributions:
        result["native_scoring_excerpt"] = value["native_text"]
        result["excerpt_truncated"] = value["native_text_truncated"]
    elif native and value.get("native_text"):
        result["native_scoring_in_details"] = True
    if value.get("missing"):
        result["source_missing"] = value["missing"]
    return result


def choice(value):
    def candidate(row, selected):
        result = {"action": name(row.get("action")), "target": row.get("target"), "probability": row.get("probability")}
        score = scores(row.get("score") or {}, native=selected)
        if score.get("contributions") or score.get("native_scoring_excerpt"):
            result["scoring"] = score
        return result
    return {"kind": value["kind"], "selected": candidate(value["selected"], True),
            "alternatives": [candidate(row, False) for row in value["alternatives"]],
            "pool_count": value["pool_count"], "probability_basis": value["probability_basis"],
            "source_omitted": value["source_omitted_count"], "view_omitted": value["view_omitted_candidates"]}


def fact(unit):
    result = {"id": unit["id"], "type": unit["type"], "roles": people(unit["roles"]), "evidence": unit["evidence"]}
    if "observations" in unit:
        result.update(statistic=name(unit["statistic"]), observed_changes=[
            [at(row["time"]), row["before"], row["after"]] for row in unit["observations"]])
    else:
        result.update(time=at(unit["time"]), value=unit.get("payload"))
        if unit.get("before") is not None or unit.get("after") is not None:
            result.update(before=unit.get("before"), after=unit.get("after"))
    if unit.get("unresolved_cause_event_id"):
        result["cause_unresolved"] = True
    return result


def decision(unit):
    result = {"id": unit["id"], "type": unit["category"], "time": at(unit["time"]), "evidence": unit["evidence"]}
    if unit["category"] == "activity_provider_scoring":
        result.update(observed_count=unit["observed_count"], sample_policy=unit["sample_policy"],
                      samples=[{"time": at(row["time"]), "choice": choice(row["choice"])} for row in unit["samples"]])
    else:
        result.update(actor=unit["actor"], is_script_request=unit["is_script_request"], execution=unit["execution"],
                      selected_step_started=unit["selected_step_started"],
                      selected=unit["selected"], choices=[choice(row) for row in unit["choices"]],
                      submission_result=unit["submission_result"])
    return result


def digest(organized):
    facts = {unit["id"]: unit for unit in organized["facts"]}
    decisions = {unit["id"]: unit for unit in organized["decisions"]}
    embedded_facts, embedded_decisions = set(), set()
    result = {"scope": dict(organized["scope"], times="minute_precision_for_presentation",
        numeric_changes="observed_local_transitions_not_net_daily_change",
        scoring="engine_evidence_not_private_thought_or_causal_percentage",
        activity_end="observed_execution_envelope_not_continuous_attention_or_gameplay_success"),
        "activities": [], "state_timelines": [], "background": [], "standalone_facts": [], "unassigned_decisions": []}
    for unit in organized["activities"]:
        row = {"id": unit["id"], "action": name(unit["action"]), "roles": people(unit["roles"]),
               "action_tuning": unit.get("action_tuning"),
               "time": [at(unit["started"] or unit["time"]), at(unit["ended"])], "execution": unit["execution"],
               "root_exit": unit["exit"], "root_result_branch": unit["result_branch"],
               "evidence": unit["evidence"], "results": [], "decisions": []}
        reviewed_label = ACTION_LABELS.get((unit["action"].get("id"), unit.get("action_tuning")))
        if reviewed_label and reviewed_label != row["action"]:
            row.update(action=reviewed_label, observed_action_name=row["action"],
                       action_label_basis="reviewed_exact_tuning_identity")
        for topic in unit["topics"]:
            row.setdefault("topics", []).append({"action": name(topic["action"]), "roles": people(topic["roles"]),
                "action_tuning": topic.get("action_tuning"),
                "result_branch": topic["result_branch"], "time": [at(topic["started"]), at(topic["ended"])], "evidence": topic["evidence"]})
        for identifier in unit["effects"]:
            if identifier in facts:
                row["results"].append(fact(facts[identifier]))
                embedded_facts.add(identifier)
        for identifier in unit["decisions"]:
            if identifier in decisions and identifier not in embedded_decisions:
                row["decisions"].append(decision(decisions[identifier]))
                embedded_decisions.add(identifier)
            elif identifier in decisions:
                row.setdefault("shared_decision_refs", []).append(identifier)
        if unit["product_links"]:
            row["product_links"] = unit["product_links"]
        result["activities"].append(row)

    groups = {}
    for unit in organized["states"]:
        key = (unit["subject"], unit["category"], unit["zone_visit"])
        group = groups.setdefault(key, {"subject": unit["subject"], "category": unit["category"], "zone_visit": unit["zone_visit"], "intervals": []})
        state = unit["state"]
        interval = {"state": name(state.get("mood")) if unit["category"] == "mood" else name(state),
                    "time": [at(unit["started"]), at(unit["ended"])], "boundary": unit["boundary"], "evidence": unit["evidence"]}
        if unit["category"] == "mood":
            interval["intensity"] = state.get("intensity")
            if unit.get("same_tick_excursion_folded"):
                interval["same_tick_excursion_folded"] = True
        if unit["activity_links"]:
            interval["activity_links"] = unit["activity_links"]
        group["intervals"].append(interval)
    result["state_timelines"] = list(groups.values())

    # Background signals are grouped for presentation; no weather, presence or relationship event is invented.
    groups = {}
    for unit in organized["background"]:
        owner = unit.get("subject") or people(unit.get("roles", []))
        key = (unit["category"], json.dumps(owner, sort_keys=True), unit["zone_visit"])
        group = groups.setdefault(key, {"category": unit["category"], "owner_or_roles": owner,
                                      "zone_visit": unit["zone_visit"], "recorded_signals": []})
        if "state" in unit:
            signal = {"state": name(unit["state"]), "time": [at(unit["started"]), at(unit["ended"])],
                      "boundary": unit["boundary"], "evidence": unit["evidence"]}
        else:
            signal = fact(unit)
        group["recorded_signals"].append(signal)
    result["background"] = list(groups.values())
    result["standalone_facts"] = [fact(unit) for identifier, unit in facts.items() if identifier not in embedded_facts]
    result["unassigned_decisions"] = [decision(unit) for identifier, unit in decisions.items() if identifier not in embedded_decisions]
    return result
