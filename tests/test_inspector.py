"""Inspector navigation and native bridge contracts, without starting the game."""

import importlib.util
import sys
import unittest
from functools import partial
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_core import MemoryJournal, facts
from context_overlay import game_runtime
from context_overlay.collector import Collector
from context_overlay.ea_adapter import EAAdapter
from context_overlay.inspector import InspectorSession, PAGE_SIZE, TEXT_PAGE_SIZE
from context_overlay.model import entity, field
from context_overlay.recorder import Recorder


class View:
    def __init__(self):
        self.windows = []
        self.layouts = []
        self.cancelled = False

    def show(self, title, text, rows, callback):
        self.windows.append((title, text, rows, callback))
        self.layouts.append("picker")

    def show_text(self, title, text, rows, callback):
        self.windows.append((title, text, rows, callback))
        self.layouts.append("text")

    def cancel(self):
        self.cancelled = True

    def choose(self, label):
        title, text, rows, callback = self.windows[-1]
        callback(next(i for i, item in enumerate(rows) if item["label"] == label))


class InspectorChecks(unittest.TestCase):
    def setUp(self):
        self.now = 10000
        self.real_time = [0]
        self.target = facts()["actor"]
        self.journal = MemoryJournal()
        self.recorder = Recorder(self.journal, "ui-run", clock=lambda: self.real_time[0])
        self.recorder.enter(self.target, self.now)
        self.reads = []
        self.values = {"needs": {"hunger": field(25)}, "buffs": [], "interactions": [], "relationships": []}

        def read(target, name):
            self.reads.append((target["id"], name))
            return field(self.values.get(name, target))

        self.adapter = SimpleNamespace(resolve=lambda kind, identifier: entity(kind, identifier, "阿明"),
            clock=lambda: {"ticks": str(self.now), "display": "周一 12:00"}, scope=lambda: {}, read=read)
        self.runtime = SimpleNamespace(adapter=self.adapter, recorder=self.recorder, closed=False,
                                       collector=Collector(self.adapter, self.recorder))
        self.view, self.errors = View(), []
        self.session = InspectorSession(self.runtime, self.view, self.target, 100, self.errors.append)

    def add_events(self, count):
        for i in range(count):
            self.recorder.interaction("started", facts(i + 1), self.now - count + i, "native")

    def test_overview_pins_target_reads_memory_and_caps_recent_preview(self):
        self.add_events(20)
        original_count = len(self.journal.records)
        self.session.refresh()
        self.assertEqual(len(self.session.recent["events"]), 5)
        self.assertEqual(len(self.view.windows[-1][2]), 3)
        self.assertEqual(self.view.layouts[-1], "text")
        text = self.view.windows[-1][1]
        self.assertEqual(len([line for line in text.splitlines() if line[:1].isdigit()]), 5)
        self.assertIn("吃饭", text)
        self.target["id"] = "999"
        self.session.refresh()
        self.assertTrue(all(identifier != "999" for identifier, _ in self.reads))
        self.assertEqual(len(self.journal.records), original_count)

    def test_context_disabled_still_opens_history(self):
        self.runtime.collector.enabled = False
        self.session.refresh()
        self.assertIn("已停用", self.view.windows[-1][1])
        self.assertEqual(self.reads, [])
        self.view.choose("历史事件")
        self.assertIsNotNone(self.session.page)
        self.assertIn("没有已观测事件", self.view.windows[-1][1])

    def test_field_and_long_text_paging_preserve_all_content(self):
        self.values["buffs"] = ["Buff {}".format(i) for i in range(35)]
        self.session.refresh()
        self.session.field_page("buffs")
        self.view.choose("下一页")
        rows = self.view.windows[-1][2]
        self.assertTrue(any("Buff 15" in item["label"] for item in rows))
        self.assertFalse(any("Buff 0" in item["label"] for item in rows))
        text = "长说明" * 1800
        self.session.text_page("详情", text, self.session.overview)
        self.assertEqual(self.view.layouts[-1], "text")
        parts = [self.view.windows[-1][1]]
        while any(item["label"] == "下一段" for item in self.view.windows[-1][2]):
            self.view.choose("下一段")
            parts.append(self.view.windows[-1][1])
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(part) <= TEXT_PAGE_SIZE for part in parts))

    def test_history_next_previous_and_detail_freeze_versions(self):
        self.add_events(40)
        self.session.refresh()
        self.view.choose("历史事件")
        first = self.session.page
        self.view.choose("下一页")
        second = self.session.page
        self.assertEqual(second["offset"], PAGE_SIZE)
        self.assertEqual(len(self.session.previous), 1)
        self.view.choose("上一页")
        self.assertEqual(self.session.page["events"], first["events"])
        event = first["events"][0]
        changed = dict(event["facts"], finishing_type="NATURAL")
        self.recorder.interaction("exited", changed, self.now, "exit")
        self.session.event_details(event, self.session.return_history)
        self.assertEqual(self.view.layouts[-1], "text")
        self.assertIn("修订：1", self.view.windows[-1][1])
        self.view.choose("返回")
        self.assertEqual(self.session.page["events"][0]["revision"], 1)
        self.view.choose("刷新历史")
        self.assertEqual(self.session.page["events"][0]["revision"], 2)
        self.assertEqual(self.recorder.index.status()["snapshots"], 1)

    def test_filters_apply_time_type_and_internal_without_recording_mutation(self):
        self.recorder.interaction("started", facts(1), self.now - 200, "native")
        self.recorder.interaction("started", facts(2, main=False), self.now - 1, "native")
        self.recorder.change([self.target], "needs.hunger", 20, 25, self.now, "sample")
        self.session.hours = 1
        self.session.event_type = "interaction"
        self.session.new_history()
        self.assertEqual(self.session.page["total_matches"], 0)
        self.session.set_filter("internal", True)
        self.assertEqual(self.session.page["total_matches"], 1)
        self.session.set_filter("event_type", "state_change")
        self.assertEqual(self.session.page["events"][0]["field"], "needs.hunger")

    def test_expiry_is_reported_and_refresh_recovers(self):
        self.add_events(40)
        self.session.new_history()
        self.real_time[0] = 121
        self.view.choose("下一页")
        self.assertIn("已过期", self.view.windows[-1][1])
        self.view.choose("刷新历史")
        self.assertEqual(self.session.page["offset"], 0)
        self.assertEqual(self.recorder.index.status()["snapshots"], 1)

    def test_budget_failure_shows_recovery_and_keeps_recorder_alive(self):
        self.add_events(3)
        self.recorder.index.snapshot_ref_limit = 1
        self.session.invoke(self.session.new_history)
        self.assertIn("查询范围过大", self.view.windows[-1][1])
        self.assertEqual(self.recorder.status()["state"], "recording")
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)

    def test_close_and_runtime_end_release_queries_and_ignore_stale_callbacks(self):
        self.add_events(5)
        self.session.refresh()
        stale = self.view.windows[-1][3]
        self.session.new_history()
        before = len(self.view.windows)
        stale(0)
        self.assertEqual(len(self.view.windows), before)
        self.runtime.closed = True
        self.view.choose("刷新历史")
        self.assertTrue(self.session.closed)
        self.assertTrue(self.view.cancelled)
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)

    def test_cancel_closes_without_triggering_a_row(self):
        self.session.new_history()
        self.view.windows[-1][3](None)
        self.assertTrue(self.session.closed)
        self.assertEqual(self.recorder.index.status()["snapshots"], 0)

    def test_tool_interaction_is_ignored_by_capture_and_current_state(self):
        tool = SimpleNamespace(id=1, _context_overlay_tool=True)
        self.runtime.config = {"recorder_enabled": True}
        game_runtime.Runtime.capture(self.runtime, "started", tool, "native")
        ordinary = SimpleNamespace(id=2)
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.config = {"max_interactions_per_sim": 1}
        adapter.interaction = lambda item: item.id
        sim = SimpleNamespace(is_sim=True, si_state=[tool, ordinary], queue=[])
        self.assertEqual(adapter.read_interactions(sim)["value"], [2])


class NeedSamplingChecks(unittest.TestCase):
    def make_adapter(self):
        adapter = EAAdapter.__new__(EAAdapter)
        adapter.config = dict(game_runtime.DEFAULTS)
        values = {"need": 50, "relationship": 10}
        reads = []
        def needs(_):
            reads.append(True)
            return field({"hunger": field({"value": values["need"]})})
        adapter.read_needs = needs
        adapter.read_relationships = lambda _: field([{"target": entity("sim", 2),
            "tracks": {"friendship": field(values["relationship"])}}])
        sim = SimpleNamespace(sim_info=SimpleNamespace(sim_id=1))
        return adapter, sim, values, reads

    def test_default_disables_need_history_but_keeps_relationships_and_current_needs(self):
        adapter, sim, values, reads = self.make_adapter()
        self.assertFalse(adapter.config["record_need_changes"])
        recorder = Recorder(MemoryJournal())
        recorder.sample(entity("sim", 1), adapter.continuous(sim), 1)
        values.update(need=40, relationship=20)
        recorder.sample(entity("sim", 1), adapter.continuous(sim), 2)
        self.assertEqual(reads, [])
        changes = recorder.history("sim:1")["events"]
        self.assertEqual([change["field"] for change in changes], ["relationships.friendship"])
        self.assertEqual(adapter.read_needs(sim)["value"]["hunger"]["value"]["value"], 40)

    def test_opt_in_need_sampling_and_unavailable_needs_do_not_block_relationships(self):
        adapter, sim, values, reads = self.make_adapter()
        adapter.config["record_need_changes"] = True
        recorder = Recorder(MemoryJournal())
        recorder.sample(entity("sim", 1), adapter.continuous(sim), 1)
        values["need"] = 30
        recorder.sample(entity("sim", 1), adapter.continuous(sim), 2)
        self.assertEqual(recorder.history("sim:1")["events"][0]["field"], "needs.hunger")
        adapter.read_needs = lambda _: field(status="error")
        self.assertEqual(adapter.continuous(sim), {"relationship.2.friendship": 10})


def native_modules():
    modules = {}

    def module(name, **attrs):
        value = ModuleType(name)
        value.__dict__.update(attrs)
        modules[name] = value
        return value

    class Flex:
        def __init__(self, function):
            self.function = function
        def __get__(self, instance, owner):
            return partial(self.function, owner, instance)

    class ScriptObject:
        def potential_interactions(self, context, *args, **kwargs):
            yield SimpleNamespace(affordance="existing")

    class Sim(ScriptObject):
        def potential_interactions(self, context, *args, **kwargs):
            yield from super().potential_interactions(context, *args, **kwargs)

    class Dialog:
        sequence = 0
        DialogDescriptionDisplay = SimpleNamespace(FULL_DESCRIPTION=2)
        @classmethod
        def TunableFactory(cls):
            return SimpleNamespace(default=cls)
        def __init__(self, owner, **kwargs):
            self.__dict__.update(kwargs)
            self.dialog_id = Dialog.sequence
            Dialog.sequence += 1
            self.rows = []
            self.response = None
        def add_row(self, value):
            self.rows.append(value)
        def show_dialog(self, on_response):
            self.callback = on_response
        def set_responses(self, responses):
            self.extra_responses = responses
        def set_picker_columns_override(self, columns):
            self.columns = columns
        def get_single_result_tag(self):
            return self.rows[self.selected].tag

    class Result:
        TRUE = True
        def __init__(self, result, reason):
            self.result, self.reason = result, reason
        def __bool__(self):
            return self.result

    def lock(cls, **kwargs):
        for name, value in kwargs.items():
            setattr(cls, name, value)

    cancelled = []
    module("date_and_time", create_time_span=lambda **kwargs: SimpleNamespace(in_ticks=lambda: 100))
    module("services", get_active_sim=lambda: SimpleNamespace(sim_info="active-owner"),
           ui_dialog_service=lambda: SimpleNamespace(dialog_cancel=cancelled.append))
    module("event_testing.results", TestResult=Result)
    module("interactions.aop", AffordanceObjectPair=lambda affordance, target, sa, si: SimpleNamespace(affordance=affordance, target=target))
    module("interactions.base.immediate_interaction", ImmediateSuperInteraction=type("Immediate", (), {}))
    module("interactions.base.tuningless_interaction", create_tuningless_superinteraction=lambda cls: None)
    module("interactions.context", InteractionContext=SimpleNamespace(SOURCE_PIE_MENU=1))
    module("objects.script_object", ScriptObject=ScriptObject)
    module("sims.sim", Sim=Sim)
    module("sims4.localization", LocalizationHelperTuning=SimpleNamespace(get_raw_text=lambda text: text))
    module("sims4.tuning.instances", lock_instance_tunables=lock)
    module("sims4.utils", flexmethod=Flex)
    module("singletons", DEFAULT=object())
    module("ui.ui_dialog", ButtonType=SimpleNamespace(DIALOG_RESPONSE_OK=10001,
        DIALOG_RESPONSE_CANCEL=10002, DIALOG_RESPONSE_CUSTOM_1=10004, DIALOG_RESPONSE_CUSTOM_2=10005),
        UiDialogOkCancel=Dialog, UiDialogResponse=SimpleNamespace)
    class Column(SimpleNamespace):
        ColumnType = SimpleNamespace(TEXT=1)
    module("ui.ui_dialog_picker", GridPickerRow=SimpleNamespace, PickerColumn=Column,
           RowMapType=SimpleNamespace(NAME=0), UiRecipePicker=Dialog)
    return modules, cancelled


class NativeBridgeChecks(unittest.TestCase):
    def setUp(self):
        modules, self.cancelled = native_modules()
        self.patch = patch.dict(sys.modules, modules)
        self.patch.start()
        spec = importlib.util.spec_from_file_location("native_ui_checks", Path(__file__).resolve().parents[1] / "src/context_overlay/native_ui.py")
        self.native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.native)

    def tearDown(self):
        self.patch.stop()

    def test_native_picker_preserves_order_and_only_confirms_selected_tag(self):
        view = self.native.NativeView()
        selected = []
        view.show("<测试>", "状态", [{"label": "z", "detail": "末"}, {"label": "a", "detail": "首"}], selected.append)
        dialog = view.current
        self.assertEqual(dialog.title(), "＜测试＞")
        self.assertEqual(dialog.subtitle(), "状态")
        self.assertFalse(dialog.is_sortable)
        self.assertEqual(len(dialog.columns), 1)
        self.assertEqual(dialog.columns[0].column_type, 1)
        self.assertIsNone(dialog.columns[0].column_icon_name)
        self.assertEqual(dialog.columns[0].label, "状态")
        self.assertEqual(dialog.columns[0].tooltip, "状态")
        self.assertFalse(dialog.display_funds)
        self.assertFalse(dialog.display_ingredient_check)
        self.assertEqual([row.name for row in dialog.rows], ["z  |  末", "a  |  首"])
        self.assertEqual([row.tag for row in dialog.rows], [0, 1])
        dialog.selected, dialog.response = 0, 10001
        dialog.callback(dialog)
        self.assertEqual(selected, [0])
        self.assertIsNone(view.current)
        self.assertEqual(self.cancelled, [])
        view.show("标题", "", [], selected.append)
        dialog = view.current
        dialog.response = 10002
        dialog.callback(dialog)
        self.assertEqual(selected, [0, None])

    def test_text_overview_has_no_object_rows_and_buttons_dispatch_without_selection(self):
        view = self.native.NativeView()
        view.factory = lambda *args, **kwargs: self.fail("Overview must not construct an object picker")
        selected = []
        rows = [{"label": label} for label in ("当前状态", "历史事件", "刷新")]
        for response_id, expected in ((10001, 0), (10004, 1), (10005, 2), (10002, None), (-1, None)):
            view.show_text("概览", "1. 吃饭\n2. 做饭", rows, selected.append)
            dialog = view.current
            self.assertEqual(dialog.rows, [])
            self.assertIsNone(dialog.icon)
            self.assertFalse(dialog.is_special_dialog)
            self.assertEqual(dialog.responses, ())
            self.assertEqual([response.text() for response in dialog.extra_responses], ["当前状态", "历史事件", "刷新", "关闭"])
            self.assertEqual([response.text() for response in sorted(
                dialog.extra_responses, key=lambda r: r.sort_order)], ["当前状态", "历史事件", "刷新", "关闭"])
            dialog.response = response_id
            dialog.callback(dialog)
            self.assertEqual(selected[-1], expected)
            self.assertIsNone(view.current)
        self.assertEqual(self.cancelled, [])

    def test_replaced_native_dialog_cannot_act_and_is_cancelled_once(self):
        view, selected = self.native.NativeView(), []
        view.show("一", "", [], selected.append)
        old = view.current
        view.show("二", "", [], selected.append)
        old.callback(old)
        self.assertEqual(selected, [])
        self.assertEqual(self.cancelled, [old.dialog_id])

    def test_menu_injection_preserves_original_avoids_duplicates_and_uninstalls(self):
        native = self.native
        runtime = SimpleNamespace(closed=False, adapter=SimpleNamespace(in_scope=lambda target: target is not None))
        inspector = native.NativeInspector(runtime, lambda message: None)
        original = native.ScriptObject.potential_interactions
        inspector.install()
        context = SimpleNamespace(sim=object(), source=1, shift_held=False)
        target = native.Sim()
        aops = list(target.potential_interactions(context))
        self.assertEqual([aop.affordance for aop in aops], ["existing", native.InspectInteraction])
        context.source = 2
        self.assertEqual(len(list(target.potential_interactions(context))), 1)
        context.source = 1
        self.assertEqual(len(list(target.potential_interactions(context, ignored_objects=[object()]))), 1)
        inspector.close()
        self.assertIs(native.ScriptObject.potential_interactions, original)

    def test_inspect_interaction_is_read_only_user_directed_and_revalidates_target(self):
        native = self.native
        calls = []
        inspector = SimpleNamespace(can_inspect=lambda target, context: target == "local", open_object=calls.append)
        runtime = SimpleNamespace(closed=False, inspector=inspector)
        with patch.object(game_runtime, "_runtime", runtime):
            self.assertFalse(native.InspectInteraction.allow_autonomous)
            self.assertTrue(native.InspectInteraction.allow_user_directed)
            self.assertTrue(native.InspectInteraction.test(target="local", context=None))
            self.assertFalse(native.InspectInteraction.test(target="away", context=None))
            interaction = native.InspectInteraction()
            interaction.target, interaction.context = "local", None
            with self.assertRaises(StopIteration) as stopped:
                next(interaction._run_interaction_gen(None))
            self.assertTrue(stopped.exception.value)
            self.assertEqual(calls, ["local"])
            interaction.target = "away"
            with self.assertRaises(StopIteration) as stopped:
                next(interaction._run_interaction_gen(None))
            self.assertFalse(stopped.exception.value)
            self.assertEqual(calls, ["local"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
