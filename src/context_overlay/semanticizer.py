"""Deterministic Chinese explanations; facts are never modified."""

from context_overlay import VERSION
from context_overlay.model import copy_data
from context_overlay.profiles import OBJECT_STATES


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
        if value.get("status") in STATUS_NAMES:
            return "{}（{}）".format(STATUS_NAMES[value["status"]], value.get("reason", "无补充说明"))
        if "state" in value and "value" in value:
            return "{}：{}".format(display(value["state"]), display(value["value"]))
        if value.get("name"):
            return display(value["name"])
        if value.get("text"):
            suffix = "（名称未完整解析）" if value.get("status") in ("unmapped", "unresolved_tokens") else ""
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
    else:
        actors = "、".join(display(item) for item in event.get("participants", []))
        field_name = FIELD_NAMES.get(event["field"], event["field"])
        if event["field"] in ("buffs", "relationship.bits") and event["before"] is None:
            text = "{}新增{}：{}。".format(actors, field_name, display(event["after"]))
        elif event["field"] in ("buffs", "relationship.bits") and event["after"] is None:
            text = "{}移除{}：{}。".format(actors, field_name, display(event["before"]))
        else:
            text = "{}的{}：{} → {}。".format(actors, field_name, display(event["before"]), display(event["after"]))
        if event.get("interval"):
            text += "这是采样区间内观测到的差异，未证明由某次行为导致。"
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


def translate(packet):
    result = copy_data(packet)
    result["rendered"] = render(packet)
    return result
