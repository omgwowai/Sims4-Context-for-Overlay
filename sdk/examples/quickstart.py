"""复制进自己的 MOD，改好导入路径，再从游戏线程调用 round_trip()。"""

from my_overlay_mod.vendor.context_overlay_client import Client, ContextOverlayError


client = Client()
PRODUCER = "team.my_overlay.demo"


def round_trip():
    """每次手动调用写一条示例事件；异常交给调用方记录或显示。"""
    status = client.get_status()
    if not status["ready"]:
        return {"ready": False, "state": status["state"]}
    if status["recorder"]["state"] != "recording":
        return {"ready": True, "recorded": False, "recorder": status["recorder"]}

    context = client.get_context("sim", "active", fields=["identity", "time", "location"],
                                 history_limit=5, origins=["game"], expected_session_id=status["session_id"])
    receipt = client.append_event(PRODUCER,
        {"text": "我的 Overlay 已经接上了。", "context_request": context["request_id"]},
        entities=[context["target"]["key"]], idempotency_key=context["request_id"],
        expected_session_id=context["session_id"])
    with client.history(None, None, producers=[PRODUCER], page_size=5,
                        expected_session_id=context["session_id"]) as query:
        events = query.page["history"]["events"]
    return {"ready": True, "recorded": True, "context": context,
            "receipt": receipt, "recent_own_events": events}


# 在你自己的游戏回调里：
# try:
#     result = round_trip()
# except ContextOverlayError as exc:
#     your_logger(exc.to_dict())
