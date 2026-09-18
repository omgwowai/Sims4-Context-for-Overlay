"""Source-bound accounting and rule comparisons; no inference of recall or quality scores."""

from collections import Counter

from .experience_recap import REVIEW_REASONS, digest, snapshot_digest, review_groups

SECTIONS = ("activities", "results", "relationship_observations", "states", "review_actions")


def validate_bundle(bundle):
    if bundle["snapshot_id"] != bundle["recap"]["snapshot_id"] or snapshot_digest(bundle) != bundle["snapshot_id"]:
        raise ValueError("Snapshot mismatch or modified bundle")
    if set(bundle["units"]) != set(bundle["ledger"]):
        raise ValueError("Unit ledger is incomplete")


def event_accounting(bundle):
    """Mutually exclusive primary destination, plus every unit/placement for audit.

    One source event can support multiple units (e.g. mood interval boundaries).
    Priority is recap > review > detail > external > not_expanded, not a deletion.
    Short refs are intentionally excluded so comparisons survive ref renumbering.
    """
    units, ledger = bundle["units"], bundle["ledger"]
    labels = {}
    for row in bundle["audit"].get("labels", []):
        labels.setdefault(row["unit"], []).append({key: row[key] for key in ("basis", "text", "identity")})
    fingerprints = {uid: digest({"unit": unit, "labels": labels.get(uid, [])}) for uid, unit in units.items()}
    rows = {}
    for evidence in bundle["audit"]["evidence"].values():
        identifier = evidence["event_id"]
        if identifier in rows:
            raise ValueError("Duplicate event in evidence accounting")
        members = evidence["units"]
        if any(uid not in units for uid in members):
            raise ValueError("Evidence references an absent unit")
        placements = sorted({ledger[uid]["placement"] for uid in members})
        destination = next((p for p in ("recap", "review", "detail", "external") if p in placements), "not_expanded")
        reasons = {r for uid in members for r in ledger[uid].get("review_reasons", units[uid].get("review_reasons", []))}
        # Old bundles had one generic review reason. Do not invent its missing breakdown.
        if "review" in placements and not reasons:
            reasons.add("legacy_review_unspecified")
        rows[identifier] = {"revision": evidence["revision"], "semantic_role": evidence["semantic_role"],
            "supporting": evidence["outside_entity_index"], "destination": destination,
            "units": {uid: {"lane": ledger[uid]["lane"], "placement": ledger[uid]["placement"],
                            "reason": ledger[uid]["reason"], "content_sha256": fingerprints[uid]} for uid in sorted(members)},
            "review_reasons": sorted(reasons), "label_bases": sorted({label["basis"] for uid in members for label in labels.get(uid, [])})}
        if not members:
            if evidence.get("disposition") in (None, "unresolved"):
                raise ValueError("Source event has neither a unit nor an explicit disposition")
            rows[identifier]["disposition"] = evidence["disposition"]
    return rows


def quality_report(bundle):
    validate_bundle(bundle)
    rows = event_accounting(bundle)
    selected = {key: row for key, row in rows.items() if not row["supporting"]}
    manifest, packet = bundle["manifest"], bundle["recap"]
    expected = manifest.get("selected_events", len(selected))
    if expected != len(selected):
        raise ValueError("Selected source event accounting is incomplete")
    counts = Counter(row["destination"] for row in selected.values())
    lane_counts = Counter(row["lane"] for row in bundle["ledger"].values())
    reasons = Counter(reason for row in bundle["ledger"].values() for reason in row.get("review_reasons", []))
    unknown = {}
    critical = []
    for ref, evidence in bundle["audit"]["evidence"].items():
        if evidence["semantic_role"] == "unknown" and not evidence["outside_entity_index"]:
            action = (evidence.get("occurrence") or {}).get("action", {})
            key = tuple(evidence.get("resource_identity", ["interaction", action.get("id"), action.get("tuning_name")]))
            group = unknown.setdefault(key, {"resource": list(key), "count": 0, "event_ids": []})
            group["count"] += 1
            group["event_ids"].append(evidence["event_id"])
        if evidence.get("importance") == "critical":
            occurrence = evidence.get("occurrence") or {}
            participants = {r["entity_key"] for r in occurrence.get("roles", []) if r["role"] in ("actor", "target")}
            eligible = manifest["entity_key"] in participants
            shown = [uid for uid in evidence["units"] if bundle["ledger"][uid]["lane"] == "activities"
                     and bundle["ledger"][uid]["placement"] == "recap"]
            critical.append({"event_id": evidence["event_id"], "evidence_ref": ref,
                "participation_confirmed": eligible, "in_main": bool(shown),
                "execution": occurrence.get("execution"), "units": shown})
    important_ok = all(row["in_main"] for row in critical if row["participation_confirmed"])
    totals = {"selected_events": len(selected), "supporting_events": len(rows) - len(selected),
        "organized_units": len(bundle["units"]),
        "organized_standalone": sum(not row["units"] for row in rows.values()),
        "units_by_lane": dict(sorted(lane_counts.items())),
        "event_destinations": {key: counts[key] for key in ("recap", "review", "detail", "external", "not_expanded")},
        "semantic_roles": dict(sorted(Counter(row["semantic_role"] for row in selected.values()).items())),
        "recap_sections": {section: len(packet[section]) for section in SECTIONS},
        "review_reason_units": dict(sorted(reasons.items())), "review_display_groups": len(review_groups(packet)),
        "name_issue_units": len(packet.get("name_quality", {}).get("unresolved", [])),
        "merged_links": sum(bool(link["merged"]) for link in bundle["audit"]["links"]),
        "not_expanded_reasons": dict(sorted(Counter(row["disposition"] for row in selected.values() if not row["units"]).items()))}
    checks = {"all_selected_events_accounted": len(selected) == expected,
              "destination_totals_match": sum(counts.values()) == len(selected),
              "critical_participant_actions_in_main": important_ok}
    return {"format": "experience_quality_v1", "snapshot_id": bundle["snapshot_id"],
        "source": {key: manifest[key] for key in ("source_sha256", "latest_events_sha256", "session_id", "entity_key")},
        "rules": {key: manifest[key] for key in ("view_version", "recap_version", "resource_policy", "implementation_sha256")},
        "totals": totals, "checks": checks, "critical_events": critical,
        "unknown_resources": sorted(unknown.values(), key=lambda row: (-row["count"], str(row["resource"]))),
        "events": rows,
        "limits": ["Accounting checks source retention, not capture recall or correctness of every classification.",
                   "Event destinations are mutually exclusive; units and reason flags may overlap.",
                   "Not expanded means retained in source with an explicit rule, not deleted.",
                   "Reviewed rules describe installed official tuning; third-party overrides are not verified."]}


def quality_markdown(report):
    total = report["totals"]
    rows = ["# 事件整理对账", "", "人物：`{}`。源日志：`{}`。".format(report["source"]["entity_key"], report["source"]["source_sha256"]),
        "", "检查原始事件是否有去向，不把压缩率当作准确率；未展开的事件仍在原始日志中。", "",
        "| 层级 | 数量 |", "| --- | ---: |",
        "| 人物关联的最新事件 | {} |".format(total["selected_events"]),
        "| 为解释关联而引入的其他事件 | {} |".format(total["supporting_events"]),
        "| 组织单元 | {} |".format(total["organized_units"]),
        "| 组织层独立保留的事件 | {} |".format(total["organized_standalone"]),
        "| 正文阅读项（活动／结果／关系观察／状态组） | {} |".format(sum(n for key, n in total["recap_sections"].items() if key != "review_actions")),
        "| 待核查动作 | {} |".format(total["recap_sections"]["review_actions"]),
        "| 待核查动作的阅读分组 | {} |".format(total["review_display_groups"]),
        "| 全部待核查单元（含非动作） | {} |".format(total["units_by_lane"].get("review", 0)), "",
        "## 最新事件的去向", "", "同一事件可支持多个单元，下表按正文、待核查、详情、外部、未展开的优先顺序只计一次。", ""]
    if "record_counts" in report:
        counts = report["record_counts"]
        rows[6:6] = ["原始 records 层：{} 条事件修订 + {} 条关联观察／共享边界 = {} 条。".format(
            counts["event_revisions"], counts["auxiliary_records"], sum(counts.values())), ""]
    names = {"recap": "进入正文", "review": "待核查", "detail": "按需详情", "external": "外部事件", "not_expanded": "按规则未展开"}
    rows += ["- {}：{}。".format(names[key], value) for key, value in total["event_destinations"].items()]
    rows += ["", "## 待核查原因", "", "各原因独立计数，同一单元可能同时有多种问题。", ""]
    rows += ["- {}：{} 个单元。".format(REVIEW_REASONS.get(key, key), value) for key, value in total["review_reason_units"].items()]
    rows += ["", "## 对账检查", ""]
    check_names = {"all_selected_events_accounted": "所有人物关联事件均有去向", "destination_totals_match": "各去向合计等于输入事件数",
                   "critical_participant_actions_in_main": "确认参与的重要火灾动作保留在正文"}
    rows += ["- {}：{}。".format(check_names[key], "通过" if value else "未通过") for key, value in report["checks"].items()]
    rows += ["", "完整事件 ID、归并链接、未展开规则和高频未知资源见同目录 quality.json 与 details.bundle.json。",
             "这些检查不证明采集到了游戏中的所有事件，也不推断灭火成功、持续专注或人物知情。", ""]
    return "\n".join(rows)


def compare_bundles(before, after):
    """Only compare rule projections of exactly the same source and person."""
    reports = [quality_report(bundle) for bundle in (before, after)]
    left, right = reports
    if left["source"] != right["source"]:
        raise ValueError("Rule comparison requires the same source, latest events, session and entity")
    # Supporting dependencies may legitimately change with new association rules.
    selected = [{key for key, row in r["events"].items() if not row["supporting"]} for r in reports]
    if selected[0] != selected[1]:
        raise ValueError("Selected source event set changed")
    changes = [{"event_id": key, "before": left["events"].get(key), "after": right["events"].get(key)}
               for key in sorted(set(left["events"]) | set(right["events"]))
               if left["events"].get(key) != right["events"].get(key)]
    def rendered(bundle, section):
        refs = {ref: ids for ref, ids in bundle["routes"].items() if not ref.startswith("@")}
        people = {person["ref"]: person["key"] for person in bundle["recap"]["people"]}
        def normalize(value, field=None):
            if isinstance(value, dict):
                return {key: normalize(item, key) for key, item in value.items()}
            if isinstance(value, list):
                if field == "roles":
                    return [[role, people[person]] for role, person in value]
                if field == "links":
                    return [[refs[ref], transition] for ref, transition in value]
                return [normalize(item, field) for item in value]
            if isinstance(value, str):
                if field in ("ref", "refs", "activity", "product_from"):
                    return refs[value]
                if field == "who":
                    return people[value]
            return value
        return normalize(bundle["recap"][section])
    changed_sections = [section for section in SECTIONS if rendered(before, section) != rendered(after, section)]
    return {"format": "experience_quality_comparison_v1", "source": left["source"],
        "before_snapshot": before["snapshot_id"], "after_snapshot": after["snapshot_id"],
        "before_rules": left["rules"], "after_rules": right["rules"],
        "before_totals": left["totals"], "after_totals": right["totals"],
        "changed_sections": changed_sections,
        "changed_events": len(changes), "changes": changes,
        "note": "Stable event/unit IDs; short refs ignored. Changes are not automatically improvements."}
