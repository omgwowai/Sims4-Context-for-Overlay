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
from context_overlay.collector import Collector
from context_overlay.ea_adapter import EAAdapter, enum_name
from context_overlay.hooks import Hooks
from context_overlay.history import DEFAULT_EVENT_CAPACITY, MIB
from context_overlay.model import envelope, new_id, utc_now
from context_overlay.recorder import Recorder
from context_overlay.storage import Journal


DEFAULTS = {"recorder_enabled": True, "collector_enabled": True, "semanticizer_enabled": True,
            "max_entities": 4096,
            "max_interactions_per_sim": 128, "max_buffs_per_sim": 256,
            "history_capacity": DEFAULT_EVENT_CAPACITY, "history_memory_mb": 1536,
            "history_query_limit": 8, "history_query_max_refs": 100000,
            "history_query_memory_mb": 256, "history_query_ttl_seconds": 120,
            "writer_capacity": 2048, "writer_memory_mb": 32,
            "run_output_mb": 2048, "disk_reserve_mb": 1024, "development_driver": False,
            "inspector_enabled": True}
DEFAULTS.update(autonomy_enabled=True, autonomy_top_n=5, autonomy_pending_capacity=256,
                autonomy_pending_memory_mb=8, autonomy_pending_ttl_seconds=600)
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
    def __init__(self):
        import services
        from sims4.common import get_available_packs
        self.simulation_thread_id = threading.get_ident()
        self.api_ready = False
        self.config = load_config()
        self.session_id = new_id()
        self.directory = data_root() / "runs" / self.session_id
        self.adapter = EAAdapter(self.config)
        self.initial_scope = self.adapter.scope()
        self.manager = services.get_event_manager()
        self.provenance = json.loads(pkgutil.get_data("context_overlay", "build_info.json").decode("utf-8"))
        self.provenance.update({"runtime_python": sys.version,
                                "available_packs": [enum_name(pack) for pack in get_available_packs()],
                                "other_mods": "not_enumerated"})
        self.writer = Journal(self.directory, self.config["writer_capacity"],
                              max_bytes=self.config["run_output_mb"] * MIB,
                              reserve_bytes=self.config["disk_reserve_mb"] * MIB,
                              queue_bytes=self.config["writer_memory_mb"] * MIB)
        self.recorder = Recorder(self.writer, self.session_id, self.config["history_capacity"],
                                 self.config["recorder_enabled"], memory_bytes=self.config["history_memory_mb"] * MIB,
                                 snapshot_limit=self.config["history_query_limit"],
                                 snapshot_refs=self.config["history_query_max_refs"],
                                 snapshot_bytes=self.config["history_query_memory_mb"] * MIB,
                                 snapshot_ttl=self.config["history_query_ttl_seconds"])
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
        first = not self.recorder.paused
        self.recorder.fail(message)
        if first:
            log("RECORDING FAILED: " + str(message))

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
        self.recorder.note("session_start", {"scope": self.adapter.scope(), "config": self.config,
                                             "provenance": self.provenance,
                                             "event_coverage": self.sources.status(), "event_diagnostics": self.sources.diagnostics(),
                                             "autonomy": self.autonomy.status(),
                                             "python": sys.version, "module_version": VERSION}, self.adapter.clock())
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
        observed_here = getattr(interaction, "_context_overlay_recording_run", None) == self.session_id
        expected_id = "{}:interaction:{}:{}".format(self.session_id, interaction.sim.sim_info.sim_id, interaction.id)
        if not self.adapter.in_scope(interaction.sim) and not (phase == "exited" and expected_id in self.recorder.events):
            return
        if observed_here and expected_id not in self.recorder.events:
            return  # A live interaction evicted by FIFO must not reappear as new.
        facts = self.adapter.interaction(interaction)
        decision = getattr(interaction, "_context_overlay_autonomy_decision", None)
        if decision and decision[0] == self.session_id:
            facts["decision_event_id"] = decision[1]
            facts["decision_coverage"] = "exact_selected_instance"
        elif facts.get("trigger", {}).get("name") == "AUTONOMY":
            facts["decision_coverage"] = "not_observed_or_not_committed"
        if facts["parent_interaction_id"]:
            facts["parent_event_id"] = "{}:interaction:{}:{}".format(self.session_id, facts["parent_actor_id"], facts["parent_interaction_id"])
        event = self.recorder.interaction(phase, facts, self.adapter.clock(), source)
        if event:
            interaction._context_overlay_recording_run = self.session_id
        if (event and phase == "started" and facts["trigger"]["name"] == "REACTION"
                and getattr(interaction, "_context_overlay_reaction_run", None) != self.session_id):
            interaction._context_overlay_reaction_run = self.session_id
            self.recorder.fact("reaction.started", facts["participants"], {"interaction": facts["name"],
                "perception": "not_inferred"}, self.adapter.clock(), source, roles=facts["roles"],
                cause={"event_id": event["event_id"], "basis": "reaction_interaction_started"})

    def handle_event(self, sim_info, event_type, resolver):
        if self.closed or self.recorder.paused:
            return
        try:
            name = self.event_names.get(event_type, enum_name(event_type))
            sources = getattr(self, "sources", None)
            if sources is not None and any(frame["from_load"] for frame in sources.frames):
                return
            if sources is not None and sources.native(sim_info, name, resolver):
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
                if sources is not None and any(f["kind"] in ("buff_add", "buff_remove") for f in sources.frames):
                    return  # The method adapter retains handles, reason and source.
                value = self.adapter.resource(resolver.get_resolved_arg("buff"), resource_kind="buff", tokens=(sim,))
                added = name == "BuffBeganEvent"
                self.recorder.change([actor], "buffs", None if added else value, value if added else None,
                                     self.adapter.clock(), "TestEvent." + name,
                                     cause=sources.cause() if sources is not None else None)
            elif name in ("AddRelationshipBit", "RemoveRelationshipBit"):
                target_id = resolver.get_resolved_arg("target_sim_id")
                info = self.adapter.services.sim_info_manager().get(target_id)
                other = info.get_sim_instance() if info else None
                if info is not None:
                    from relationships.relationship_enums import RelationshipDirection
                    bit = resolver.get_resolved_arg("relationship_bit")
                    value = self.adapter.resource(bit, resource_kind="relbit", tokens=(sim, other))
                    added = name == "AddRelationshipBit"
                    event = self.recorder.relationship_bit(actor, self.adapter.event_reference(info), value, added,
                                                    self.adapter.clock(), "TestEvent." + name,
                                                    bit.directionality == RelationshipDirection.BIDIRECTIONAL,
                                                    cause=sources.cause() if sources is not None else None)
        except Exception:
            self.fail(traceback.format_exc())

    def state_changed(self, args, kwargs, result):
        if self.closed or self.recorder.paused:
            return
        component, state, old, new = args[:4]
        owner = component.owner
        sources = getattr(self, "sources", None)
        if sources is not None and any(frame["from_load"] for frame in sources.frames):
            return
        if not getattr(owner, "is_sim", False) and self.adapter.common_state(state) and self.adapter.in_scope(owner):
            self.recorder.change([self.adapter.reference(owner)], "object_states." + str(state.guid64),
                                 self.adapter.resource(old, resource_kind="object_state"), self.adapter.resource(new, resource_kind="object_state"),
                                  self.adapter.clock(), "StateComponent._trigger_on_state_changed",
                                  cause=sources.cause() if sources is not None else None)

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
                return
            now = self.adapter.clock()
            if getattr(self, "autonomy", None) is not None:
                self.autonomy.poll()
            objects = self.adapter.live_objects()
            if len(objects) > self.config["max_entities"]:
                raise RuntimeError("Entity enumeration exceeds configured read budget")
            local = {self.adapter.reference(obj)["key"]: obj for obj in objects if self.adapter.in_scope(obj)}
            for key in set(self.known) - set(local):
                self.recorder.leave(self.known[key], now)
            for key in set(local) - set(self.known):
                reference = self.adapter.reference(local[key])
                self.recorder.enter(reference, now)
                if getattr(local[key], "is_sim", False):
                    for item in self.adapter.interaction_objects(local[key]):
                        phase = "observed_running" if enum_name(item.pipeline_progress) == "RUNNING" else "observed"
                        self.capture(phase, item, "scope_entry_snapshot")
            self.known = {key: self.adapter.reference(obj) for key, obj in local.items()}
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
                "inspector": ({"state": "failed", "error": self.inspector_error} if self.inspector_error else
                              self.inspector.status() if self.inspector is not None else {"state": "disabled"})}

    def export(self, kind="sim", identifier="active", limit=50, internal=False, fields=None,
               representation="both", include_history=True, history_query=None):
        if self.closed:
            raise RuntimeError("This recording run is closed")
        packet = self.collector.collect(kind, identifier, fields, limit, include_internal=internal,
                                        representation=representation, include_history=include_history, history_query=history_query)
        path = self.writer.export(packet)
        return {"request_id": packet["request_id"], "path": path, "status": "queued", "packet_status": packet["status"]}

    def export_nearby(self, identifier="active", radius="8", kinds="sim", same_level=True,
                      same_room=False, limit=32, metric="horizontal"):
        from context_overlay import api
        packet = api.get_nearby_entities(identifier,
            kinds=["sim", "object"] if kinds == "all" else kinds.split(","),
            radius=None if radius == "room" else float(radius), metric=metric,
            same_level=same_level, same_room=True if radius == "room" else same_room,
            limit=limit, expected_session_id=self.session_id)
        return {"request_id": packet["request_id"], "path": self.writer.export(packet),
                "status": "queued", "packet_status": packet["status"], "count": packet["count"],
                "matched_count": packet["matched_count"], "truncated": packet["truncated"],
                "coverage": packet["coverage"]}

    def history(self, kind="sim", identifier="active", limit=50, internal=False):
        if self.closed:
            raise RuntimeError("This recording run is closed")
        target = self.resolve_history(kind, identifier)
        packet = envelope("history", self.session_id)
        packet.update({"request_id": new_id(), "target": target,
                       "provenance": self.provenance,
                       "history": self.recorder.history(target["key"], limit, internal)})
        if self.config["semanticizer_enabled"]:
            from context_overlay.semanticizer import render
            packet["rendered"] = render(packet)
        return {"request_id": packet["request_id"], "path": self.writer.export(packet), "status": "queued"}

    def _export_history_page(self, page):
        packet = envelope("history", self.session_id)
        packet.update({"request_id": new_id(), "target": page["target"],
                       "provenance": self.provenance, "history": page})
        if self.config["semanticizer_enabled"]:
            from context_overlay.semanticizer import render
            packet["rendered"] = render(packet)
        return {"request_id": packet["request_id"], "path": self.writer.export(packet), "status": "queued",
                "cursor": page["cursor"], "next_cursor": page["next_cursor"], "has_more": page["has_more"],
                "total_matches": page["total_matches"]}

    def history_query(self, kind="sim", identifier="active", **filters):
        if self.closed:
            raise RuntimeError("This recording run is closed")
        target = self.resolve_history(kind, identifier)
        return self._export_history_page(self.recorder.query_history(target["key"], target=target, **filters))

    def resolve_history(self, kind, identifier):
        key = "{}:{}".format(kind, identifier)
        reference = self.recorder.references.get(key)
        if reference is not None:
            from context_overlay.model import copy_data
            return copy_data(reference)
        return self.adapter.resolve(kind, identifier)

    def history_next(self, cursor):
        if self.closed:
            raise RuntimeError("This recording run is closed")
        return self._export_history_page(self.recorder.history_page(cursor))

    def history_close(self, cursor):
        self.recorder.close_query(cursor)
        return {"closed": True}

    def stop(self, reason):
        if self.closed:
            return
        self.api_ready = False
        self.closed = True
        errors = []
        sources = getattr(self, "sources", None)
        autonomy = getattr(self, "autonomy", None)

        def attempt(label, action):
            try:
                action()
            except Exception as exc:
                errors.append("{}: {}: {}".format(label, type(exc).__name__, exc))

        if autonomy is not None:
            attempt("close_autonomy", autonomy.close)
        attempt("session_end", lambda: self.recorder.note(
            "session_end", {"reason": reason, "status": self.recorder.status(),
                            "event_coverage": sources.status() if sources else {},
                            "autonomy": autonomy.status() if autonomy else {},
                            "event_diagnostics": sources.diagnostics() if sources else {}}, self.adapter.clock()))
        if self.inspector is not None:
            attempt("close_inspector", self.inspector.close)
        attempt("close_queries", self.recorder.close_queries)
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
        # Teardown is an explicit boundary, never a per-frame callback. Drain
        # within the writer's bounded timeout before the process can exit.
        attempt("close_writer", lambda: self.writer.close(wait=True))
        writer_error = self.writer.status().get("error")
        if writer_error:
            errors.append(writer_error)
        # Retain failed writers' pending data; never throw it away during teardown.
        _retired.append(self.writer)
        if errors:
            self.fail("Run cleanup failed: " + "; ".join(errors))
        log("RUN STOPPED {}: {}".format(self.session_id, reason))


def start(*_):
    global _runtime, _startup_error
    if _runtime is not None and not _runtime.closed:
        _runtime.stop("new_load")
    _runtime = None
    _startup_error = None
    try:
        _runtime = Runtime()
        _runtime.install()
        _runtime.api_ready = not _runtime.closed
    except Exception:
        _startup_error = traceback.format_exc()
        log("START FAILED: " + _startup_error)
        if _runtime is not None:
            _runtime.fail(_startup_error)
            _runtime.stop("startup_failure")
    return _runtime is not None and not _runtime.closed and _startup_error is None


def stop(*_):
    if _runtime is not None:
        _runtime.stop("zone_teardown")


def initialize():
    global _lifecycle_hooks
    if _lifecycle_hooks is not None:
        return
    import services
    import sims4.commands
    from zone import Zone
    _lifecycle_hooks = Hooks(log)
    _lifecycle_hooks.after(Zone, "on_loading_screen_animation_finished", lambda args, kwargs, result: start())
    _lifecycle_hooks.before(Zone, "on_teardown", lambda args, kwargs: stop())

    @sims4.commands.Command("co.status", command_type=sims4.commands.CommandType.Live)
    def status_command(_connection=None):
        state = _runtime.status() if _runtime else {"state": "waiting_for_zone"}
        if _startup_error:
            state.update({"state": "startup_failed", "error": _startup_error})
        sims4.commands.CheatOutput(_connection)(json.dumps(state))

    @sims4.commands.Command("co.export", command_type=sims4.commands.CommandType.Live)
    def export_command(kind: str="sim", identifier: str="active", limit: int=50, internal: bool=False,
                       representation: str="both", history: bool=True, fields: str="all", _connection=None):
        output = sims4.commands.CheatOutput(_connection)
        try:
            selected = None if fields == "all" else fields.split(",")
            output(json.dumps(_runtime.export(kind, identifier, limit, internal, selected, representation, history), ensure_ascii=False))
        except Exception as exc:
            output("ContextOverlay export failed: " + str(exc))

    @sims4.commands.Command("co.nearby", command_type=sims4.commands.CommandType.Live)
    def nearby_command(identifier: str="active", radius: str="8", kinds: str="sim",
                       same_level: bool=True, same_room: bool=False, limit: int=32,
                       metric: str="horizontal", _connection=None):
        output = sims4.commands.CheatOutput(_connection)
        try:
            if _runtime is None:
                raise RuntimeError("Wait for the zone to load")
            result = _runtime.export_nearby(identifier, radius, kinds, same_level, same_room, limit, metric)
            output(json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            output("ContextOverlay nearby query failed: " + str(exc))

    @sims4.commands.Command("co.history", command_type=sims4.commands.CommandType.Live)
    def history_command(kind: str="sim", identifier: str="active", limit: int=50,
                        internal: bool=False, _connection=None):
        output = sims4.commands.CheatOutput(_connection)
        try:
            output(json.dumps(_runtime.history(kind, identifier, limit, internal), ensure_ascii=False))
        except Exception as exc:
            output("ContextOverlay history failed: " + str(exc))

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
        output = sims4.commands.CheatOutput(_connection)
        try:
            values = lambda value: None if value == "all" else value.split(",")
            result = _runtime.history_query(kind, identifier, page_size=page_size, include_internal=internal,
                                            time_field=time_field,
                                            from_ticks=None if from_ticks == "none" else from_ticks,
                                            to_ticks=None if to_ticks == "none" else to_ticks,
                                            event_types=values(event_types), fields=values(fields),
                                            outcomes=values(outcomes), tuning_ids=values(tuning_ids), order=order)
            output(json.dumps(result, ensure_ascii=False))
        except Exception as exc:
            output("ContextOverlay history query failed: " + str(exc))

    @sims4.commands.Command("co.history_next", command_type=sims4.commands.CommandType.Live)
    def history_next_command(cursor: str, _connection=None):
        output = sims4.commands.CheatOutput(_connection)
        try:
            output(json.dumps(_runtime.history_next(cursor), ensure_ascii=False))
        except Exception as exc:
            output("ContextOverlay history page failed: " + str(exc))

    @sims4.commands.Command("co.history_close", command_type=sims4.commands.CommandType.Live)
    def history_close_command(cursor: str, _connection=None):
        output = sims4.commands.CheatOutput(_connection)
        try:
            output(json.dumps(_runtime.history_close(cursor)))
        except Exception as exc:
            output("ContextOverlay history close failed: " + str(exc))

    log("LOADED version={} python={} roots={}".format(VERSION, sys.version, sys.path))
    zone = services.current_zone()
    if zone is not None and zone.is_zone_running:
        start()
