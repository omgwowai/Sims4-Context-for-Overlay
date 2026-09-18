"""Opt-in local validation driver. Disabled in the default configuration.

Accepts a bounded set of game operations, never arbitrary Python or shell code.
Actions are explicitly labelled in the observation journal.
"""

import json
import os
import time
import traceback

from context_overlay.model import utc_now


class Driver:
    def __init__(self, runtime):
        self.runtime = runtime
        self.directory = runtime.directory.parent.parent / "validation"
        self.directory.mkdir(exist_ok=True)
        consumed = self.directory / "consumed.json"
        self.last_request = json.loads(consumed.read_text(encoding="utf-8"))["request_id"] if consumed.exists() else None
        self.probe = None

    def close(self):
        if self.probe and self.probe["hooks"] is not None:
            self.probe["hooks"].remove()
            self.probe["hooks"] = None

    def frame_probe(self, seconds=None):
        """Opt-in bounded simulation-update timing; no claim about render FPS."""
        if seconds is not None:
            if type(seconds) is not int or not 1 <= seconds <= 60:
                raise ValueError("Probe seconds must be 1 through 60")
            self.close()
            import zone
            from context_overlay.hooks import Hooks
            probe = {"until": time.perf_counter() + seconds, "previous": None, "samples": [], "error": None}
            hooks = Hooks(lambda message: probe.update(error=message))
            probe["hooks"] = hooks
            def updated(args, kwargs, result):
                now = time.perf_counter()
                if now > probe["until"] or len(probe["samples"]) >= 10000:
                    return
                if probe["previous"] is not None:
                    probe["samples"].append((now - probe["previous"]) * 1000)
                probe["previous"] = now
            hooks.after(zone.Zone, "update", updated)
            self.probe = probe
        if self.probe is None:
            return {"state": "not_started"}
        rows = sorted(self.probe["samples"])
        complete = time.perf_counter() >= self.probe["until"]
        if complete:
            self.close()
        return {"state": "complete" if complete else "measuring", "samples": len(rows), "error": self.probe["error"],
                "basis": "Zone.update wall-clock intervals, not render FPS",
                "interval_ms": {key: round(rows[min(len(rows) - 1, int(len(rows) * q))], 3) if rows else None
                                for key, q in (("p50", .5), ("p95", .95), ("p99", .99), ("max", 1))}}

    def write(self, name, payload):
        destination = self.directory / name
        pending = destination.with_suffix(".pending")
        pending.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(str(pending), str(destination))

    def poll(self):
        if self.probe and self.probe["hooks"] is not None and time.perf_counter() >= self.probe["until"]:
            self.close()
        path = self.directory / "request.json"
        if not path.exists():
            return
        request = json.loads(path.read_text(encoding="utf-8-sig"))
        request_id = request["request_id"]
        if request_id == self.last_request:
            return
        self.last_request = request_id
        # Mark receipt before execution: restarting a run must not replay its
        # own restart command or repeat a previous state-changing operation.
        self.write("consumed.json", {"request_id": request_id})
        response = {"request_id": request_id, "recorded_at": utc_now(), "session_id": self.runtime.session_id}
        started = time.perf_counter()
        try:
            response["result"] = self.execute(request)
            response["ok"] = True
        except Exception:
            response["ok"] = False
            response["error"] = traceback.format_exc()
        response["execution_ms"] = round((time.perf_counter() - started) * 1000, 3)
        self.write("response.json", response)

    def execute(self, request):
        runtime = self.runtime
        adapter = runtime.adapter
        services = adapter.services
        operation = request["operation"]
        if operation == "frame_probe":
            return self.frame_probe(request.get("seconds"))
        if operation == "status":
            return runtime.status()
        if operation in ("api_info", "api_context", "api_history", "api_append", "api_changes", "api_page", "api_close",
                         "api_view", "api_view_status", "api_view_page", "api_view_explain", "api_view_close"):
            from context_overlay import api
            methods = {"api_info": "get_api_info", "api_context": "get_context", "api_history": "query_history",
                       "api_append": "append_event", "api_changes": "read_event_changes",
                       "api_page": "get_history_page", "api_close": "close_history",
                       "api_view": "query_event_view", "api_view_status": "get_event_view_status",
                       "api_view_page": "get_event_view_page", "api_view_explain": "explain_event_view",
                       "api_view_close": "close_event_view"}
            return getattr(api, methods[operation])(**request.get("params", {}))
        if operation == "entities":
            results = []
            text = request.get("match", "").lower()
            for obj in adapter.live_objects():
                if adapter.in_scope(obj):
                    reference = adapter.reference(obj)
                    if not text or text in json.dumps(reference, ensure_ascii=False).lower():
                        results.append(reference)
            return results[:500]
        if operation == "export":
            return runtime.export(request.get("kind", "sim"), request.get("id", "active"),
                                  int(request.get("limit", 50)), request.get("internal", False), request.get("fields"),
                                  request.get("representation", "both"), request.get("history", True))
        if operation == "history":
            return runtime.history(request.get("kind", "sim"), request.get("id", "active"),
                                   int(request.get("limit", 50)), request.get("internal", False))
        if operation == "history_query":
            return runtime.history_query(request.get("kind", "sim"), request.get("id", "active"), **request.get("filters", {}))
        if operation in ("history_next", "history_close"):
            return getattr(runtime, operation)(request["cursor"])
        if operation == "marker":
            runtime.recorder.note("validation_marker", {"label": request["label"]}, adapter.clock())
            return {"marked": request["label"]}
        if operation == "catalog":
            import sims4.resources
            allowed = {name: name for name in
                       ("INTERACTION", "RECIPE", "BUFF", "STATISTIC", "OBJECT_STATE", "RELATIONSHIP_BIT")}
            # EA keeps state types and state values in the same manager.
            allowed["OBJECT_STATE_VALUE"] = "OBJECT_STATE"
            kind = request.get("type", "INTERACTION")
            manager = services.get_instance_manager(getattr(sims4.resources.Types, allowed[kind]))
            text = request.get("match", "").lower()
            results = [adapter.resource(value) for value in manager.types.values()
                       if text in getattr(value, "__name__", "").lower()]
            return results[:200]
        if operation == "speed":
            from clock import ClockSpeedMode, GameSpeedChangeSource
            speed = int(request["value"])
            if speed not in (0, 1, 2, 3):
                raise ValueError("Invalid game speed")
            runtime.recorder.note("validation_action", request, adapter.clock())
            game_clock = services.game_clock_service()
            game_clock.set_clock_speed(ClockSpeedMode(speed), source=GameSpeedChangeSource.GAMEPLAY,
                                       reason="ContextOverlay validation", immediate=True)
            return {"speed_requested": speed, "observed_speed": int(game_clock.clock_speed)}
        if operation == "restart":
            from context_overlay.game_runtime import start
            if not start():
                raise RuntimeError("New run failed to start; inspect runtime.log")
            return {"new_run_started": True}
        if operation not in ("push", "cancel", "set_need", "add_buff", "remove_buff", "relationship_score", "set_state"):
            raise ValueError("Unknown validation operation")
        runtime.recorder.note("validation_action", request, adapter.clock())
        target = adapter.resolve(request.get("kind", "sim"), request.get("id", "active"))
        obj = adapter.object_for(target)
        if not adapter.in_scope(obj):
            raise ValueError("Validation target outside active lot")
        import sims4.resources
        if operation == "push":
            from interactions.context import InteractionContext
            from interactions.priority import Priority
            affordance = services.get_instance_manager(sims4.resources.Types.INTERACTION).get(int(request["affordance_id"]))
            destination = adapter.object_for(adapter.resolve(request.get("target_kind", "object"), request["target_id"]))
            if not adapter.in_scope(destination):
                raise ValueError("Interaction target outside active lot")
            context = InteractionContext(obj, InteractionContext.SOURCE_SCRIPT, Priority.High)
            if request.get("recipe_id"):
                raise ValueError("Select the recipe through the game's picker; recipe_id is not a push argument")
            result = obj.push_super_affordance(affordance, destination, context)
            return {"accepted": bool(result), "detail": str(result)}
        if operation == "cancel":
            from interactions.interaction_finisher import FinishingType
            candidates = list(obj.si_state) + list(obj.queue)
            current = getattr(obj.queue, "running", None)
            if current is not None:
                candidates.append(current)
            selected = next(item for item in candidates if str(item.id) == str(request["interaction_id"]))
            return {"accepted": bool(selected.cancel(FinishingType.USER_CANCEL, cancel_reason_msg="ContextOverlay validation cancellation"))}
        if operation == "set_need":
            from context_overlay.ea_adapter import NEEDS
            stat_type = adapter.statistics[NEEDS[request["need"]]]
            stat = obj.sim_info.commodity_tracker.get_statistic(stat_type, add=False)
            if stat is None:
                raise ValueError("Need statistic not instantiated")
            before = stat.get_value()
            stat.set_value(float(request["value"]))
            return {"before": before, "after": stat.get_value()}
        if operation in ("add_buff", "remove_buff"):
            buff = services.get_instance_manager(sims4.resources.Types.BUFF).get(int(request["buff_id"]))
            if operation == "add_buff":
                accepted = obj.sim_info.Buffs.add_buff_from_op(buff)
                return {"accepted": bool(accepted), "present": obj.sim_info.Buffs.has_buff(buff)}
            obj.sim_info.Buffs.remove_buff_by_type(buff)
            return {"removed": not obj.sim_info.Buffs.has_buff(buff)}
        if operation == "relationship_score":
            from context_overlay.ea_adapter import RELATIONSHIP_TRACKS
            other = adapter.object_for(adapter.resolve("sim", request["target_id"]))
            if not adapter.in_scope(other):
                raise ValueError("Relationship target outside active lot")
            track = adapter.statistics[RELATIONSHIP_TRACKS[request.get("track", "friendship")]]
            obj.sim_info.relationship_tracker.set_relationship_score(other.sim_info.sim_id, float(request["value"]), track=track)
            return {"value": obj.sim_info.relationship_tracker.get_relationship_score(other.sim_info.sim_id, track=track)}
        value = services.get_instance_manager(sims4.resources.Types.OBJECT_STATE).get(int(request["value_id"]))
        if not adapter.common_state(value.state) or not obj.has_state(value.state):
            raise ValueError("State is not part of the supported profile")
        before = adapter.resource(obj.get_state(value.state))
        obj.set_state(value.state, value)
        return {"before": before, "after": adapter.resource(obj.get_state(value.state))}
