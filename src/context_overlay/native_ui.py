"""EA-native picker windows and a script-only, immediate inspection interaction."""

import functools

import date_and_time
import services
from event_testing.results import TestResult
from interactions.aop import AffordanceObjectPair
from interactions.base.immediate_interaction import ImmediateSuperInteraction
from interactions.base.tuningless_interaction import create_tuningless_superinteraction
from interactions.context import InteractionContext
from objects.script_object import ScriptObject
from sims.sim import Sim
from sims4.localization import LocalizationHelperTuning
from sims4.tuning.instances import lock_instance_tunables
from sims4.utils import flexmethod
from singletons import DEFAULT
from ui.ui_dialog import ButtonType, UiDialogOkCancel, UiDialogResponse
from ui.ui_dialog_picker import GridPickerRow, PickerColumn, RowMapType, UiRecipePicker

from context_overlay.hooks import Hooks
from context_overlay.inspector import InspectorSession


def localized(text):
    # User names and resource strings are plain text, never UI markup.
    clean = str(text).replace("<", "＜").replace(">", "＞")
    return LocalizationHelperTuning.get_raw_text(clean)


def text_factory(text):
    return lambda *args, **kwargs: localized(text)


def current_inspector():
    from context_overlay.game_runtime import _runtime
    if _runtime is not None and not _runtime.closed:
        return _runtime.inspector
    return None


class InspectInteraction(ImmediateSuperInteraction):
    INSTANCE_SUBCLASSES_ONLY = True
    _context_overlay_tool = True

    @flexmethod
    def test(cls, inst, target=DEFAULT, context=DEFAULT, **kwargs):
        target = inst.target if target is DEFAULT and inst is not None else target
        context = inst.context if context is DEFAULT and inst is not None else context
        inspector = current_inspector()
        if inspector is not None and inspector.can_inspect(target, context):
            return TestResult.TRUE
        return TestResult(False, "ContextOverlay: target is outside the current observation scope")

    def _run_interaction_gen(self, timeline):
        inspector = current_inspector()
        if inspector is None or not inspector.can_inspect(self.target, self.context):
            return False
        inspector.open_object(self.target)
        return True
        yield  # Keep the EA interaction generator contract without waiting.


create_tuningless_superinteraction(InspectInteraction)
lock_instance_tunables(InspectInteraction, allow_user_directed=True, allow_autonomous=False,
                      visible=True, display_name=text_factory("查看状态与历史"),
                      pie_menu_priority=5, _saveable=None)
# A dedicated stable ID, not EA's zero ID for internal tuningless interactions.
# The pie-menu choice retains its AOP; no tuning package or manager lookup is needed.
InspectInteraction.guid64 = 0xE99B69EA5AE84512


class InspectorTextDialog(UiDialogOkCancel):
    @property
    def responses(self):
        # Supply navigation and close together, so EA's default OK/CANCEL
        # pair cannot be inserted between our custom actions.
        return ()


class NativeView:
    def __init__(self):
        self.current = None
        self.factory = UiRecipePicker.TunableFactory().default
        self.text_dialog_factory = InspectorTextDialog.TunableFactory().default

    def cancel(self):
        dialog, self.current = self.current, None
        if dialog is not None:
            service = services.ui_dialog_service()
            if service is not None:
                service.dialog_cancel(dialog.dialog_id)

    def show(self, title, text, rows, callback):
        self.cancel()
        active = services.get_active_sim()
        owner = active.sim_info if active is not None else None
        dialog = self.factory(owner, title=text_factory(title), text=text_factory(text),
                              subtitle=text_factory(text), help_tooltip=text_factory(text),
                              text_ok=text_factory("打开"), text_cancel=text_factory("关闭"),
                              min_selectable=1, max_selectable=1, is_sortable=False,
                              bubble_up_selected=False, dialog_options=0,
                              display_ingredient_check=False, display_prepped_ingredient_check=False,
                              display_funds=False, skill=None, filter_categories=(),
                              column_sort_priorities=None)
        # The client renders TEXT columns without reserved thumbnail space,
        # but does not display the recipe subtitle with this configuration.
        # Keep time/filter/page information in the visible column heading;
        # its tooltip retains the complete coverage and query explanation.
        heading = " | ".join(text.splitlines()[:2]) or "条目 / 摘要"
        if len(heading) > 160:
            heading = heading[:159] + "…"
        dialog.set_picker_columns_override((PickerColumn(
            column_type=PickerColumn.ColumnType.TEXT, column_data_name=RowMapType.NAME,
            column_icon_name=None, label=localized(heading), icon=None,
            tooltip=localized(text) if text else None, width=820, sortable=False),))
        for index, item in enumerate(rows):
            line = item["label"]
            if item["detail"] and item["detail"] not in line:
                line += "  |  " + item["detail"]
            dialog.add_row(GridPickerRow(option_id=index, tag=index,
                name=localized(line), row_description=localized(item["detail"])))

        self._present(dialog, lambda response: response.get_single_result_tag()
                      if response.response == ButtonType.DIALOG_RESPONSE_OK else None, callback)

    def show_text(self, title, text, rows, callback):
        """Display event lines in the dialog body, without any object/icon rows."""
        if not 1 <= len(rows) <= 3:
            raise ValueError("Text overview supports one to three navigation buttons")
        self.cancel()
        active = services.get_active_sim()
        owner = active.sim_info if active is not None else None
        dialog = self.text_dialog_factory(owner, title=text_factory(title), text=text_factory(text),
            text_ok=text_factory(rows[0]["label"]), text_cancel=text_factory("关闭"),
            is_special_dialog=False, dialog_options=0, icon=None, secondary_icon=None)
        identifiers = (ButtonType.DIALOG_RESPONSE_OK, ButtonType.DIALOG_RESPONSE_CUSTOM_1,
                       ButtonType.DIALOG_RESPONSE_CUSTOM_2)
        responses = [UiDialogResponse(dialog_response_id=identifiers[index],
            text=text_factory(item["label"]), sort_order=index)
            for index, item in enumerate(rows)]
        responses.append(UiDialogResponse(dialog_response_id=ButtonType.DIALOG_RESPONSE_CANCEL,
            text=text_factory("关闭"), sort_order=len(rows)))
        dialog.set_responses(tuple(responses))
        response_map = {identifiers[index]: index for index in range(len(rows))}
        self._present(dialog, lambda response: response_map.get(response.response), callback)

    def _present(self, dialog, result, callback):
        def responded(response):
            if self.current is not response:
                return
            # EA cancels this dialog AFTER invoking listeners. Do not cancel it
            # again when the callback opens a replacement window or closes us.
            self.current = None
            callback(result(response))

        self.current = dialog
        try:
            dialog.show_dialog(on_response=responded)
        except Exception:
            self.cancel()
            raise


class NativeInspector:
    def __init__(self, runtime, log):
        self.runtime = runtime
        self.log = log
        self.last_error = None
        self.session = None
        self.view = NativeView()
        self.hooks = Hooks(self.error)
        self.active = True
        self.ticks_per_hour = date_and_time.create_time_span(hours=1).in_ticks()

    def error(self, message):
        if message != self.last_error:
            self.log("INSPECTOR ERROR: " + str(message))
        self.last_error = str(message)

    def can_inspect(self, target, context):
        return bool(self.active and not self.runtime.closed and context is not None
                    and getattr(context, "sim", None) is not None
                    and getattr(context, "source", None) == InteractionContext.SOURCE_PIE_MENU
                    and not getattr(context, "shift_held", False)
                    and self.runtime.adapter.in_scope(target))

    def _hook_menu(self, owner):
        original = owner.potential_interactions
        state = {"active": True}

        @functools.wraps(original)
        def wrapped(obj, context, *args, **kwargs):
            direct = not kwargs.get("ignored_objects")
            found = False
            for aop in original(obj, context, *args, **kwargs):
                found = found or aop.affordance is InspectInteraction
                yield aop
            if state["active"] and direct and not found:
                try:
                    if self.can_inspect(obj, context):
                        yield AffordanceObjectPair(InspectInteraction, obj, InspectInteraction, None)
                except Exception as exc:
                    self.error("Menu: {}: {}".format(type(exc).__name__, exc))

        self.hooks._install(owner, "potential_interactions", wrapped, state)

    def install(self):
        try:
            # Sim overrides ScriptObject's generator instead of delegating to it.
            self._hook_menu(ScriptObject)
            self._hook_menu(Sim)
        except Exception:
            self.hooks.remove()
            raise
        self.log("INSPECTOR READY: entity menu and native picker windows installed")

    def open_object(self, obj):
        if not self.active or self.runtime.closed or not self.runtime.adapter.in_scope(obj):
            raise ValueError("Only instantiated entities on the current lot can be inspected")
        if self.session is not None:
            self.session.close()
        target = self.runtime.adapter.reference(obj)
        self.session = InspectorSession(self.runtime, self.view, target, self.ticks_per_hour, self.error)
        self.session.invoke(self.session.refresh)
        self.log("INSPECTOR OPEN " + target["key"])

    def open(self, kind="sim", identifier="active"):
        target = self.runtime.adapter.resolve(kind, identifier)
        self.open_object(self.runtime.adapter.object_for(target))

    def close(self):
        self.active = False
        try:
            if self.session is not None:
                self.session.close()
        finally:
            self.hooks.remove()

    def status(self):
        opened = self.session is not None and not self.session.closed
        return {"state": "ready" if self.active else "closed", "menu_hooks": len(self.hooks.entries),
                "window_open": opened, "target": self.session.target if opened else None,
                "last_error": self.last_error}
