"""Deterministic Chinese explanations; facts are never modified."""

from context_overlay import VERSION
from context_overlay.model import copy_data
from context_overlay.profiles import OBJECT_STATES
from context_overlay.event_sources import LABELS


FIELD_NAMES = {"needs.hunger": "饥饿需求值", "needs.energy": "精力需求值",
               "needs.fun": "娱乐需求值", "needs.social": "社交需求值",
               "needs.hygiene": "卫生需求值", "needs.bladder": "膀胱需求值",
               "buffs": "Buff", "relationship.bits": "关系标记",
               "relationships": "关系", "relationships.friendship": "友谊值",
               "relationships.romance": "浪漫关系值", "object_states": "物件状态",
               "identity": "身份", "location": "位置", "time": "游戏时间",
               "needs": "需求", "interactions": "当前交互"}
FIELD_NAMES.update({"hunger": "饥饿", "energy": "精力", "fun": "娱乐", "social": "社交",
                    "hygiene": "卫生", "bladder": "膀胱", "friendship": "友谊", "romance": "浪漫关系"})
FIELD_NAMES.update({"object_states." + identifier: entry[1] for identifier, entry in OBJECT_STATES.items()})
FIELD_NAMES.update({"target": "对象", "tracks": "关系数值", "bits": "关系标记"})
STATUS_NAMES = {"not_present": "未实例化", "not_applicable": "不适用", "unsupported": "尚未支持",
                "out_of_scope": "不在观测范围", "disabled": "已停用", "error": "读取失败"}
SOURCES = {"PIE_MENU": "玩家指令", "AUTONOMY": "自主选择", "SCRIPT": "脚本触发",
           "SCRIPT_WITH_USER_INTENT": "带玩家意图的脚本触发", "REACTION": "系统反应",
           "SOCIAL_ADJUSTMENT": "社交调整", "GET_COMFORTABLE": "姿态调整",
           "POSTURE_GRAPH": "姿态系统", "UNIT_TEST": "游戏测试来源",
           "BODY_CANCEL_AOP": "身体姿态取消衔接", "CARRY_CANCEL_AOP": "携带动作取消衔接",
           "VEHCILE_CANCEL_AOP": "载具动作取消衔接"}


def display(value):
    if value is None:
        return "未知"
    if isinstance(value, dict):
        if "text" in value and not value.get("text") and (value.get("fallback") or value.get("status")):
            reason = "显示文本为空" if value.get("status") in ("resolved", "empty_display_name") else "名称未取得"
            return str(value.get("fallback") or "无显示名称") + "（" + reason + "）"
        if value.get("status") in STATUS_NAMES:
            return "{}（{}）".format(STATUS_NAMES[value["status"]], value.get("reason", "无补充说明"))
        if "state" in value and "value" in value:
            return "{}：{}".format(display(value["state"]), display(value["value"]))
        if value.get("name"):
            return display(value["name"])
        if value.get("text"):
            if value.get("status") == "empty_display_name":
                return value["text"] + "（显示文本为空）"
            if value.get("status") == "no_display_name":
                absent = "参考资源未显式指定名称" if value.get("reason") == "no_name_in_reference_tuning" else "未取得显示名称"
                return "{}（{}{}）".format(value["text"], "隐藏资源，" if value.get("visible") is False else "", absent)
            suffix = "（名称未完整解析，待解释）" if value.get("status") in ("unmapped", "unresolved_tokens") else ""
            return value["text"] + suffix
        if value.get("label"):
            return value["label"]
        if "value" in value:
            return display(value["value"])
        if "id" in value:
            return "{} {}".format(value.get("kind", "资源"), value["id"])
        return "，".join("{}：{}".format(FIELD_NAMES.get(str(k), str(k)), display(v))
                         for k, v in sorted(value.items()) if k not in ("source", "status"))
    if isinstance(value, list):
        return "；".join(display(item) for item in value) if value else "无"
    if isinstance(value, float):
        return "{:.3f}".format(value).rstrip("0").rstrip(".")
    return str(value)


def role_names(event, roles):
    keys = {item["entity_key"] for item in event.get("roles", []) if item["role"] in roles}
    return "、".join(display(item) for item in event.get("participants", []) if item["key"] in keys)


def resource_details(value, root="", limit=128, node_limit=20000):
    """Readable descriptions with evidence paths; never replace resource names.

    Identical references repeated in event metadata/cause are shown once. A
    runtime text and an unselected static alternative remain separate entries.
    """
    pending, items, seen, nodes = [(value, root)], [], set(), 0
    truncated = False
    skip = {"name", "description", "tooltip", "localization", "raw_name", "rendered",
            "semantic_view", "reference_semantics", "source", "string_source", "provenance"}

    def add(resource, role, text, path, basis, attribute=None, index=None):
        nonlocal truncated
        if not isinstance(text, dict) or not text.get("text") or text.get("status") not in (
                "resolved", "raw_text", "rule_resolved", "unresolved_tokens"):
            return
        identity = (resource.get("resource_kind", resource.get("kind")), resource.get("id", resource.get("tuning_id")),
                    resource.get("tuning_name"), display(resource.get("name")))
        key = identity + (role, basis, attribute, index, text.get("hash"), text["text"], text["status"])
        if key in seen:
            return
        seen.add(key)
        if len(items) >= limit:
            truncated = True
            return
        items.append({"resource_id": identity[1], "resource_kind": identity[0],
            "tuning_name": identity[2], "label": identity[3], "role": role, "text": text["text"],
            "status": text["status"], "basis": basis, "attribute": attribute, "variant_index": index,
            "evidence_ref": path.lstrip(".")})

    while pending and nodes < node_limit:
        current, path = pending.pop()
        nodes += 1
        if isinstance(current, dict):
            for role in ("description", "tooltip"):
                text = current.get(role)
                source = text.get("source", {}) if isinstance(text, dict) else {}
                basis = ("observed_buff_owner_context" if source.get("token_binding", {}).get("basis") == "buff_owner_at_read" else
                         "base_description_overrides_not_evaluated" if source.get("client_overrides_evaluated") is False else
                         "runtime_text" if role == "description" else "tooltip_condition_not_evaluated")
                add(current, role, text, path + "." + role, basis, source.get("attribute"), source.get("intensity"))
            reference = current.get("reference_semantics", {})
            # Also expose static names where runtime name acquisition failed.
            name = current.get("name")
            roles = ("description", "tooltip") if isinstance(name, dict) and name.get("status") in (
                "resolved", "raw_text", "rule_resolved") else ("name", "description", "tooltip")
            for role in roles:
                for i, link in enumerate(reference.get("fields", {}).get(role, {}).get("alternatives", [])):
                    add(current, role, link.get("rendered"), path + ".reference_semantics.fields.{}.alternatives[{}].rendered".format(role, i),
                        "static_reference_not_historical_observation", link.get("attribute"), link.get("index"))
            pending.extend((child, path + "." + key) for key, child in reversed(list(current.items()))
                           if key not in skip and isinstance(child, (dict, list)))
        elif isinstance(current, list):
            pending.extend((current[i], path + "[{}]".format(i)) for i in range(len(current) - 1, -1, -1)
                           if isinstance(current[i], (dict, list)))
    return {"items": items, "truncated": truncated or bool(pending), "limit": limit}


def detail_text(value):
    result = resource_details(value, limit=32)
    rows = []
    for item in result["items"]:
        label = {"name": "参考名称", "description": "资源说明", "tooltip": "条件提示"}[item["role"]]
        if item["basis"] == "static_reference_not_historical_observation":
            label += "（静态参考，未确认当时使用）"
        elif item["basis"] == "base_description_overrides_not_evaluated":
            label += "（基础说明，未判断年龄或特征覆盖）"
        elif item["basis"] == "observed_buff_owner_context":
            label += "（按持有者解析）"
        elif item["role"] == "tooltip":
            label += "（未判断触发条件）"
        if item["status"] == "unresolved_tokens":
            label += "（未完整解析）"
        rows.append("{} · {}：{}".format(item["label"], label, item["text"]))
    if result["truncated"]:
        rows.append("资源说明超过展示范围，更多内容保留在原始数据中。")
    return "\n\n".join(rows)


def subjects(event):
    named = role_names(event, ("subject", "affected", "actor"))
    if named:
        target = role_names(event, ("target",))
        return named + ("与" + target if target else "")
    # Old journals may only distinguish the appended initiator. Do not turn an
    # index association into another recipient of the effect.
    excluded = {item["entity_key"] for item in event.get("roles", []) if item["role"] == "initiator"}
    return "、".join(display(item) for item in event.get("participants", []) if item["key"] not in excluded) or "对象未确认"


def explain_event(event):
    if event["event_type"] == "interaction":
        facts = event["facts"]
        actor = display(facts["actor"])
        action = display(facts.get("name") or facts.get("tuning_name") or facts.get("tuning_id"))
        target = "，目标为" + display(facts["target"]) if facts.get("target") else ""
        stage = event["stage"]
        if stage == "ended":
            wording = {"completed": "已自然结束", "cancelled": "已取消",
                       "failed": "失败", "unknown": "已退出，结果未确认"}[event["outcome"]]
        else:
            wording = {"queued": "已排队", "running": "在最近一次观测时运行中",
                       "triggered": "已观察到触发，后续结果未确认"}.get(stage, "阶段未知")
        source_raw = facts.get("trigger", {}).get("name")
        trigger = SOURCES.get(source_raw, "未映射来源 " + str(source_raw)) if source_raw else "来源未知"
        text = "{}的“{}”交互{}{}；{}。".format(actor, action, wording, target, trigger)
        if event.get("started_time") is None:
            text += "未观测到开始时间。"
        if event["tier"] == "internal":
            text = "[内部步骤] " + text
        if facts.get("outcome_result"):
            text += "玩法结果分支：{}（与交互退出类型分别记录）。".format(facts["outcome_result"])
        if facts.get("decision_event_id"):
            text += "已关联选中本交互的 Autonomy 决策。"
        elif facts.get("decision_coverage"):
            text += "未取得已提交的决策明细。"
    elif event["event_type"] == "game_event":
        actors = subjects(event)
        category = event["category"]
        payload = event.get("payload", {})
        text = "{}：{}。".format(actors, LABELS.get(category, category))
        if category == "autonomy.decision":
            selected = payload.get("selected", {})
            text = "{}通过 Autonomy 选择“{}”，{}；记录了 {} 层选择。".format(actors,
                display(selected.get("action")),
                "已进入立即执行" if payload.get("retention_gate") == "immediate_entered" else "已成功入队",
                len(payload.get("stages", [])))
            if selected.get("target"):
                text += "目标：{}。".format(display(selected["target"]))
            if payload.get("cache_origin"):
                text += "使用先前缓存的选择，选择时间与提交时间分别保留。"
            text += "请求来源：{}{}。入队或进入执行不代表行为完成。".format(
                SOURCES.get(payload.get("context_source"), str(payload.get("context_source"))),
                "，脚本请求" if payload.get("is_script_request") else "")
        elif category == "statistic.direct":
            statistic = payload.get("statistic") or {}
            label = {"LTR_Friendship_Main": "友谊值", "LTR_Romance_Main": "浪漫关系值"}.get(statistic.get("tuning_name"), display(statistic))
            text = "{}的{}：{} → {}（直接效果）。".format(actors, label, display(payload.get("before")), display(payload.get("after")))
        elif category == "inventory.transfer":
            before, after = payload.get("before") or {}, payload.get("after") or {}
            text = "{}：库存变化。容器：{} → {}；数量：{} → {}。".format(
                display(payload.get("item")) if payload.get("item") else role_names(event, ("item",)) or "物品未确认",
                display(before["container"]) if before.get("container") else "无库存容器",
                display(after["container"]) if after.get("container") else "无库存容器",
                display(before.get("stack_count")), display(after.get("stack_count")))
            if payload.get("split_product"):
                text += "拆出物品：{}。".format(display(payload["split_product"]))
            if before.get("hidden") != after.get("hidden"):
                text += "隐藏库存：{} → {}。".format(display(before.get("hidden")), display(after.get("hidden")))
        elif category == "crafting.completed":
            text = "{}：游戏报告制作产物“{}”；配方：{}；品质：{}。".format(actors,
                display(payload.get("crafted_object")), display(payload.get("recipe")), display(payload.get("quality")))
        elif category == "payment.completed":
            text = "{}：实际资金变化 {}，余额 {} → {}。".format(actors, display(payload.get("actual_amount")),
                display(payload.get("before")), display(payload.get("after")))
            if payload.get("recipe"):
                text += "配方：{}。".format(display(payload["recipe"]))
        elif category == "mood.changed":
            text = "{}：{} → {}；强度 {} → {}。".format(actors, display(payload.get("old_mood")),
                display(payload.get("new_mood")), display(payload.get("old_intensity")), display(payload.get("new_intensity")))
        elif category.startswith("aspiration."):
            text = "{}：{}；资源类型：{}。{}".format(actors, LABELS.get(category, category),
                display(payload.get("aspiration_type")), display(payload))
        else:
            text += display(payload)
        if category == "broadcast.effect":
            text += "仅表示效果执行回调，不推断目睹、理解或效果成功。"
        if category == "life.milestone":
            text += "记录的是里程碑解锁通知时间，不代表对应人生事件刚刚发生。"
        if category == "skill.level" and payload.get("interpretation"):
            text += "仅取得等级通知，未排除初始化，未补造旧等级。"
        if category not in LABELS:
            text = "[待解释] " + text
    else:
        actors = subjects(event)
        field_name = FIELD_NAMES.get(event["field"], event["field"])
        if event["field"] in ("buffs", "relationship.bits") and event["before"] is None:
            text = "{}新增{}：{}。".format(actors, field_name, display(event["after"]))
        elif event["field"] in ("buffs", "relationship.bits") and event["after"] is None:
            text = "{}移除{}：{}。".format(actors, field_name, display(event["before"]))
        else:
            text = "{}的{}：{} → {}。".format(actors, field_name, display(event["before"]), display(event["after"]))
    if event.get("effects"):
        text += "关联效果 {} 项（详情中保留各项事实）。".format(len(event["effects"]))
    if event["event_type"] != "interaction":
        initiator = role_names(event, ("initiator",))
        if initiator:
            text += "发起者：{}。".format(initiator)
        if event.get("tier") == "internal":
            text = "[内部机制] " + text
    if event.get("metadata", {}).get("classification") == "unclassified":
        text += "[用途待解释：保留原始条目]"
    result = {"text": text, "event_id": event["event_id"], "revision": event["revision"],
              "game_time": event.get("last_observed_time"), "rules_version": VERSION}
    if event.get("category") == "autonomy.decision":
        result["decision_details"] = autonomy_details(event["payload"])
    return result


def autonomy_details(payload):
    kinds = {"interaction": "行为选择", "target": "具体目标选择", "mixer_provider": "子行为提供者",
             "mixer_group": "子行为组别"}
    modes = {"weighted": "按权重抽取", "uniform": "均匀抽取", "deterministic": "确定性最高分", "unknown": "方式未确认"}
    labels = {"raw_score": "原始分数", "route_time": "路径时间", "rel_utility_score": "关系倍率",
              "buff_utility_score": "Buff 倍率", "commodity_scores": "需求贡献", "opportunity_costs": "机会成本",
              "efficiency": "效率", "duration": "持续时间", "estimated_distance": "预计距离",
              "mixer_weight": "子行为权重", "modified_desire": "修正后需求", "fulfillment_rate": "满足速率"}
    percent = lambda value: "未取得" if value is None else "{:.2%}".format(value)
    parts = ["选择时间：{}；提交时间：{}。".format(display(payload.get("selection_time")), display(payload.get("commit_time"))),
             "关联交互：" + str(payload.get("interaction_event_id"))]
    for i, stage in enumerate(payload.get("stages", []), 1):
        parts.append("第 {} 层 · {}：{}；实际池 {} 项，展示 {} 项，未展示原概率 {}。".format(
            i, kinds.get(stage["kind"], stage["kind"]), modes.get(stage["mode"], stage["mode"]),
            display(stage.get("pool_count")), len(stage["candidates"]), percent(stage.get("omitted_probability"))))
        parts.append("本层概率以完整选择池为分母；各层分别比较。")
        for candidate in stage["candidates"]:
            name = display(candidate.get("action") or candidate.get("group"))
            target = "，目标 " + display(candidate["target"]) if candidate.get("target") else ""
            parts.append("{}第 {} 名：{}{}；原分 {}，权重 {}，原概率 {}。".format(
                "[选中] " if candidate["selected"] else "", candidate["rank"], name, target,
                display(candidate.get("raw_score")), display(candidate["weight"]), percent(candidate["probability"])))
            score = candidate.get("score_components", {})
            values = score.get("values", {})
            available = ["{}：{}".format(labels.get(key, key), display(value)) for key, value in values.items() if value is not None]
            if available:
                parts.append("评分组成（游戏原值，贡献与倍率不相加为百分比）：" + "；".join(available))
            if score.get("native_text"):
                parts.append("游戏评分说明：" + str(score["native_text"]))
            if score.get("missing"):
                parts.append("未提供／未能关联的评分项：" + "、".join(labels.get(key, key) for key in score["missing"]))
            if score.get("engine_gsi_consistency_warning"):
                parts.append("游戏报告过 GSI 评分公式过期，明细不能作为重新计算总分的依据。")
        check = stage.get("multitasking", {})
        if check.get("applicable"):
            parts.append("多任务检查：实际值 {}，阈值 {}，{}。".format(display(check.get("roll")),
                display(check.get("threshold")), "通过" if check.get("passed") else "未通过"))
        elif check:
            parts.append("多任务检查：" + ("不适用。" if check.get("applicable") is False else "覆盖不足。"))
        if stage.get("coverage") == "scored_input_only_pool_unavailable":
            parts.append("实际抽取池未观测到；这里只能展示评分输入，未补造概率。")
    filters = payload.get("filters", {})
    parts.append("筛选覆盖：{}；观测到 {} 条行为评估记录。".format(filters.get("coverage"), filters.get("evaluated_affordance_rows")))
    for rejected in filters.get("rejections", []):
        parts.append("筛除 {} 项，阶段 {}，原因模板：{}".format(rejected["count"], rejected["stage"], rejected["reason_template"]))
    if filters.get("object_status_counts"):
        parts.append("对象检查状态计数：" + display(filters["object_status_counts"]))
    execution = payload.get("execution_result")
    if execution:
        parts.append("执行返回：{}；异常：{}。".format(display(execution.get("returned_success")), display(execution.get("exception_type"))))
    return "\n\n".join(parts)


def render(packet):
    current = []
    for name, result in packet.get("snapshot", {}).items():
        label = FIELD_NAMES.get(name, name)
        if result["status"] == "available":
            text = label + "：" + display(result["value"])
        else:
            text = "{}：{}（{}）".format(label, STATUS_NAMES.get(result["status"], result["status"]), result.get("reason", "无补充说明"))
        current.append({"field": name, "text": text, "evidence_ref": "snapshot." + name})
    history = [explain_event(event) for event in packet.get("history", {}).get("events", [])]
    return {"language": "zh-CN", "rules_version": VERSION, "current": current, "history": history,
            "resource_details": resource_details(packet)}


def translate(packet, catalog=None):
    result = copy_data(packet)
    view = catalog.enrich(packet) if catalog is not None else packet
    result["rendered"] = render(view)
    if catalog is not None:
        result["semantic_view"] = {key: value for key, value in view.items() if key in ("snapshot", "history", "target")}
        for item in result["rendered"]["resource_details"]["items"]:
            if item["evidence_ref"].split(".", 1)[0] in result["semantic_view"]:
                item["evidence_ref"] = "semantic_view." + item["evidence_ref"]
        result["rendered"]["name_resolution"] = {"catalog_format": catalog.data["format"],
            "catalog_inputs": catalog.data.get("inputs", {}),
            "catalog_provenance": catalog.data.get("provenance", {}),
            "historical_facts_preserved": True,
            **catalog.provenance}
    return result
