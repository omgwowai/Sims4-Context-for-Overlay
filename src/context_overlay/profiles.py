"""Small, explicitly verified resource profile for the first game scenario."""

PROFILE_VERSION = "common_object_states_v1"
OBJECT_STATES = {
    "15075": ("Broken", "损坏程度"),
    "15079": ("BrokenState", "损坏状态"),
    "15128": ("Dirty", "清洁程度"),
    "15129": ("DirtyState", "清洁状态"),
    "15188": ("Freshness", "新鲜度"),
    "15303": ("Quality", "品质"),
    "15319": ("Servings", "份量状态"),
    "28123": ("Consuming", "食用状态"),
}
RESOURCE_LABELS = dict(OBJECT_STATES, **{
    "15077": ("Brokenness_Neutral", "损坏程度中性状态"),
    "15081": ("BrokenState_Unbroken", "未损坏"),
    "15130": ("DirtyState_Clean", "干净"),
    "15131": ("DirtyState_Dirty", "脏污"),
    "15132": ("Dirty_Clean", "清洁程度：干净"),
    "15189": ("Freshness_Fresh", "新鲜"),
    "15190": ("Freshness_Spoiled", "已变质"),
    "15304": ("Quality_Normal", "品质普通"),
    "15305": ("Quality_Outstanding", "品质出色"),
    "15306": ("Quality_Poor", "品质差"),
    "15321": ("Servings_Large", "大份量（游戏状态）"),
    "28126": ("Consuming_NotEating", "未在食用"),
    "15797": ("friendship-friend", "朋友"),
    "15802": ("friendship-disliked", "不喜欢"),
    "129295": ("HasBeenFriends", "曾为朋友"),
    "13387": ("fridge_Cook", "选择烹饪食谱"),
    "13433": ("generic_consume_food", "吃东西"),
    "13377": ("Food_Eat_Active", "进食内部步骤（主动）"),
    "13378": ("Food_Eat_Passive", "进食内部步骤（被动）"),
})


def resource_name(identifier, tuning_name, raw_name):
    if raw_name.get("status") in ("resolved", "raw_text"):
        return raw_name
    entry = RESOURCE_LABELS.get(str(identifier))
    if entry is None or entry[0] != tuning_name:
        return raw_name
    return {"text": entry[1], "status": "rule_resolved", "rule": PROFILE_VERSION,
            "raw_name": raw_name}
