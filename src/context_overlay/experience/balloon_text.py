"""Literal balloon descriptions shared by Context and derived event views."""

TYPE_LABELS = {"THOUGHT": "思考", "SPEECH": "说话", "DISTRESS": "困扰",
               "SENTIMENT": "感受", "SENTIMENT_INFANT": "婴儿感受"}


def describe(payload):
    kind = payload.get("balloon_type") or {}
    label = TYPE_LABELS.get(kind.get("name"), "未知类型（{}）".format(kind.get("value")))
    text = "已发送{}气泡请求".format(label)
    icon_object = payload.get("icon_object") or {}
    if icon_object.get("entity"):
        ref = icon_object["entity"]
        name = ref.get("name")
        if isinstance(name, dict):
            name = name.get("text") or name.get("fallback")
        text += "；图标引用：" + str(name or ref["key"])
    icon = payload.get("icon")
    if isinstance(icon, dict) and "instance" in icon:
        text += "；图标资源 {}/{}/{}（含义未映射）".format(icon["type"], icon["group"], icon["instance"])
    if payload.get("unavailable_fields"):
        text += "；部分气泡字段未取得"
    text += "；未确认屏幕显示，不推断想法或对话内容"
    return text
