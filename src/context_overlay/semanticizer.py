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
                absent = "参考资源未配置显示名称" if value.get("reason") == "no_name_in_reference_tuning" else "未配置显示名称"
                return "{}（{}{}；用途待解释）".format(value["text"], "隐藏资源，" if value.get("visible") is False else "", absent)
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
    elif event["event_type"] == "game_event":
        actors = subjects(event)
        category = event["category"]
        payload = event.get("payload", {})
        text = "{}：{}。{}".format(actors, LABELS.get(category, category), display(payload))
        if category == "statistic.direct":
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
    return {"text": text, "event_id": event["event_id"], "revision": event["revision"],
            "game_time": event.get("last_observed_time"), "rules_version": VERSION}


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
    return {"language": "zh-CN", "rules_version": VERSION, "current": current, "history": history}


def translate(packet, catalog=None):
    result = copy_data(packet)
    view = catalog.enrich(packet) if catalog is not None else packet
    result["rendered"] = render(view)
    if catalog is not None:
        result["semantic_view"] = {key: value for key, value in view.items() if key in ("snapshot", "history", "target")}
        result["rendered"]["name_resolution"] = {"catalog_format": catalog.data["format"],
            "catalog_inputs": catalog.data.get("inputs", {}),
            "catalog_provenance": catalog.data.get("provenance", {}),
            "historical_facts_preserved": True,
            **catalog.provenance}
    return result
