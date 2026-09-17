"""Read-only inspector navigation; no EA imports and no implicit file exports."""

from context_overlay.history import HistoryError
from context_overlay.model import copy_data
from context_overlay.semanticizer import FIELD_NAMES, STATUS_NAMES, LABELS, display, explain_event, detail_text, game_time


PAGE_SIZE = 15
TEXT_PAGE_SIZE = 700
RECORDING_NAMES = {"recording": "记录中", "disabled": "记录已停用", "failed": "记录已暂停：发生错误"}


def short(value, length=140):
    text = str(value).replace("\r", " ").replace("\n", " ")
    return text if len(text) <= length else text[:length - 1] + "…"


def event_label(event):
    if event["event_type"] == "interaction":
        facts = event["facts"]
        name = display(facts.get("name") or facts.get("tuning_name"))
        phase = {"running": "运行中", "queued": "排队", "triggered": "已触发", "ended": "已退出"}.get(event["stage"], "未知")
        if event["stage"] == "ended":
            phase = {"completed": "自然结束", "cancelled": "取消", "failed": "失败", "unknown": "结果未知"}[event["outcome"]]
        return short("{} · {}".format(name, phase), 100)
    if event["event_type"] == "game_event":
        return short(LABELS.get(event["category"], "待解释：" + event["category"]), 100)
    return short("{}变化".format(FIELD_NAMES.get(event["field"], event["field"])), 100)


def recent_event_line(event):
    return short("{} | {}".format(game_time(event.get("last_observed_time")),
                                 explain_event(event)["text"]), 120)


def preview(result):
    if result.get("status") != "available":
        return STATUS_NAMES.get(result.get("status"), result.get("status", "未知"))
    value = result.get("value")
    if isinstance(value, list):
        return "{} 项：{}".format(len(value), short(display(value[:3]), 160))
    return short(display(value), 180)


def row(label, detail, action):
    return {"label": label, "detail": detail, "action": action}


class InspectorSession:
    def __init__(self, runtime, view, target, ticks_per_hour, on_error):
        self.runtime = runtime
        self.view = view
        self.target = copy_data(target)
        self.ticks_per_hour = ticks_per_hour
        self.on_error = on_error
        self.packet = None
        self.context_error = None
        self.recent = None
        self.page = None
        self.previous = []
        self.hours = 24
        self.event_type = None
        self.internal = False
        self.closed = False
        self.generation = 0

    def close(self):
        self.closed = True
        self.generation += 1
        self.release_query()
        self.packet = self.recent = None
        self.view.cancel()

    def release_query(self):
        if self.page is not None:
            try:
                self.runtime.recorder.close_query(self.page["cursor"])
            except HistoryError as exc:
                if exc.code not in ("cursor_expired", "session_closed", "session_changed"):
                    self.on_error(str(exc))
        self.page = None
        self.previous = []

    def invoke(self, action):
        if self.closed or self.runtime.closed:
            self.close()
            return
        try:
            action()
        except Exception as exc:
            self.on_error("Inspector: {}: {}".format(type(exc).__name__, exc))
            if isinstance(exc, HistoryError):
                reason = {
                    "query_budget": "查询范围过大。请缩短时间范围，或筛选事件类型后重试。",
                    "query_limit": "已有查询占满限额。请稍后重试，或关闭其他查询。",
                    "cursor_expired": "历史页面已过期（默认 120 秒现实时间）。请刷新历史。",
                    "session_closed": "此次记录已结束，请关闭窗口并重新点击实体。",
                    "session_changed": "记录范围已经改变，请重新打开窗口。",
                }.get(exc.code, "历史查询失败。")
            else:
                reason = "无法显示此页面。错误已写入 runtime.log。"
            self.show("暂时无法显示", reason + "\n" + short(exc, 300), [
                row("返回概览", "", self.overview),
                row("调整历史筛选", "缩小查询范围不会影响记录", self.filters),
                row("刷新历史", "建立新的历史查询", self.new_history)])

    def show(self, title, text, rows, text_only=False):
        self.generation += 1
        generation = self.generation

        def selected(index):
            if generation != self.generation or self.closed:
                return
            if index is None:
                self.close()
            elif isinstance(index, int) and 0 <= index < len(rows):
                self.invoke(rows[index]["action"])

        show = self.view.show_text if text_only else self.view.show
        show(short(display(self.target), 70) + " · " + title, text, rows, selected)

    def refresh(self):
        self.release_query()
        self.context_error = None
        self.packet = None
        if self.runtime.collector.enabled:
            try:
                self.packet = self.runtime.collector.collect(
                    self.target, include_history=False, representation="raw")
            except Exception as exc:
                self.context_error = short(exc, 200)
                self.on_error("Inspector context: " + str(exc))
        else:
            self.context_error = "Context 采集器已停用；仍可查看历史。"
        self.recent = self.runtime.recorder.history(self.target["key"], limit=5, group_effects=True)
        self.overview()

    def coverage(self, history):
        status = history.get("status", "unknown")
        text = RECORDING_NAMES.get(status, status)
        observed = history.get("target_observation", {})
        if not observed.get("currently_observed"):
            text += "；此实体当前没有处于已登记的观测范围"
        error = history.get("coverage", {}).get("error")
        if error:
            text += "\n" + short(error, 200)
        evicted = history.get("coverage", {}).get("evicted_events", 0)
        if evicted:
            text += "\nFIFO 已淘汰 {} 条最早事件；当前查询只覆盖保留范围。".format(evicted)
        return text + "\n仅本次运行、当前地块的已观测记录；没有记录不代表没有发生。"

    def overview(self):
        if self.recent is None:
            self.refresh()
            return
        self.release_query()
        summary = [self.coverage(self.recent)]
        if self.packet:
            summary.append("快照时间：" + game_time(self.packet["read_finished"]))
            for name in (("needs", "interactions", "buffs") if self.target["kind"] == "sim" else ("object_states",)):
                summary.append(FIELD_NAMES[name] + "：" + preview(self.packet["snapshot"][name]))
        if self.context_error:
            summary.append("当前状态：" + self.context_error)
        summary.append("\n近期主要事件（最多 5 条；完整内容见“历史事件”）：")
        rows = [row("当前状态", "分类查看快照中的全部已采集字段", self.categories),
                row("历史事件", "默认近 24 游戏小时，按首次观测时间倒序，每页 15 条", self.new_history),
                row("刷新", "重新读取状态与近期事件", self.refresh)]
        for index, event in enumerate(self.recent["events"], 1):
            summary.append("{}. {}".format(index, recent_event_line(event)))
        if not self.recent["events"]:
            summary.append("当前没有可展示的主要事件。可以让游戏运行一段时间后刷新。")
        self.show("状态与历史", "\n".join(summary), rows, text_only=True)

    def categories(self):
        rows = [row("返回概览", "", self.overview)]
        if self.packet:
            for name, result in self.packet["snapshot"].items():
                rows.append(row(FIELD_NAMES.get(name, name), preview(result),
                                lambda name=name: self.field_page(name)))
        self.show("当前状态", "快照时间：" + game_time(self.packet["read_finished"]) if self.packet else self.context_error or "暂无快照", rows)

    def field_page(self, name, offset=0):
        result = self.packet["snapshot"][name]
        title = FIELD_NAMES.get(name, name)
        if result["status"] != "available":
            self.text_page(title, display(result), self.categories)
            return
        value = result["value"]
        if isinstance(value, list):
            entries = [(str(i + 1), item) for i, item in enumerate(value)]
        elif isinstance(value, dict):
            entries = [(FIELD_NAMES.get(str(key), str(key)), item) for key, item in value.items()]
        else:
            self.text_page(title, display(value), self.categories)
            return
        rows = [row("返回状态分类", "", self.categories)]
        if offset:
            rows.append(row("上一页", "", lambda: self.field_page(name, offset - PAGE_SIZE)))
        if offset + PAGE_SIZE < len(entries):
            rows.append(row("下一页", "", lambda: self.field_page(name, offset + PAGE_SIZE)))
        for label, item in entries[offset:offset + PAGE_SIZE]:
            text = display(item)
            description_text = detail_text(item)
            full_text = text + ("\n\n" + description_text if description_text else "")
            rows.append(row(short(label + " · " + text, 110), short(text, 180),
                            lambda label=label, text=full_text: self.text_page(title + " · " + label, text, lambda: self.field_page(name, offset))))
        description = "快照时间：{}\n共 {} 项；第 {} 页。数值沿用游戏内部单位，未转换为百分比。".format(
            game_time(self.packet["read_finished"]), len(entries), offset // PAGE_SIZE + 1)
        if not entries:
            description += "\n已读取的此分类为空；覆盖范围以采集目录为准。"
        self.show(title, description, rows)

    def text_page(self, title, text, back, offset=0):
        rows = [row("返回", "", back)]
        if offset:
            rows.append(row("上一段", "", lambda: self.text_page(title, text, back, offset - TEXT_PAGE_SIZE)))
        if offset + TEXT_PAGE_SIZE < len(text):
            rows.append(row("下一段", "", lambda: self.text_page(title, text, back, offset + TEXT_PAGE_SIZE)))
        self.show(title, text[offset:offset + TEXT_PAGE_SIZE] or "无", rows, text_only=True)

    def event_details(self, event, back):
        explanation = explain_event(event)
        text = explanation["text"]
        if explanation.get("decision_details"):
            text += "\n\n" + explanation["decision_details"]
        if event.get("facts", {}).get("decision_event_id"):
            text += "\n\n决策事件 ID：" + event["facts"]["decision_event_id"]
        descriptions = detail_text(event)
        if descriptions:
            text += "\n\n" + descriptions
        text += "\n\n首次观测：{}\n开始：{}\n结束：{}\n最近观测：{}".format(
            game_time(event.get("first_observed_time")), game_time(event.get("started_time")),
            game_time(event.get("ended_time")), game_time(event.get("last_observed_time")))
        text += "\n\n事件 ID：{}\n修订：{}\n持久化：{}（查询时状态）".format(
            event["event_id"], event["revision"], "已写入" if event.get("persistence") == "written" else "已接收，尚未确认写入")
        if event.get("source"):
            text += "\n来源：{}\n证据：{}".format(event["source"], event.get("evidence_type", "未知"))
        if event.get("roles"):
            text += "\n参与角色：" + display(event["roles"])
        if event.get("cause"):
            text += "\n关联依据：" + display(event["cause"])
        for effect in event.get("effects", []):
            text += "\n\n关联效果：{}\n事件 ID：{}\n时间：{}\n来源：{}".format(
                explain_event(effect)["text"], effect["event_id"],
                game_time(effect.get("last_observed_time")), effect.get("source", "未知"))
        self.text_page("事件详情", text, back)

    def filter_text(self):
        return "{}；{}；{}".format("本次运行全部时间" if self.hours is None else "近 {} 游戏小时".format(self.hours),
            {None: "全部事件类型", "interaction": "交互", "state_change": "状态变化", "game_event": "生活事件"}[self.event_type],
            "含内部步骤" if self.internal else "仅主要事件")

    def filters(self):
        self.release_query()
        rows = [row("返回概览", "", self.overview)]
        for hours in (1, 6, 24, None):
            label = "本次运行全部时间" if hours is None else "近 {} 游戏小时".format(hours)
            rows.append(row("时间：" + label, "选择后立即应用", lambda hours=hours: self.set_filter("hours", hours)))
        for value, label in ((None, "全部"), ("interaction", "交互"), ("state_change", "状态变化"), ("game_event", "生活事件")):
            rows.append(row("类型：" + label, "选择后立即应用", lambda value=value: self.set_filter("event_type", value)))
        rows.append(row("隐藏内部步骤" if self.internal else "显示内部步骤", "默认只看主要事件", lambda: self.set_filter("internal", not self.internal)))
        rows.append(row("应用当前筛选", "", self.new_history))
        self.show("历史筛选", self.filter_text() + "\n时间依据首次观测；查询过大时请缩短范围。", rows)

    def set_filter(self, name, value):
        setattr(self, name, value)
        self.new_history()

    def new_history(self):
        self.release_query()
        now = int(self.runtime.adapter.clock()["ticks"])
        self.page = self.runtime.recorder.query_history(self.target["key"], target=self.target,
            page_size=PAGE_SIZE, include_internal=self.internal, time_field="first_observed", order="desc",
            from_ticks=now - self.hours * self.ticks_per_hour if self.hours is not None else None,
            to_ticks=now + 1, event_types=[self.event_type] if self.event_type else None,
            group_effects=not self.internal and self.event_type is None)
        self.history_page()

    def move_page(self, backwards=False):
        cursor = self.previous[-1] if backwards else self.page["next_cursor"]
        updated = self.runtime.recorder.history_page(cursor)
        if backwards:
            self.previous.pop()
        else:
            self.previous.append(self.page["cursor"])
        self.page = updated
        self.history_page()

    def return_history(self):
        self.page = self.runtime.recorder.history_page(self.page["cursor"])
        self.history_page()

    def history_page(self):
        page = self.page
        rows = [row("返回概览", "", self.overview), row("筛选历史", self.filter_text(), self.filters),
                row("刷新历史", "释放旧查询并重新读取", self.new_history)]
        if self.previous:
            rows.append(row("上一页", "", lambda: self.move_page(True)))
        if page["has_more"]:
            rows.append(row("下一页", "", self.move_page))
        for event in page["events"]:
            rows.append(row(event_label(event), game_time(event.get("first_observed_time")),
                            lambda event=event: self.event_details(event, self.return_history)))
        text = "{}\n第 {} 页，匹配 {} 条；按首次观测时间倒序。\n本查询固定事件版本，更新内容请刷新。\n\n{}".format(
            self.filter_text(), page["offset"] // PAGE_SIZE + 1, page["total_matches"], self.coverage(page))
        if not page["events"]:
            text += "\n当前筛选下没有已观测事件。"
        self.show("历史事件", text, rows)
