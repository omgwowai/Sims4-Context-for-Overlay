"""Build a short offline recap and resolve its snapshot-bound evidence.

No model calls, runtime writes, calendar-day inference or token-based truncation.
The bundle is the detail store; only its recap field is the default input.
"""

from collections import Counter
import copy
import hashlib
import json
import re

from .experience_digest import name
from .experience_labels import LabelRenderer
from .experience_policy import compact
from .experience_view import build_experiences
from .filter_events import tick
from .event_sequence import EventSequence


VERSION = "experience_recap_v1_3"
REVIEW_REASONS = {"classification_missing": "分类待补充", "name_unresolved": "名称或参数未解析",
                  "association_missing": "所属活动未关联", "protected_detail": "因关联后果保留的执行细节",
                  "unsupported_observation_shape": "观测结构待核查"}
LANES = ("activities", "facts", "states", "decisions", "background", "details", "review", "external")
BOUNDARIES = {"paired_observations": "起止已配对", "end_not_observed": "未见结束",
              "start_not_observed": "未见开始", "discontinuous_observations": "观测不连续",
              "ambiguous_repeated_add": "重复添加，边界不明"}
EXITS = {"USER_CANCEL": "主动取消", "INTERACTION_INCOMPATIBILITY": "交互不兼容退出",
         "NATURAL": "自然退出", "RESET": "重置退出", "TRANSITION_FAILURE": "过渡失败",
         "DISPLACED": "被其他交互替换", "FAILED_TESTS": "条件检查失败",
         "INTERACTION_QUEUE": "队列退出", "SOCIALS": "社交交互退出"}
EXITS.update({"LIABILITY": "关联机制终止", "KILLED": "交互被终止", "AUTO_EXIT": "自动退出",
              "SI_FINISHED": "上级交互结束", "TARGET_DELETED": "目标已删除", "PRIORITY": "优先级退出",
              "WAIT_IN_LINE": "等候队列退出", "OBJECT_CHANGED": "物件变化退出", "SITUATIONS": "情境机制退出",
              "CRAFTING": "制作机制退出", "DIALOG": "对话框退出", "CONDITIONAL_EXIT": "条件退出",
              "FIRE": "火灾机制退出", "WEDDING": "婚礼机制退出", "ROUTING_FORMATION": "行进队形退出",
              "UNKNOWN": "退出原因未知"})
TYPE_NAMES = {"relationship.bits": "关系标记", "relationship.knowledge": "对他人的知识",
              "skill.level": "技能等级", "trait.added": "添加特征", "trait.removed": "移除特征",
              "aspiration.goal_completed": "目标完成通知", "aspiration.stage_completed": "阶段完成通知"}


def packed(value, canonical=False):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=canonical, separators=(",", ":"))


def digest(value):
    if isinstance(value, (list, EventSequence)):
        result = hashlib.sha256(b"[")
        for i, item in enumerate(value):
            if i:
                result.update(b",")
            result.update(packed(item, True).encode("utf-8"))
        result.update(b"]")
        return result.hexdigest()
    return hashlib.sha256(packed(value, True).encode("utf-8")).hexdigest()


def at(value, seconds=False):
    if value is None:
        return None
    display = value.get("display", "")
    match = re.fullmatch(r"(\d\d:\d\d):(\d\d)(?:\.\d+)? day:(\d+) week:(\d+)", display)
    return "{}/{} {}{}".format(match[4], match[3], match[1], ":" + match[2] if seconds else "") if match else display or "tick:" + str(value.get("ticks"))


def value_text(value, labels=None):
    if value is None:
        return "未记录"
    if isinstance(value, dict):
        if "name" in value or "text" in value or "tuning_name" in value or ("id" in value and "kind" in value):
            return (labels or LabelRenderer())(value)
        fields = {"_known_stats": "已知技能", "_known_traits": "已知特征", "mood": "情绪", "intensity": "强度"}
        return "；".join("{}={}".format(fields.get(k, k), value_text(value[k], labels)) for k in sorted(value)) or "空"
    if isinstance(value, list):
        return "、".join(value_text(v, labels) for v in value) or "空"
    return str(value)


def fact_text(unit, labels=None):
    """Shorten familiar payloads; unhandled fields remain explicitly visible."""
    render = lambda value: value_text(value, labels)
    kind, payload = unit["type"], unit.get("payload")
    p = payload if isinstance(payload, dict) else {}
    used = set()
    text = TYPE_NAMES.get(kind, kind)
    if kind == "payment.completed":
        amount = p.get("actual_amount")
        if type(amount) in (int, float):
            text = ("支付 §{}".format(-amount) if amount < 0 else "资金增加 §{}".format(amount))
        else:
            text = "支付结果未完整记录"
        if p.get("recipe"):
            text += "（{}）".format(render(p["recipe"]))
        used = {"actual_amount", "requested_amount", "before", "after", "reason", "recipe"}
    elif kind == "crafting.completed":
        text = "产物：{}；品质：{}".format(render(p.get("crafted_object")), render(p.get("quality")))
        # These fields are meaningful even when they disagree with the product label.
        if p.get("recipe"):
            text += "；配方：" + render(p["recipe"])
        used = {"crafted_object", "quality", "recipe", "record_origin", "skill"}
        if name(p.get("masterwork")) == "Masterwork_Normalwork":
            used.add("masterwork")
    elif kind in ("trait.added", "trait.removed"):
        text += "：" + render(p.get("trait"))
        used = {"trait", "trait_guid", "trait_type"}
    elif kind == "skill.level":
        text += "：{} {} → {}".format(render(p.get("skill")), render(p.get("before")), render(p.get("after")))
        used = {"skill", "before", "after"}
        if p.get("interpretation"):
            text += "（等级通知，未排除初始化）"
            used.add("interpretation")
    elif kind == "relationship.knowledge":
        # Null/unknown before is not an empty set; do not infer acquisition by set difference.
        text += "：{} → {}".format(render(p.get("before")), render(p.get("after")))
        used = {"before", "after", "changed_fields"}
    elif kind == "relationship.bits":
        before, after = unit.get("before"), unit.get("after")
        text += ("新增：" + render(after) if before is None and after is not None else
                 "移除：" + render(before) if after is None and before is not None else
                 "：{} → {}".format(render(before), render(after)))
    elif kind.startswith("aspiration."):
        text += "：{}；目标：{}".format(render(p.get("aspiration")), render(p.get("objective_instance")))
        used = {"aspiration", "objective_instance", "aspiration_type"}
    extra = {k: v for k, v in p.items() if k not in used}
    if extra:
        text += "；" + render(extra)
    if payload is not None and not isinstance(payload, dict):
        text += "；" + render(payload)
    if kind != "relationship.bits" and (unit.get("before") is not None or unit.get("after") is not None):
        text += "；{} → {}".format(render(unit.get("before")), render(unit.get("after")))
    if kind == "life.milestone":
        text += "（解锁通知，不代表人生事件刚发生）"
    return text


def execution(unit):
    exit_text = EXITS.get(unit.get("exit"), "退出原因待解析")
    if unit.get("started") is None:
        return "未见开始" + ("；" + exit_text if unit.get("exit") else "")
    if unit.get("ended") is None:
        return "已开始；未见结束"
    return "已执行；" + (exit_text if unit.get("exit") else "已退出")


def activity_timing(unit):
    """Keep queue/observation milestones separate from the execution envelope."""
    row = {"time": [at(unit.get("started")), at(unit.get("ended"))]}
    before = unit.get("queued") or unit.get("first_observed")
    start, seen = tick(unit.get("started")), tick(before)
    if seen is not None and (start is None or seen < start):
        row["queued_at" if unit.get("queued") else "observed_at"] = at(before, seconds=True)
    return row


def snapshot_digest(bundle):
    packet = {k: v for k, v in bundle["recap"].items() if k != "snapshot_id"}
    return digest({"manifest": bundle["manifest"], "recap": packet,
                   **{k: bundle[k] for k in ("units", "routes", "ledger", "audit")}})


def ordered_evidence(bundle):
    """Aliases retain source order even after canonical JSON reorders object keys."""
    return sorted(bundle["audit"]["evidence"], key=lambda ref: int(ref[1:]))


def organized_items(bundle):
    """The same explicit order for exports, cold queries and cached queries."""
    for uid in sorted(bundle["units"], key=lambda uid: int(bundle["ledger"][uid]["ref"][1:])):
        yield {"item_id": uid, "kind": "unit", "lane": bundle["ledger"][uid]["lane"], "unit": bundle["units"][uid]}
    for ref in ordered_evidence(bundle):
        evidence = bundle["audit"]["evidence"][ref]
        if not evidence["units"]:
            yield {"item_id": "standalone:" + evidence["event_id"], "kind": "standalone",
                   "event_id": evidence["event_id"], "revision": evidence["revision"],
                   "category": evidence["semantic_role"], "recap_disposition": evidence["disposition"],
                   "evidence_ref": ref}


def build_recap(loaded, entity_key, game_version=None):
    """Consume a verified read_journal result; CLI entry points use load_source."""
    if not loaded.get("complete") or loaded.get("event_scope") != "all" or not re.fullmatch(r"[0-9a-f]{64}", loaded.get("sha256", "")):
        raise ValueError("Verified complete source required")
    if not entity_key or not any(entity_key in e.get("entities", []) for e in loaded["events"]):
        raise ValueError("Requested entity is not indexed in this journal")
    view = build_experiences(loaded["events"], loaded["session_id"], entity_key, game_version)
    units, lanes = {}, {}
    for lane in LANES:
        for unit in view.get(lane, view["organized"].get(lane, [])):
            if unit["id"] in units:
                raise ValueError("Duplicate unit identity")
            units[unit["id"]], lanes[unit["id"]] = unit, lane
    refs = {identifier: "r" + str(i + 1) for i, identifier in enumerate(units)}
    routes = {refs[identifier]: [identifier] for identifier in units}
    routes.update({"@" + lane: [key for key in units if lanes[key] == lane] for lane in LANES})
    ledger = {key: {"lane": lanes[key], "placement": "detail", "ref": refs[key],
                    "reason": "detailed_" + lanes[key]} for key in units}
    names = view["consumer_packet"]["entities"]
    people = {}
    labels = LabelRenderer(game_version)

    def person(key):
        if key not in people:
            observed = names.get(key, {}).get("names_observed", [])
            people[key] = {"ref": "p" + str(len(people)), "key": key,
                           "name": " / ".join(observed) if observed else key}
        return people[key]["ref"]

    focus = person(entity_key)

    def participants(unit):
        return [[row["role"], person(row["entity_key"])] for row in unit.get("roles", [])
                if row["entity_key"].startswith("sim:")]

    def object_name(key):
        entry = names.get(key, {})
        evidence = entry.get("name_evidence") or [{"key": key, "name": n} for n in entry.get("names_observed", [])]
        rendered = []
        for value in evidence:
            text = labels(value, "object")
            if text not in rendered:
                rendered.append(text)
        return " / ".join(rendered) or key

    def shown(unit, reason="conservative_preservation"):
        ledger[unit["id"]].update(placement="recap", reason=reason)

    packet = {"scope": {"person": focus, "time_format": "游戏周/日 HH:MM，分钟精度；null 表示未观测边界",
        "activity_defaults": "roles 省略时行动者为主角；execution 省略时已执行、根交互自然退出",
        "activity_times": "time 只表示已观测执行起止；queued_at 为入队，observed_at 仅为首次观测，精确到秒；此前不算已执行，空白不代表空闲",
        "state_default_boundary": "boundary 省略时起止已配对",
        "basis": "采集时段内的记录；角色不等于目睹或知情；退出不等于玩法成功或全程专注",
        "details": "以 snapshot_id 和 ref 回查；@lane 可分页查询该类全部单元",
        "numeric": "局部数值观测保留在详情，不推算全天净变化"},
        "activities": [], "results": [], "relationship_observations": [], "states": [], "review_actions": []}
    for unit in view["organized"]["activities"]:
        labels.unit = unit["id"]
        row = dict(activity_timing(unit), ref=refs[unit["id"]],
                   action=labels(unit["action"], "interaction", unit.get("action_tuning")),
                   execution=execution(unit), roles=participants(unit))
        if unit.get("importance"):
            row["importance"] = unit["importance"]
        targets = [r["entity_key"] for r in unit["roles"] if r["role"] == "target" and not r["entity_key"].startswith("sim:")]
        if targets:
            row["target"] = [object_name(k) for k in targets]
        if entity_key not in [r["entity_key"] for r in unit["roles"]]:
            row["context"] = "关联上下文，未确认主角参与"
        if row["roles"] == [["actor", focus]]:
            del row["roles"]
        if unit.get("started") is not None and unit.get("ended") is not None and unit.get("exit") == "NATURAL":
            del row["execution"]
        if unit.get("topics"):
            row["topics"] = [dict(activity_timing(topic), action=labels(topic["action"], "interaction", topic.get("action_tuning")),
                                  execution=execution(topic), roles=participants(topic)) for topic in unit["topics"]]
        if unit.get("product_links"):
            row["product_from"] = sorted({refs[x["activity"]] for x in unit["product_links"]})
        packet["activities"].append(row)
        shown(unit)
    relationship_groups = {}
    for unit in view["organized"]["facts"]:
        labels.unit = unit["id"]
        if unit["type"] == "statistic.direct":
            if unit["category"] == "relationship_numeric":
                key = packed([unit["roles"], unit["statistic"], unit["zone_visit"]], True)
                group = relationship_groups.setdefault(key, {"refs": [], "statistic": value_text(unit["statistic"], labels),
                    "roles": participants(unit), "observed_changes": {"up": 0, "down": 0, "same": 0, "unknown": 0}})
                group["refs"].append(refs[unit["id"]])
                for observation in unit["observations"]:
                    before, after = observation["before"], observation["after"]
                    valid = type(before) in (int, float) and type(after) in (int, float)
                    direction = "unknown" if not valid else "up" if after > before else "down" if after < before else "same"
                    group["observed_changes"][direction] += 1
                shown(unit, "grouped_relationship_local_observations_not_net_change")
                continue
            ledger[unit["id"]]["reason"] = "local_numeric_observations_on_demand"
            continue
        row = {"ref": refs[unit["id"]], "time": at(unit["time"]), "text": fact_text(unit, labels), "roles": participants(unit)}
        object_roles = [[r["role"], r["entity_key"]] for r in unit.get("roles", []) if not r["entity_key"].startswith("sim:")]
        if object_roles:
            row["object_roles"] = object_roles
        if unit.get("activity"):
            row["activity"] = refs[unit["activity"]]
        if unit.get("unresolved_cause_event_id"):
            row["association"] = "原因未关联"
        packet["results"].append(row)
        shown(unit, "protected_result_or_social_fact")
    packet["relationship_observations"] = list(relationship_groups.values())
    groups = {}
    for unit in view["organized"]["states"]:
        labels.unit = unit["id"]
        state_text = value_text(unit["state"], labels)
        key = packed([unit["subject"], unit["category"], unit["zone_visit"], unit["state"]], True)
        if key not in groups:
            groups[key] = {"who": person(unit["subject"]), "kind": unit["category"], "visit": unit["zone_visit"],
                           "state": state_text, "intervals": []}
        row = {"ref": refs[unit["id"]], "time": [at(unit.get("started")), at(unit.get("ended"))]}
        if unit["boundary"] != "paired_observations":
            row["boundary"] = BOUNDARIES.get(unit["boundary"], unit["boundary"])
        if unit.get("activity_links"):
            row["links"] = [[refs[x["activity"]], x["transition"]] for x in unit["activity_links"]]
        groups[key]["intervals"].append(row)
        shown(unit, "state_intervals_grouped_without_merging")
    packet["states"] = list(groups.values())
    for unit in view["review"]:
        labels.unit = unit["id"]
        reasons = unit.setdefault("review_reasons", ["classification_missing"])
        ledger[unit["id"]].update(placement="review", reason=reasons[0])
        if "action" in unit:
            packet["review_actions"].append({"ref": refs[unit["id"]], "time": at(unit["time"]),
                "action": labels(unit["action"], "interaction", unit.get("action_tuning")), "execution": execution(unit),
                "roles": participants(unit), "timing": activity_timing(unit), "zone_visit": unit["zone_visit"],
                "resource": [unit["action"].get("id"), unit.get("action_tuning")],
                "review_reasons": reasons, "status": "执行情况按记录保留；分类、名称或关联仍待核查"})
    for unit in view["external"]:
        ledger[unit["id"]].update(placement="external", reason="not_game_fact")
    packet["coverage"] = {"review": dict(sorted(Counter(u["category"] for u in view["review"]).items())),
                          "on_demand": dict(sorted(Counter(r["lane"] for r in ledger.values() if r["placement"] == "detail").items())),
                          "external": len(view["external"]), "note": "待核查未清空；保留全部已组织活动，常规数值和评分按需展开"}
    packet["people"] = list(people.values())
    view["audit"]["labels"] = labels.audit
    for issue in labels.audit:
        if issue["basis"] in ("unresolved", "reviewed_partial"):
            reasons = units[issue["unit"]].setdefault("review_reasons", [])
            if "name_unresolved" not in reasons:
                reasons.append("name_unresolved")
    for key, unit in units.items():
        if unit.get("review_reasons"):
            ledger[key]["review_reasons"] = unit["review_reasons"]
    routes["@labels"] = sorted({r["unit"] for r in labels.audit})
    packet["name_quality"] = {
        "reviewed": [ref for uid, ref in refs.items() if any(r["unit"] == uid and r["basis"].startswith("reviewed") for r in labels.audit)],
        "unresolved": [ref for uid, ref in refs.items() if any(r["unit"] == uid and r["basis"] in ("unresolved", "reviewed_partial") for r in labels.audit)],
        "note": "中文释义不等于游戏显示名；名称参数缺失不补猜。使用 labels facet 回查原名、状态和释义来源"}
    bounds = [None, None]
    for event in loaded["events"]:
        for key in ("first_observed_time", "last_observed_time", "started_time", "ended_time"):
            value = event.get(key)
            if tick(value) is not None:
                if bounds[0] is None or tick(value) < tick(bounds[0]):
                    bounds[0] = value
                if bounds[1] is None or tick(value) > tick(bounds[1]):
                    bounds[1] = value
    packet["scope"]["observed_event_bounds"] = [at(t) for t in bounds]
    from .resources import implementation_hashes
    implementation = implementation_hashes()
    bundle = {"manifest": {"recap_version": VERSION, "view_version": view["policy_version"],
        "resource_policy": view["resource_policy"], "implementation_sha256": implementation,
        "source_sha256": loaded["sha256"], "session_id": loaded["session_id"], "entity_key": entity_key,
        "selected_events": view["metrics"]["selected_events"],
        "latest_events_sha256": digest(loaded["events"]), "observed_event_bounds": bounds,
        "bounds_basis": "min_max_event_observations_not_calendar_or_continuous_capture"},
        "recap": packet, "units": units, "routes": routes, "ledger": ledger, "audit": view["audit"]}
    snapshot = snapshot_digest(bundle)
    bundle["snapshot_id"] = packet["snapshot_id"] = snapshot
    bundle["metrics"] = {"prior_consumer_bytes": len(packed(view["consumer_packet"]).encode("utf-8")),
        "recap_bytes": len(packed(packet).encode("utf-8")), "activities": len(packet["activities"]),
        "protected_results": len(packet["results"]), "state_intervals": len(view["organized"]["states"]),
        "units": len(units), "recap_tokens": None}
    return bundle


def resolve(bundle, snapshot_id, entry, facet="units", offset=0, limit=20, loaded=None):
    if snapshot_id != bundle["snapshot_id"] or snapshot_id != bundle["recap"]["snapshot_id"] or snapshot_digest(bundle) != snapshot_id:
        raise ValueError("Snapshot mismatch or modified bundle")
    direct_evidence = bundle["audit"]["evidence"].get(entry)
    if entry not in bundle["routes"] and entry not in bundle["units"] and direct_evidence is None and entry != "@audit":
        raise ValueError("Unknown reference in this snapshot")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Use a non-negative offset and a limit between 1 and 100")
    ids = set(bundle["routes"].get(entry, [entry] if entry in bundle["units"] else
                                   direct_evidence.get("units", []) if direct_evidence else []))
    if facet == "decisions":
        ids.update(key for uid in list(ids) for key in bundle["units"][uid].get("decisions", []))
        ids = {uid for uid in ids if bundle["ledger"][uid]["lane"] == "decisions"}
    elif facet in ("evidence", "raw", "links", "labels"):
        # Include the activity's facts/states/decisions, not unrelated effects of a
        # dependency. This makes one activity reference useful for explanation.
        roots = set(ids)
        ids.update(uid for uid, unit in bundle["units"].items() if unit.get("activity") in roots
                   or any(x["activity"] in roots for x in unit.get("activity_links", [])))
        ids.update(key for uid in roots for key in bundle["units"][uid].get("decisions", []))
    elif facet != "units":
        raise ValueError("Unknown facet")
    evidence = (bundle["audit"]["evidence"] if entry == "@audit" else {entry: direct_evidence} if direct_evidence is not None else
                {ref: row for ref, row in bundle["audit"]["evidence"].items() if ids.intersection(row.get("units", []))})
    if facet in ("units", "decisions"):
        rows = [unit for uid, unit in bundle["units"].items() if uid in ids]
    elif facet == "evidence":
        rows = [dict(row, ref=ref) for ref, row in evidence.items()]
    elif facet == "links":
        rows = [row for row in bundle["audit"]["links"] if row["child"] in evidence or row["parent"] in evidence]
    elif facet == "labels":
        rows = [row for row in bundle["audit"].get("labels", []) if row["unit"] in ids]
    else:
        manifest = bundle["manifest"]
        if loaded is None or not loaded.get("complete") or loaded["sha256"] != manifest["source_sha256"] or loaded["session_id"] != manifest["session_id"] or digest(loaded["events"]) != manifest["latest_events_sha256"]:
            raise ValueError("The original verified source is required for raw evidence")
        by_id = {e["event_id"]: e for e in loaded["events"]}
        rows = []
        for row in evidence.values():
            event = by_id.get(row["event_id"])
            if event is None or event["revision"] != row["revision"]:
                raise ValueError("Source revision mismatch")
            rows.append(event)
    total = len(rows)
    return copy.deepcopy({"snapshot_id": snapshot_id, "ref": entry, "facet": facet, "total": total,
                          "offset": offset, "next_offset": offset + limit if offset + limit < total else None,
                          "items": rows[offset:offset + limit]})


def review_groups(packet):
    """Presentation only: every reference still points to its original occurrence."""
    groups = {}
    for row in packet["review_actions"]:
        key = packed([row.get("review_reasons", ["classification_missing"]), row.get("resource", row["ref"]),
                      row.get("roles"), row.get("zone_visit")], True)
        groups.setdefault(key, []).append(row)
    return list(groups.values())


def markdown(bundle):
    packet = bundle["recap"]
    people = {p["ref"]: p["name"] for p in packet["people"]}
    focus = people[packet["scope"]["person"]]
    role_names = {"actor": "行动者", "subject": "主体", "target": "对象", "initiator": "发起者",
                  "participant": "参与者", "recipient": "接收者", "owner": "所有者"}

    def roles(row, activity=False, exclude_actor=False):
        values = row.get("roles", [["actor", packet["scope"]["person"]]] if activity else [])
        return "；".join("{}：{}".format(role_names.get(r, "其他角色"), people[p])
                        for r, p in values if not (exclude_actor and r == "actor"))

    def actors(row, activity=False):
        values = row.get("roles", [["actor", packet["scope"]["person"]]] if activity else [])
        return "、".join(people[p] for r, p in values if r == "actor") or "行动者未记录"

    def span(times):
        return " → ".join(t or "未观测到" for t in times) if isinstance(times, list) else times or "时间未知"

    def timing(row):
        before = "入队：" + row["queued_at"] + "；" if row.get("queued_at") else (
            "首次观测：" + row["observed_at"] + "；" if row.get("observed_at") else "")
        times = row["time"]
        return before + ("执行：" + span(times) if times[0] else "未见开始 → " + (times[1] or "未见结束"))

    def esc(text):
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\n", " ")

    lines = ["# {} · 采集时段回顾".format(esc(focus)), "", "范围：{}。时间格式：游戏周/日 HH:MM。".format(span(packet["scope"]["observed_event_bounds"])), "",
             "保留每段已组织活动；入队／首次观测不等于开始执行，未见开始的条目只是尝试。时间空白不代表空闲，记录范围不代表人物知情范围。", "",
             "## 活动与结果", "", "| 引用 | 行动者 | 时间与阶段 | 活动及执行 | 其他人物与结果 |", "| --- | --- | --- | --- | --- |"]
    associated = set()
    for row in packet["activities"]:
        results = [f for f in packet["results"] if f.get("activity") == row["ref"]]
        associated.update(f["ref"] for f in results)
        detail = roles(row, True, exclude_actor=True)
        if row.get("target"):
            detail += "；目标：" + "、".join(row["target"])
        if row.get("context"):
            detail += "；" + row["context"]
        for f in results:
            detail += "；[{}] {} {}（{}）".format(f["ref"], roles(f), f["text"], f["time"])
        if row.get("product_from"):
            detail += "；产物来源：" + ", ".join(row["product_from"])
        lines.append("| {} | {} | {} | {}；{} | {} |".format(row["ref"], esc(actors(row, True)), timing(row), esc(row["action"]), esc(row.get("execution", "已执行；自然退出")), esc(detail.lstrip("；"))))
        for topic in row.get("topics", []):
            lines.append("| ↳ {} | {} | {} | {}；{} | {} |".format(row["ref"], esc(actors(topic)), timing(topic), esc(topic["action"]), esc(topic["execution"]), esc(roles(topic, exclude_actor=True))))
    independent = [f for f in packet["results"] if f["ref"] not in associated]
    if independent:
        lines += ["", "## 独立结果与关系记录", ""]
        lines += ["- [{}] {} · {} · {}".format(f["ref"], f["time"], esc(roles(f)), esc(f["text"])) for f in independent]
    if packet["relationship_observations"]:
        lines += ["", "## 关系数值的局部观测", "", "以下仅计数日志中的直接数值变化，不推断全天净变化。", ""]
        for group in packet["relationship_observations"]:
            counts = group["observed_changes"]
            lines.append("- {} · {}：上升 {} 次、下降 {} 次、未变 {} 次、未知 {} 次。引用：{}。".format(
                esc(roles(group)), esc(group["statistic"]), counts["up"], counts["down"], counts["same"], counts["unknown"], ", ".join(group["refs"])))
    lines += ["", "## 观测到的感受与情绪", "", "分组仅用于阅读；每个区间保持独立，不依据时间相邻补造原因。", ""]
    for group in packet["states"]:
        intervals = ["[{}] {}（{}）{}".format(r["ref"], span(r["time"]), r.get("boundary", "起止已配对"),
                     "；活动关联 " + value_text(r["links"]) if r.get("links") else "") for r in group["intervals"]]
        lines.append("- {} · {}：{}".format(esc(people[group["who"]]), esc(group["state"]), "；".join(intervals)))
    lines += ["", "## 待核查与详情", "", "待核查单元：{}。数值、评分与背景可按引用展开；未把它们当作已删除噪音。".format(sum(packet["coverage"]["review"].values())), ""]
    quality = packet.get("name_quality", {})
    lines += ["名称使用中文释义的条目：{}。仍有名称或参数缺口：{}。释义不是游戏显示名；用条目引用及 `labels` 查询原名、解析状态和资源依据。".format(
        ", ".join(quality.get("reviewed", [])) or "无", ", ".join(quality.get("unresolved", [])) or "无"), ""]
    lines += ["按原因、资源、人物角色和到访分组，仅折叠阅读展示；各次执行与引用保持独立。", ""]
    for group in review_groups(packet):
        first = group[0]
        reasons = "、".join(REVIEW_REASONS.get(code, code) for code in first.get("review_reasons", ["classification_missing"]))
        lines += ["<details>", "<summary>{} · {} · {} · {} 次</summary>".format(
            esc(reasons), esc(roles(first)), esc(first["action"]), len(group)), ""]
        lines += ["- [{}] {} · {} · {}".format(r["ref"], timing(r["timing"]) if r.get("timing") else r["time"],
                  esc(r["action"]), esc(r["execution"])) for r in group]
        lines += ["", "</details>", ""]
    lines += ["", "快照：`{}`。使用同一 bundle 的条目引用查询详情；`@review`、`@details`、`@background`、`@decisions` 可分页展开，`@audit` 查询全部证据。".format(bundle["snapshot_id"]), ""]
    return "\n".join(lines)
