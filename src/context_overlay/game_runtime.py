"""EA lifecycle integration and simulation-thread collection."""

import json
import os
import pkgutil
import sys
import threading
import time
import traceback
from pathlib import Path

from context_overlay import VERSION
from context_overlay.collector import Collector, FIELDS
from context_overlay.ea_adapter import EAAdapter, enum_name
from context_overlay.hooks import Hooks
from context_overlay.history import DEFAULT_EVENT_CAPACITY, MIB
from context_overlay.model import new_id, utc_now
from context_overlay.recorder import Recorder
from context_overlay.storage import Journal, atomic_json


DEFAULTS = {"recorder_enabled": True, "collector_enabled": True, "semanticizer_enabled": True,
            "max_entities": 4096,
            "max_interactions_per_sim": 128, "max_buffs_per_sim": 256,
            "history_capacity": DEFAULT_EVENT_CAPACITY, "history_memory_mb": 1536,
            "history_query_limit": 8, "history_query_max_refs": 100000,
            "history_query_memory_mb": 256, "history_query_ttl_seconds": 120,
            "writer_capacity": 2048, "writer_memory_mb": 32,
            "run_output_mb": 2048, "disk_reserve_mb": 1024, "development_driver": False,
            "inspector_enabled": True}
DEFAULTS.update(external_rate_per_second=20, external_burst=40)
DEFAULTS.update(autonomy_enabled=True, autonomy_top_n=5, autonomy_pending_capacity=256,
                autonomy_pending_memory_mb=8, autonomy_pending_ttl_seconds=600)
DEFAULTS.update(event_view_memory_mb=4096, event_view_source_mb=128,
                event_view_query_limit=8, event_view_ttl_seconds=300, event_view_build_seconds=120)
DEFAULTS.update(export_views_on_stop=True)
_runtime = None
_lifecycle_hooks = None
_retired = []
_root = None
_startup_error = None


def data_root():
    global _root
    if _root is None:
        source = os.path.abspath(__file__)
        normalized = source.replace("\\", "/")
        marker = normalized.lower().find("/mods/")
        if marker < 0:
            raise RuntimeError("Cannot locate the active user-data directory from installed package path")
        _root = Path(normalized[:marker]) / "ContextOverlay"
        _root.mkdir(parents=True, exist_ok=True)
    return _root


def log(message):
    line = utc_now() + " " + str(message)
    try:
        with (data_root() / "runtime.log").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except Exception:
        try:
            import sims4.log
            sims4.log.Logger("ContextOverlay").error(line)
        except Exception:
            pass


def load_config():
    result = dict(DEFAULTS)
    path = data_root() / "config.json"
    if path.exists():
        with path.open("r", encoding="utf-8-sig") as stream:
            configured = json.load(stream)
        for name, value in configured.items():
            if name not in DEFAULTS:
                raise ValueError("Unknown configuration field: " + name)
            default = DEFAULTS[name]
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValueError(name + " must be a boolean")
            elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(name + " must be a positive integer")
            result[name] = value
    return result


class Runtime:
    def __init__(self, previous=None):
        import services
        import game_services
        from sims4.common import get_available_packs
        self.simulation_thread_id = threading.get_ident()
        self.api_ready = False
        self.config = previous.config if previous else load_config()
        self.session_id = previous.session_id if previous else new_id()
        self.directory = previous.directory if previous else data_root() / "runs" / self.session_id
        self.game_service_manager = game_services.service_manager
        self.history_suspended = False
        self.resumed = previous is not None
        self.event_views = getattr(previous, "event_views", None)
        self.view_targets = dict(getattr(previous, "view_targets", {}))
        self.view_exports = getattr(previous, "view_exports", None)
        self._reported_recording_error = getattr(previous, "_reported_recording_error", None)
        self._target_error_reported = False
        self.session_end_sequence = None
        self.adapter = EAAdapter(self.config)
        self.initial_scope = self.adapter.scope()
        self.manager = services.get_event_manager()
        self.provenance = json.loads(pkgutil.get_data("context_overlay", "build_info.json").decode("utf-8"))
        self.provenance.update({"runtime_python": sys.version,
                                "available_packs": [enum_name(pack) for pack in get_available_packs()],
                                "other_mods": "not_enumerated"})
        self.writer = previous.writer if previous else Journal(self.directory, self.config["writer_capacity"],
                              max_bytes=self.config["run_output_mb"] * MIB,
                              reserve_bytes=self.config["disk_reserve_mb"] * MIB,
                              queue_bytes=self.config["writer_memory_mb"] * MIB)
        self.recorder = previous.recorder if previous else Recorder(self.writer, self.session_id, self.config["history_capacity"],
                                 self.config["recorder_enabled"], memory_bytes=self.config["history_memory_mb"] * MIB,
                                 snapshot_limit=self.config["history_query_limit"],
                                 snapshot_refs=self.config["history_query_max_refs"],
                                 snapshot_bytes=self.config["history_query_memory_mb"] * MIB,
                                 snapshot_ttl=self.config["history_query_ttl_seconds"],
                                 external_rate=self.config["external_rate_per_second"],
                                 external_burst=self.config["external_burst"])
        self.recorder.begin_zone(self.initial_scope)
        self.collector = Collector(self.adapter, self.recorder, self.config["collector_enabled"], self.config["semanticizer_enabled"], self.provenance)
        self.hooks = Hooks(self.fail)
        from context_overlay.event_sources import EventSources
        self.sources = EventSources(self)
        from context_overlay.autonomy_capture import AutonomyCapture
        self.autonomy = AutonomyCapture(self)
        self.events = []
        self.event_names = {}
        self.alarm = None
        self.known = {}
        self.started = time.monotonic()
        self.poll_max_ms = 0.0
        self.poll_count = 0
        self.closed = False
        self.driver = None
        self.inspector = None
        self.inspector_error = None

    def fail(self, message):
        self.recorder.fail(message)
        self._report_recording_failure()

    def _run_report(self, phase, reason=None, errors=()):
        state = self.recorder.status()
        persistence = state["persistence"]
        end = getattr(self, "session_end_sequence", None)
        drained = persistence.get("accepted_sequence") == persistence.get("durable_sequence") and not persistence.get("pending_bytes", 0)
        complete = (state["state"] == "recording" and not errors and bool(end) and drained
                    and persistence.get("durable_sequence", 0) >= end) if phase == "closed" else None
        return {"format": "run_status_v1", "session_id": self.session_id, "updated_at": utc_now(),
                "phase": phase, "reason": reason, "capture_complete": complete,
                "game_time": getattr(self, "boundary_time", None), "session_end_sequence": end,
                "journal_drained": drained, "recorder": state, "cleanup_errors": list(errors)}

    def _save_run_report(self, report):
        try:
            directory = getattr(self, "directory", None) or self.writer.directory
            atomic_json(directory / "run-status.json", report)
        except Exception as exc:
            log("RUN STATUS WRITE FAILED: {}: {}".format(type(exc).__name__, exc))

    def _report_recording_failure(self):
        state = self.recorder.status()
        if state["state"] == "failed" and state["error"] != getattr(self, "_reported_recording_error", None):
            self._reported_recording_error = state["error"]
            log("RECORDING FAILED: " + str(state["error"]))
            self._save_run_report(self._run_report("failed", state["error"]))

    def _remember_view_targets(self, force=False):
        if not force and not getattr(self, "config", {}).get("export_views_on_stop", False):
            return
        try:
            for reference in self.adapter.household_members():
                self.view_targets[reference["key"]] = reference.get("name")
        except Exception as exc:
            if not getattr(self, "_target_error_reported", False):
                self._target_error_reported = True
                log("VIEW TARGETS UNAVAILABLE: {}: {}".format(type(exc).__name__, exc))

    def _exports(self):
        if self.view_exports is None:
            from context_overlay.run_artifacts import RunArtifacts
            self.view_exports = RunArtifacts(self.directory, self.session_id, self.provenance.get("build_game_version"),
                self.config["event_view_source_mb"] * MIB, self.config["event_view_memory_mb"] * MIB,
                self.config["event_view_build_seconds"])
        return self.view_exports

    def export_views(self):
        self._remember_view_targets(force=True)
        return self._exports().start(self.writer.status(), self.view_targets, self._run_report("active"))

    def _finish_outputs(self, reason, errors):
        self._report_recording_failure()
        report = self._run_report("closed", reason, errors)
        self._save_run_report(report)
        if getattr(self, "config", {}).get("export_views_on_stop", False):
            try:
                result = self._exports().finish(self.writer.status(), self.view_targets, report)
                log("VIEW EXPORT {}: {}".format(result["state"], json.dumps(result, ensure_ascii=False)))
            except Exception as exc:
                log("VIEW EXPORT FAILED: {}: {}".format(type(exc).__name__, exc))

    def install(self):
        import alarms
        import clock
        from event_testing.test_events import TestEvent
        from interactions.base.interaction import Interaction
        from objects.components.state import StateComponent
        if self.config["recorder_enabled"]:
            for name in ("InteractionStart", "InteractionExitedPipeline", "BuffBeganEvent", "BuffEndedEvent",
                         "AddRelationshipBit", "RemoveRelationshipBit"):
                event = getattr(TestEvent, name)
                self.manager.register_single_event(self, event)
                self.events.append(event)
                self.event_names[event] = name
            self.hooks.after(Interaction, "on_added_to_queue", lambda args, kwargs, result: self.capture("queued", args[0], "Interaction.on_added_to_queue"))
            self.hooks.after(Interaction, "_exited_pipeline", lambda args, kwargs, result: self.capture("exited", args[0], "Interaction._exited_pipeline"))
            self.hooks.after(StateComponent, "_trigger_on_state_changed", self.state_changed)
            self.sources.install()
            self.autonomy.install()
        self.recorder.note("zone_entry" if self.resumed else "session_start", {
                                             "scope": self.initial_scope, "zone_visit": self.recorder.zone_visit,
                                             "config": self.config,
                                             "provenance": self.provenance,
                                             "event_coverage": self.sources.status(), "event_diagnostics": self.sources.diagnostics(),
                                             "autonomy": self.autonomy.status(),
                                             "python": sys.version, "module_version": VERSION}, self.adapter.clock())
        self._remember_view_targets()
        self._save_run_report(self._run_report("active"))
        if self.config["development_driver"]:
            from context_overlay.test_driver import Driver
            self.driver = Driver(self)
        self.alarm = alarms.add_alarm_real_time(self, clock.interval_in_real_seconds(1), self.poll, repeating=True)
        self.poll(None)
        if self.config["inspector_enabled"]:
            try:
                from context_overlay.native_ui import NativeInspector
                self.inspector = NativeInspector(self, log)
                self.inspector.install()
            except Exception:
                self.inspector_error = traceback.format_exc()
                if self.inspector is not None:
                    try:
                        self.inspector.close()
                    except Exception:
                        log("INSPECTOR CLEANUP FAILED: " + traceback.format_exc())
                log("INSPECTOR SETUP FAILED: " + self.inspector_error)
        log("RUN STARTED {} in {}".format(self.session_id, self.directory))

    def capture(self, phase, interaction, source):
        if (self.closed or self.recorder.paused or not self.config["recorder_enabled"]
                or getattr(interaction, "_context_overlay_tool", False)):
            return
        if interaction.sim is None:
            return
        observed_here = getattr(interaction, "_context_overlay_recording_run", None) == self.recorder.interaction_namespace
        expected_id = self.recorder.interaction_event_id(interaction.sim.sim_info.sim_id, interaction.id)
        if not self.adapter.in_scope(interaction.sim) and not (phase == "exited" and expected_id in self.recorder.events):
            return
        if observed_here and expected_id not in self.recorder.events:
            return  # A live interaction evicted by FIFO must not reappear as new.
        facts = self.adapter.interaction(interaction)
        decision = getattr(interaction, "_context_overlay_autonomy_decision", None)
        if decision and decision[0] == self.recorder.interaction_namespace:
            facts["decision_event_id"] = decision[1]
            facts["decision_coverage"] = "exact_selected_instance"
        elif facts.get("trigger", {}).get("name") == "AUTONOMY":
            facts["decision_coverage"] = "not_observed_or_not_committed"
        if facts["parent_interaction_id"]:
            facts["parent_event_id"] = self.recorder.interaction_event_id(facts["parent_actor_id"], facts["parent_interaction_id"])
        event = self.recorder.interaction(phase, facts, self.adapter.clock(), source)
        if event:
            interaction._context_overlay_recording_run = self.recorder.interaction_namespace
        if (event and phase == "started" and facts["trigger"]["name"] == "REACTION"
                and getattr(interaction, "_context_overlay_reaction_run", None) != self.recorder.interaction_namespace):
            interaction._context_overlay_reaction_run = self.recorder.interaction_namespace
            self.recorder.fact("reaction.started", facts["participants"], {"interaction": facts["name"],
                "perception": "not_inferred"}, self.adapter.clock(), source, roles=facts["roles"],
                cause={"event_id": event["event_id"], "basis": "reaction_interaction_started"})

    def handle_event(self, sim_info, event_type, resolver):
        if self.closed or self.recorder.paused:
            return
        try:
            name = self.event_names.get(event_type, enum_name(event_type))
            sources = self.sources
            if any(frame["from_load"] for frame in sources.frames):
                return
            if sources.native(sim_info, name, resolver):
                return
            if name in ("InteractionStart", "InteractionExitedPipeline"):
                interaction = resolver.get_resolved_arg("interaction")
                if interaction is not None:
                    self.capture("started" if name == "InteractionStart" else "exited", interaction, "TestEvent." + name)
                return
            sim = sim_info.get_sim_instance() if sim_info is not None else None
            if not self.adapter.in_scope(sim):
                return
            actor = self.adapter.reference(sim)
            if name in ("BuffBeganEvent", "BuffEndedEvent"):
                if any(f["kind"] in ("buff_add", "buff_remove") for f in sources.frames):
                    return  # The method adapter retains handles, reason and source.
                value = self.adapter.resource(resolver.get_resolved_arg("buff"), resource_kind="buff", tokens=(sim,))
                added = name == "BuffBeganEvent"
                self.recorder.change([actor], "buffs", None if added else value, value if added else None,
                                     self.adapter.clock(), "TestEvent." + name,
                                     cause=sources.cause())
            elif name in ("AddRelationshipBit", "RemoveRelationshipBit"):
                target_id = resolver.get_resolved_arg("target_sim_id")
                info = self.adapter.services.sim_info_manager().get(target_id)
                other = info.get_sim_instance() if info else None
                if info is not None:
                    from relationships.relationship_enums import RelationshipDirection
                    bit = resolver.get_resolved_arg("relationship_bit")
                    value = self.adapter.resource(bit, resource_kind="relbit", tokens=(sim, other))
                    added = name == "AddRelationshipBit"
                    self.recorder.relationship_bit(actor, self.adapter.event_reference(info), value, added,
                                                    self.adapter.clock(), "TestEvent." + name,
                                                    bit.directionality == RelationshipDirection.BIDIRECTIONAL,
                                                    cause=sources.cause())
        except Exception:
            self.fail(traceback.format_exc())

    def state_changed(self, args, kwargs, result):
        if self.closed or self.recorder.paused:
            return
        component, state, old, new = args[:4]
        owner = component.owner
        sources = self.sources
        if any(frame["from_load"] for frame in sources.frames):
            return
        if not getattr(owner, "is_sim", False) and self.adapter.common_state(state) and self.adapter.in_scope(owner):
            self.recorder.change([self.adapter.reference(owner)], "object_states." + str(state.guid64),
                                 self.adapter.resource(old, resource_kind="object_state"), self.adapter.resource(new, resource_kind="object_state"),
                                  self.adapter.clock(), "StateComponent._trigger_on_state_changed",
                                  cause=sources.cause())

    def poll(self, _):
        if self.closed:
            return
        started = time.monotonic()
        try:
            _retired[:] = [writer for writer in _retired if writer._thread.is_alive() or writer.status()["error"]]
            if self.driver:
                self.driver.poll()
            if self.closed:
                return
            if self.recorder.status()["state"] != "recording":
                self._report_recording_failure()
                return
            self._remember_view_targets()
            now = self.adapter.clock()
            self.autonomy.poll()
            objects = self.adapter.live_objects()
            if len(objects) > self.config["max_entities"]:
                raise RuntimeError("Entity enumeration exceeds configured read budget")
            local = {}
            for obj in objects:
                if self.adapter.in_scope(obj):
                    reference = self.adapter.reference(obj)
                    local[reference["key"]] = (obj, reference)
            for key in set(self.known) - set(local):
                self.recorder.leave(self.known[key], now)
            for key in set(local) - set(self.known):
                obj, reference = local[key]
                self.recorder.enter(reference, now)
                if getattr(obj, "is_sim", False):
                    for item in self.adapter.interaction_objects(obj):
                        phase = "observed_running" if enum_name(item.pipeline_progress) == "RUNNING" else "observed"
                        self.capture(phase, item, "scope_entry_snapshot")
            self.known = {key: reference for key, (_, reference) in local.items()}
        except Exception:
            self.fail(traceback.format_exc())
        finally:
            self.poll_count += 1
            self.poll_max_ms = max(self.poll_max_ms, (time.monotonic() - started) * 1000)

    def status(self):
        return {"module_version": VERSION, "session_id": self.session_id, "directory": str(self.directory),
                "closed": self.closed,
                "provenance": self.provenance,
                "scope": self.initial_scope, "recorder": self.recorder.status(),
                "known_entities": len(self.known), "native_subscriptions": len(self.events),
                "data_hooks": len(self.hooks.entries), "poll_count": self.poll_count,
                "event_coverage": self.sources.status(),
                "event_diagnostics": self.sources.diagnostics(),
                "autonomy": self.autonomy.status(),
                "poll_max_ms": self.poll_max_ms, "config": self.config,
                "view_targets": getattr(self, "view_targets", {}),
                "view_exports": self.view_exports.status() if getattr(self, "view_exports", None) is not None else {"state": "not_started"},
                "inspector": ({"state": "failed", "error": self.inspector_error} if self.inspector_error else
                              self.inspector.status() if self.inspector is not None else {"state": "disabled"})}

    def export(self, kind="sim", identifier="active", limit=50, internal=False, fields=None,
               representation="both", include_history=True):
        from context_overlay import api
        packet = api.get_context(kind, identifier, fields=FIELDS if fields is None else fields,
            history_limit=limit, include_internal=internal, representation=representation,
            include_history=include_history, expected_session_id=self.session_id)
        return self._export_packet(packet)

    def export_nearby(self, identifier="active", radius="8", kinds="sim", same_level=True,
                      same_room=False, limit=32, metric="horizontal"):
        from context_overlay import api
        packet = api.get_nearby_entities(identifier,
            kinds=["sim", "object"] if kinds == "all" else kinds.split(","),
            radius=None if radius == "room" else float(radius), metric=metric,
            same_level=same_level, same_room=True if radius == "room" else same_room,
            limit=limit, expected_session_id=self.session_id)
        return self._export_packet(packet)

    def _export_packet(self, packet):
        summary = {"request_id": packet["request_id"], "path": self.writer.export(packet),
                   "status": "queued", "packet_status": packet["status"]}
        summary.update({key: packet[key] for key in ("count", "matched_count", "truncated", "coverage") if key in packet})
        history = packet.get("history", {})
        summary.update({key: history[key] for key in ("cursor", "next_cursor", "has_more", "total_matches") if key in history})
        return summary

    def history(self, kind="sim", identifier="active", limit=50, internal=False):
        if self.closed:
            raise RuntimeError("This recording run is closed")
        target = self.collector.resolve_history(kind, identifier)
        page = self.recorder.history(target["key"], limit, internal)
        return self._export_packet(self.collector.history_packet(page, target=target))

    def history_query(self, kind="sim", identifier="active", **filters):
        if self.closed:
            raise RuntimeError("This recording run is closed")
        target = self.collector.resolve_history(kind, identifier)
        return self._export_packet(self.collector.query_history(target, **filters))

    def history_next(self, cursor):
        if self.closed:
            raise RuntimeError("This recording run is closed")
        return self._export_packet(self.collector.history_packet(self.recorder.history_page(cursor)))

    def history_close(self, cursor):
        self.recorder.close_query(cursor)
        return {"closed": True}

    def finish_history(self):
        self.history_suspended = False
        if getattr(self, "event_views", None) is not None:
            self.event_views.shutdown()
        try:
            self.recorder.close_queries()
        finally:
            try:
                self.writer.close(wait=True)
            finally:
                # Failed writers retain their pending data for diagnostics.
                _retired.append(self.writer)

    def stop(self, reason, preserve_history=False):
        if self.closed:
            if self.history_suspended and not preserve_history:
                try:
                    self.session_end_sequence = self.recorder.note("session_end", {"reason": reason}, self.boundary_time)
                finally:
                    self.finish_history()
                self._finish_outputs(reason, [])
                log("RUN STOPPED {}: {}".format(self.session_id, reason))
            return
        self.api_ready = False
        self.closed = True
        errors = []

        def attempt(label, action):
            try:
                action()
            except Exception as exc:
                errors.append("{}: {}: {}".format(label, type(exc).__name__, exc))

        attempt("close_autonomy", self.autonomy.close)
        self.boundary_time = None
        def leave_zone():
            self.boundary_time = self.adapter.clock()
            self.recorder.end_zone(self.boundary_time)
        attempt("leave_zone", leave_zone)
        def session_boundary():
            sequence = self.recorder.note("zone_exit" if preserve_history else "session_end", {
                            "reason": reason, "status": self.recorder.status(),
                            "event_coverage": self.sources.status(), "autonomy": self.autonomy.status(),
                            "event_diagnostics": self.sources.diagnostics()}, self.boundary_time)
            if not preserve_history:
                self.session_end_sequence = sequence
        attempt("session_boundary", session_boundary)
        if self.inspector is not None:
            attempt("close_inspector", self.inspector.close)
        if getattr(self, "driver", None) is not None:
            attempt("close_driver", self.driver.close)
        if self.alarm is not None:
            def cancel_alarm():
                import alarms
                alarms.cancel_alarm(self.alarm)
            attempt("cancel_alarm", cancel_alarm)
            self.alarm = None
        attempt("remove_hooks", self.hooks.remove)
        for event in self.events:
            attempt("unregister_" + str(event), lambda event=event: self.manager.unregister(self, (event,)))
        self.events[:] = []
        # Travel keeps the same writer, sequence, FIFO, dedup table and query
        # index. Drain at this explicit boundary, never in a per-frame callback.
        if preserve_history:
            attempt("flush_writer", self.writer.flush)
        writer_error = self.writer.status().get("error")
        if writer_error:
            errors.append(writer_error)
        self.history_suspended = preserve_history and not errors
        if not self.history_suspended:
            attempt("close_history", self.finish_history)
            writer_error = self.writer.status().get("error")
            if writer_error and writer_error not in errors:
                errors.append(writer_error)
        if errors:
            self.fail("Run cleanup failed: " + "; ".join(errors))
            log("RUN CLEANUP FAILED: " + "; ".join(errors))
        if self.history_suspended:
            self._save_run_report(self._run_report("travel", reason, errors))
        else:
            self._finish_outputs(reason, errors)
        log("RUN {} {}: {}".format("SUSPENDED" if self.history_suspended else "STOPPED", self.session_id, reason))


def start(*_, resume_travel=False):
    global _runtime, _startup_error
    previous = _runtime
    continuation = None
    if previous is not None:
        if resume_travel and previous.closed and previous.history_suspended:
            import game_services
            if previous.game_service_manager is game_services.service_manager and game_services.service_manager is not None:
                continuation = previous
        if continuation is None:
            previous.stop("new_load")
    _runtime = None
    _startup_error = None
    try:
        _runtime = Runtime(continuation) if continuation else Runtime()
        if continuation:
            continuation.history_suspended = False  # Writer ownership moved.
        _runtime.install()
        _runtime.api_ready = not _runtime.closed
    except Exception:
        _startup_error = traceback.format_exc()
        log("START FAILED: " + _startup_error)
        if _runtime is not None:
            _runtime.fail(_startup_error)
            _runtime.stop("startup_failure")
        elif continuation:
            continuation.stop("startup_failure")
    return _runtime is not None and not _runtime.closed and _startup_error is None


def stop(*_):
    if _runtime is not None:
        import game_services
        manager = game_services.service_manager
        traveling = manager is not None and manager.is_traveling
        _runtime.stop("travel" if traveling else "zone_teardown", preserve_history=traveling)


def stop_game_services():
    import game_services
    manager = game_services.service_manager
    if _runtime is not None and (manager is None or not manager.is_traveling):
        _runtime.stop("game_services_shutdown")


def initialize():
    global _lifecycle_hooks
    if _lifecycle_hooks is not None:
        return
    import services
    import game_services
    import sims4.commands
    from zone import Zone
    _lifecycle_hooks = Hooks(log)
    _lifecycle_hooks.after(Zone, "on_loading_screen_animation_finished", lambda args, kwargs, result: start(resume_travel=True))
    _lifecycle_hooks.before(Zone, "on_teardown", lambda args, kwargs: stop())
    _lifecycle_hooks.before(game_services, "stop_services", lambda args, kwargs: stop_game_services())

    def respond(connection, operation, *args, **kwargs):
        output = sims4.commands.CheatOutput(connection)
        try:
            if _runtime is None:
                raise RuntimeError("Wait for the zone to load")
            output(json.dumps(getattr(_runtime, operation)(*args, **kwargs), ensure_ascii=False))
        except Exception as exc:
            output("ContextOverlay {} failed: {}".format(operation, exc))

    @sims4.commands.Command("co.status", command_type=sims4.commands.CommandType.Live)
    def status_command(_connection=None):
        state = _runtime.status() if _runtime else {"state": "waiting_for_zone"}
        if _startup_error:
            state.update({"state": "startup_failed", "error": _startup_error})
        sims4.commands.CheatOutput(_connection)(json.dumps(state))

    @sims4.commands.Command("co.export", command_type=sims4.commands.CommandType.Live)
    def export_command(kind: str="sim", identifier: str="active", limit: int=50, internal: bool=False,
                       representation: str="both", history: bool=True, fields: str="all", _connection=None):
        selected = None if fields == "all" else fields.split(",")
        respond(_connection, "export", kind, identifier, limit, internal, selected, representation, history)

    @sims4.commands.Command("co.export_views", command_type=sims4.commands.CommandType.Live)
    def export_views_command(_connection=None):
        respond(_connection, "export_views")

    @sims4.commands.Command("co.api_test", command_type=sims4.commands.CommandType.Live)
    def api_test_command(_connection=None):
        from context_overlay.api_probe import run
        output = sims4.commands.CheatOutput(_connection)
        try:
            output(json.dumps(run(data_root() / "api-self-test.json"), ensure_ascii=False))
        except Exception as exc:
            output("ContextOverlay API check failed: " + str(exc))

    @sims4.commands.Command("co.api_verify", command_type=sims4.commands.CommandType.Live)
    def api_verify_command(_connection=None):
        from context_overlay.api_probe import verify
        output = sims4.commands.CheatOutput(_connection)
        try:
            output(json.dumps(verify(data_root() / "api-self-test.json"), ensure_ascii=False))
        except Exception as exc:
            output("ContextOverlay API verification failed: " + str(exc))

    @sims4.commands.Command("co.api_inspect", command_type=sims4.commands.CommandType.Live)
    def api_inspect_command(_connection=None):
        from context_overlay.api_probe import inspect_target
        output = sims4.commands.CheatOutput(_connection)
        try:
            target = inspect_target(data_root() / "api-self-test.json")
            if _runtime.inspector is None or _runtime.inspector_error:
                raise RuntimeError("Inspector unavailable; inspect co.status")
            _runtime.inspector.open_target(target, external_history=True)
            output("ContextOverlay self-test history: {} ({})".format(target.get("name"), target["id"]))
        except Exception as exc:
            output("ContextOverlay API inspector failed: " + str(exc))

    @sims4.commands.Command("co.nearby", command_type=sims4.commands.CommandType.Live)
    def nearby_command(identifier: str="active", radius: str="8", kinds: str="sim",
                       same_level: bool=True, same_room: bool=False, limit: int=32,
                       metric: str="horizontal", _connection=None):
        respond(_connection, "export_nearby", identifier, radius, kinds, same_level, same_room, limit, metric)

    @sims4.commands.Command("co.history", command_type=sims4.commands.CommandType.Live)
    def history_command(kind: str="sim", identifier: str="active", limit: int=50,
                        internal: bool=False, _connection=None):
        respond(_connection, "history", kind, identifier, limit, internal)

    @sims4.commands.Command("co.restart", command_type=sims4.commands.CommandType.Live)
    def restart_command(_connection=None):
        started = start()
        sims4.commands.CheatOutput(_connection)("ContextOverlay opened a new independent run" if started else "ContextOverlay failed to start; inspect co.status and runtime.log")

    @sims4.commands.Command("co.inspect", command_type=sims4.commands.CommandType.Live)
    def inspect_command(kind: str="sim", identifier: str="active", _connection=None):
        output = sims4.commands.CheatOutput(_connection)
        try:
            if _runtime is None or _runtime.inspector is None or _runtime.inspector_error:
                raise RuntimeError("Inspector is not available; inspect co.status and runtime.log")
            _runtime.inspector.open(kind, identifier)
            output("ContextOverlay inspector opened")
        except Exception as exc:
            output("ContextOverlay inspector failed: " + str(exc))

    @sims4.commands.Command("co.history_query", command_type=sims4.commands.CommandType.Live)
    def history_query_command(kind: str="sim", identifier: str="active", page_size: int=50,
                              internal: bool=False, time_field: str="first_observed",
                              from_ticks: str="none", to_ticks: str="none", event_types: str="all",
                              fields: str="all", outcomes: str="all", tuning_ids: str="all",
                              order: str="desc", _connection=None):
        values = lambda value: None if value == "all" else value.split(",")
        respond(_connection, "history_query", kind, identifier, page_size=page_size, include_internal=internal,
                time_field=time_field, from_ticks=None if from_ticks == "none" else from_ticks,
                to_ticks=None if to_ticks == "none" else to_ticks,
                event_types=values(event_types), fields=values(fields), outcomes=values(outcomes),
                tuning_ids=values(tuning_ids), order=order)

    @sims4.commands.Command("co.history_next", command_type=sims4.commands.CommandType.Live)
    def history_next_command(cursor: str, _connection=None):
        respond(_connection, "history_next", cursor)

    @sims4.commands.Command("co.history_close", command_type=sims4.commands.CommandType.Live)
    def history_close_command(cursor: str, _connection=None):
        respond(_connection, "history_close", cursor)

    log("LOADED version={} python={} roots={}".format(VERSION, sys.version, sys.path))
    zone = services.current_zone()
    if zone is not None and zone.is_zone_running:
        start()
