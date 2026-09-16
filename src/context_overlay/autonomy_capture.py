"""Observe native Autonomy choices, committing only at the execution boundary.

No tests, scoring methods, random functions or lazy roll getters are replayed.
Game imports are deferred; the same adapters can be exercised with bytecode probes.
"""

import importlib
import inspect
import sys
import time
from collections import Counter
from types import SimpleNamespace

from context_overlay.autonomy import PendingDecisions, number, selection_stage
from context_overlay.ea_adapter import enum_name
from context_overlay.event_policy import internal_interaction
from context_overlay.hooks import Hooks
from context_overlay.model import copy_data


SCORE_FIELDS = (
    "efficiency", "duration", "autonomy_scoring_preference", "rel_utility_score",
    "relationship_object_value", "party_score", "must_change_posture", "change_posture_cost",
    "base_multitasking_percentage", "bonus_multitasking_percentage", "penalty_multitasking_percentage",
    "outside_multiplier", "waiting_in_line_multiplier", "club_rule_multiplier", "business_rule_multiplier",
    "buff_utility_score", "tested_relationship_utility_score", "situation_type_utility_score",
    "sit_posture_transition_penalty", "route_time", "estimated_distance", "remaining_sis_score",
    "interaction_score_modifier_multiplier", "off_lot_object_score_multiplier", "ensemble_multiplier",
    "exclusive_situations_multiplier", "custom_schedule_multiplier", "dynasty_rule_multiplier")
COMMODITY_FIELDS = ("score", "advertise", "commodity_value", "interval", "fulfillment_rate",
                    "object_stat_use_multiplier", "already_solving_motive_multiplier", "modified_desire")
SCORE_MAPS = ("opportunity_costs", "attention_cost_scores", "attention_cost_bonus_scores", "attention_cost_penalty_scores")


def arg(args, kwargs, index, name, default=None):
    return args[index] if len(args) > index else kwargs.get(name, default)


def index_is(items, selected):
    return next((i for i, item in enumerate(items) if item is selected), None)


def selector_locals(code):
    """Read only an executing verified selector, then release every frame ref.

    The lazy multitask getter is the last common call before weighted/uniform/
    deterministic branching. Its caller contains the actual top_options pool.
    Missing/changed locals degrade coverage instead of reproducing EA logic.
    """
    frame = sys._getframe(1)
    try:
        for _ in range(12):
            if frame is None:
                break
            if frame.f_code is code:
                return {key: frame.f_locals.get(key) for key in
                        ("top_options", "autonomy_request", "interaction_mixer_group_weight")}
            frame = frame.f_back
        return {}
    finally:
        del frame


class AutonomyCapture:
    def __init__(self, runtime):
        self.runtime, self.adapter, self.recorder = runtime, runtime.adapter, runtime.recorder
        config = runtime.config
        self.limit = config.get("autonomy_top_n", 5)
        half_budget = config.get("autonomy_pending_memory_mb", 8) * 1024 * 1024 // 2
        self.pending = PendingDecisions(config.get("autonomy_pending_capacity", 256),
            half_budget,
            config.get("autonomy_pending_ttl_seconds", 600))
        self.evidence = PendingDecisions(config.get("autonomy_pending_capacity", 256), half_budget,
            config.get("autonomy_pending_ttl_seconds", 600))
        self.hooks = Hooks(self.error)
        self.roll_hooks = Hooks(self.error)
        self.roll_methods = set()
        self.chains, self.choices, self.mixers = [], [], []
        self.counts, self.coverage = Counter(), {}
        self.sequence = 0
        self.callback_ms = self.callback_max_ms = 0.0
        self.enabled = False
        self.archiver = None
        self.archiver_owned = False
        self.last_error = None

    def error(self, message):
        self.counts["observation_errors"] += 1
        self.last_error = str(message)[:512]

    def uid(self, kind):
        self.sequence += 1
        return "{}:autonomy:{}:{}".format(self.runtime.session_id, kind, self.sequence)

    def active(self, sim):
        return (self.enabled and not self.runtime.closed and not self.recorder.paused
                and self.adapter.in_scope(sim))

    def install(self):
        if not self.runtime.config.get("autonomy_enabled", True):
            self.coverage["state"] = "disabled"
            return
        try:
            service = importlib.import_module("autonomy.autonomy_service").AutonomyService
            component = importlib.import_module("autonomy.autonomy_component").AutonomyComponent
            modes = importlib.import_module("autonomy.autonomy_modes")
            providers = importlib.import_module("autonomy.autonomy_mixer_provider_scoring")._MixerProviderScoring
            random = importlib.import_module("random")
            sim_random = importlib.import_module("sims4.random")
            queue = importlib.import_module("interactions.interaction_queue").InteractionQueue
            aop = importlib.import_module("interactions.aop").AffordanceObjectPair
            interaction = importlib.import_module("interactions.base.interaction").Interaction
            elements = importlib.import_module("elements")
            self.keys = importlib.import_module("autonomy.autonomy_gsi_enums").GSIDataKeys
            self.autonomy_source = importlib.import_module("interactions.context").InteractionContext.SOURCE_AUTONOMY
            self.selector_code = inspect.unwrap(service.choose_best_interaction).__code__
            self.mixer_code = inspect.unwrap(modes._MixerAutonomy._run_gen).__code__
            self.archiver = importlib.import_module("gsi_handlers.autonomy_handlers").archiver
            self.hooks.around(service, "_select_best_result", self.begin_chain, self.end_chain)
            self.hooks.around(service, "choose_best_interaction", self.begin_choice, self.end_choice)
            self.hooks.after(sim_random, "weighted_random_index", self.weighted)
            self.hooks.after(random, "randint", self.uniform)
            self.hooks.before(component, "_should_run_cached_interaction", self.cached)
            self.hooks.after(queue, "append", self.queued)
            self.hooks.around(elements.GeneratorElementBase, "_run", self.immediate_before, self.immediate_after)
            self.hooks.around(aop, "execute_interaction", self.execute_before, self.execute_after)
            self.hooks.before(interaction, "invalidate", self.invalidated)
            self.hooks.generator(modes._MixerAutonomy, "_run_gen", self.mixer_create,
                self.mixers.append, self.mixer_leave, self.mixer_finish)
            self.hooks.around(providers, "get_mixer_provider", self.provider_before, self.provider_after)
            self.hooks.after(sim_random, "weighted_random_item", self.group_selected)
            self.hooks.around(modes._MixerAutonomy, "_create_and_score_mixers", self.mixer_scores_before, self.mixer_scores_after)
            # Ownership is relinquished if another consumer toggles this archive.
            if not self.archiver.enabled:
                self.archiver.archive_enable_fn(enableLog=True)
                self.archiver_owned = True
            self.hooks.after(type(self.archiver), "archive_enable_fn", self.archive_toggled)
            self.enabled = True
            self.coverage.update(state="installed", selection="native_calls", queue="append_return_value",
                immediate="GeneratorElementBase._run", mixer="provider_group_and_action",
                scoring="GSI_InteractionScore_and_mixer_data", rejection="GSI_reason_templates_and_object_status_counts",
                multitasking_roll="pending_sim_type", multitasking_hooked_types=0)
        except Exception as exc:
            self.error("install: {}: {}".format(type(exc).__name__, exc))
            self.close()
            self.coverage["state"] = "unavailable"

    def archive_toggled(self, args, kwargs, result):
        if args[0] is self.archiver:
            self.archiver_owned = False

    def close(self):
        self.enabled = False
        self.roll_hooks.remove()
        self.roll_methods.clear()
        self.hooks.remove()
        self.pending.clear()
        self.evidence.clear()
        self.chains[:], self.choices[:], self.mixers[:] = [], [], []
        if self.archiver_owned and self.archiver is not None:
            self.archiver.archive_enable_fn(enableLog=False)
        self.archiver_owned = False

    def status(self):
        return {"enabled": self.enabled, "coverage": dict(self.coverage), "counts": dict(self.counts),
                "buffer": self.pending.status(), "evidence_buffer": self.evidence.status(), "last_error": self.last_error,
                "gsi_enabled": bool(self.archiver.enabled) if self.archiver is not None else None,
                "callback_ms": self.callback_ms, "callback_max_ms": self.callback_max_ms,
                "timing_scope": "selection_serialization_only_excludes_GSI_generation"}

    def poll(self):
        self.pending.sweep(self.adapter.in_scope)
        self.evidence.sweep()

    def scalar(self, value):
        if value is None or isinstance(value, (str, bool)):
            return value[:1024] if isinstance(value, str) else value
        return number(value)

    def identity(self, item):
        if item is None:
            return None
        # Resource names can be resolved offline without invoking gameplay accessors.
        affordance = getattr(item, "affordance", item)
        resource = {"id": str(getattr(affordance, "guid64", "")) or None,
                    "tuning_name": getattr(affordance, "__name__", type(affordance).__name__),
                    "resource_kind": "interaction" if hasattr(item, "affordance") else None}
        if hasattr(item, "affordance"):
            resource["name"] = self.adapter.interaction_name(item, "autonomy_candidate_at_selection")
        else:
            resource = self.adapter.resource(affordance)
        return {"interaction_id": str(item.id) if getattr(item, "id", None) is not None else None,
                "action": resource, "target": self.adapter.event_reference(getattr(item, "target", None))}

    def score_detail(self, score):
        values = {field: self.scalar(getattr(score, field, None)) for field in SCORE_FIELDS}
        missing = [field for field, value in values.items() if value is None]
        commodities = getattr(score, "commodity_scores", None)
        if commodities is not None:
            values["commodity_scores"] = []
            for entry in list(commodities)[:128]:
                row = {field: self.scalar(getattr(entry, field, None)) for field in COMMODITY_FIELDS}
                commodity = getattr(entry, "commodity", None)
                row["commodity"] = self.adapter.resource(commodity, resource_kind="statistic")
                row["autonomy_weight"] = self.scalar(getattr(commodity, "autonomy_weight", 1))
                values["commodity_scores"].append(row)
            if len(commodities) > 128:
                missing.append("commodity_scores_truncated")
        else:
            missing.append("commodity_scores")
        for field in SCORE_MAPS:
            entries = getattr(score, field, None)
            if entries is None:
                missing.append(field)
                continue
            values[field] = [{"subject": self.identity(key),
                "value": [self.scalar(v) for v in value] if isinstance(value, (tuple, list)) else self.scalar(value)}
                for key, value in list(entries.items())[:128]]
            if len(entries) > 128:
                missing.append(field + "_truncated")
        return {"source": "InteractionScore", "values": values, "missing": missing,
                "engine_gsi_consistency_warning": bool(getattr(score, "HAS_SHOWN_GSI_OUT_OF_DATE_ERROR", False))}

    def gsi(self, request, key):
        data = getattr(request, "gsi_data", None)
        return data.get(getattr(self.keys, key), ()) if data else ()

    def filters(self, request):
        counts, success = Counter(), 0
        affordances = self.gsi(request, "AFFORDANCE_KEY")
        for row in affordances:
            if hasattr(row, "_reason"):
                # The format template excludes per-object args and bounds cardinality.
                reason = getattr(row, "_reason", None)
                counts[(str(getattr(row, "stage", "unknown")), str(reason)[:512])] += 1
            else:
                success += 1
        object_status = Counter(str(row[2])[:512] for row in self.gsi(request, "OBJECTS_KEY"))
        return {"coverage": "observed_GSI_only" if getattr(request, "gsi_data", None) is not None else "GSI_unavailable",
                "evaluated_affordance_rows": len(affordances), "scored_affordance_rows": success,
                "rejections": [{"stage": key[0], "reason_template": key[1], "count": value}
                               for key, value in counts.most_common(128)],
                "unlisted_rejection_rows": sum(value for _, value in counts.most_common()[128:]),
                "object_status_counts": dict(object_status.most_common(128)),
                "unlisted_object_rows": sum(value for _, value in object_status.most_common()[128:]),
                "not_observed": ["unenumerated_candidates", "individual_failed_candidates", "pre_GSI_test_details"]}

    def new_chain(self, request):
        upstream = self.evidence.pop(request)
        if upstream:
            stages, attempts = copy_data(upstream["stages"]), upstream["attempts"]
        else:
            stages, attempts = [], 0
        data = {"decision_id": self.uid("decision"), "selection_time": self.adapter.clock(),
                "mode": str(getattr(request, "autonomy_mode_label", type(request.autonomy_mode).__name__)),
                "context_source": enum_name(request.context.source),
                "is_script_request": bool(getattr(request, "is_script_request", False)),
                "stages": stages, "filters": self.filters(request),
                "mixer_rejected_attempts": max(0, attempts - 1), "top_n": self.limit}
        self.counts["selection_rounds"] += 1
        return {"request": request, "data": data}

    def begin_chain(self, args, kwargs):
        request = arg(args, kwargs, 2, "autonomy_request")
        if not self.active(getattr(request, "sim", None)):
            return None
        context = self.new_chain(request)
        self.chains.append(context)
        return context

    def end_chain(self, context, args, kwargs, result, error):
        if context is None:
            return
        if context in self.chains:
            self.chains.remove(context)
        if error is None and result is not None:
            self.stage_pending(context, result)
        else:
            self.counts["no_selected_result"] += 1

    def stage_pending(self, context, interaction):
        data = context["data"]
        if not data["stages"]:
            self.counts["missing_selection_stages"] += 1
            return
        data["selected"] = self.identity(interaction)
        data["actor"] = self.adapter.reference(interaction.sim)
        data["interaction_event_id"] = "{}:interaction:{}:{}".format(
            self.runtime.session_id, data["actor"]["id"], interaction.id)
        technical = internal_interaction(getattr(interaction, "guid64", None), type(interaction).__name__)
        technical = technical or data["context_source"] in ("POSTURE_GRAPH", "SOCIAL_ADJUSTMENT", "GET_COMFORTABLE",
            "BODY_CANCEL_AOP", "CARRY_CANCEL_AOP", "VEHCILE_CANCEL_AOP")
        data["tier"] = "main" if getattr(interaction, "is_super", False) and not technical else "internal"
        if self.pending.put(interaction, data):
            self.counts["staged"] += 1

    def begin_choice(self, args, kwargs):
        request = arg(args, kwargs, 2, "autonomy_request")
        if not self.active(getattr(request, "sim", None)):
            return None
        roll_hook = self.ensure_roll_hook(request.sim)
        chain = self.chains[-1] if self.chains and self.chains[-1]["request"] is request else None
        standalone = chain is None
        chain = chain or self.new_chain(request)
        inputs = arg(args, kwargs, 1, "scored_interactions")
        inputs = list(inputs) if isinstance(inputs, (list, tuple, set)) else []
        probabilities = self.gsi(request, "PROBABILITY_KEY")
        context = {"chain": chain, "standalone": standalone, "inputs": inputs,
            "pool": None, "mode": None, "selected_index": None, "roll": None,
            "roll_hook": roll_hook,
            "probability_table": probabilities, "probability_start": len(probabilities),
            "prefix": arg(args, kwargs, 5, "interaction_prefix", "")}
        self.choices.append(context)
        return context

    def ensure_roll_hook(self, sim):
        """Observe the actual exported method before EA enters this selector.

        ComponentMetaclass exports to type(owner), retaining the original
        component function. Base Sim need not have it, and types may appear
        after installation. Static inspection never invokes the lazy getter.
        """
        try:
            owner = type(sim)
            method = inspect.getattr_static(owner, "get_multitasking_roll")
            if method not in self.roll_methods:
                if not (inspect.isfunction(method) or isinstance(method, (staticmethod, classmethod))):
                    raise TypeError("unsupported get_multitasking_roll descriptor")
                self.roll_hooks.after(owner, "get_multitasking_roll", self.roll)
                self.roll_methods.add(inspect.getattr_static(owner, "get_multitasking_roll"))
                self.coverage["multitasking_hooked_types"] = len(self.roll_hooks.entries)
            # Inherited wrappers are already observed; do not stack another.
            if self.coverage.get("multitasking_roll") != "partial":
                self.coverage["multitasking_roll"] = "native_sim_type_method"
            return "installed"
        except Exception as exc:
            self.coverage["multitasking_roll"] = "partial"
            self.counts["multitasking_hook_unavailable"] += 1
            self.error("multitasking hook: {}: {}".format(type(exc).__name__, exc))
            # Weighted/uniform calls or native GSI may still supply evidence.
            return "unavailable"

    def roll(self, args, kwargs, result):
        if not self.choices:
            return
        current = self.choices[-1]
        local = selector_locals(self.selector_code)
        if local.get("autonomy_request") is current["chain"]["request"]:
            pool = local.get("top_options")
            if isinstance(pool, (list, tuple)):
                current["pool"] = list(pool)
                current["roll"] = number(result)

    def weighted(self, args, kwargs, result):
        if not self.choices:
            return
        current = self.choices[-1]
        local = selector_locals(self.selector_code)
        pool = arg(args, kwargs, 0, "pairs")
        if local.get("top_options") is pool:
            current.update(pool=list(pool), selected_index=result, mode="weighted")

    def uniform(self, args, kwargs, result):
        if not self.choices:
            return
        local = selector_locals(self.selector_code)
        if local.get("autonomy_request") is self.choices[-1]["chain"]["request"]:
            pool = local.get("top_options")
            if isinstance(pool, (list, tuple)):
                self.choices[-1].update(pool=list(pool), selected_index=result, mode="uniform")

    def describe_scored(self, scored, context):
        interaction = scored.interaction
        original = next((row for row in context["inputs"] if row.interaction is interaction), scored)
        detail = original.score
        if not hasattr(detail, "commodity_scores"):
            matches = [row.score for row in self.gsi(context["chain"]["request"], "AFFORDANCE_KEY")
                       if hasattr(row, "score") and (getattr(row.score, "interaction", None) is interaction
                       or row.interaction is interaction or row.interaction is getattr(interaction, "aop", None))]
            if len(matches) == 1:
                detail = matches[0]
        row = self.identity(interaction)
        row.update(raw_score=number(original.score), route_time=number(original.route_time),
                   multitasking_percentage=number(original.multitasking_percentage))
        if hasattr(detail, "commodity_scores"):
            row["score_components"] = self.score_detail(detail)
        else:
            mixer = self.evidence.get(getattr(interaction, "aop", None))
            row["score_components"] = (copy_data(mixer) if mixer else
                {"source": "ScoredInteractionData", "values": {}, "missing": ["structured_score_breakdown"]})
        return row

    def end_choice(self, context, args, kwargs, result, error):
        if context is None:
            return
        if context in self.choices:
            self.choices.remove(context)
        if error is not None:
            self.counts["selector_exceptions"] += 1
            return
        started = time.monotonic()
        try:
            request = context["chain"]["request"]
            table = self.gsi(request, "PROBABILITY_KEY")
            rows = list(table[context["probability_start"]:] if table is context["probability_table"] else table)
            pool, mode = context["pool"], context["mode"]
            if mode is None and rows and all(row.probability_type == "Best" for row in rows):
                mode = "deterministic"
            if pool is None:
                # GSI can still prove a random pool; Best only proves its winner.
                if rows and mode != "deterministic":
                    by_id = {id(row.interaction): row for row in context["inputs"]}
                    pool = [SimpleNamespace(score=row.score, interaction=row.interaction,
                        route_time=by_id[id(row.interaction)].route_time,
                        multitasking_percentage=by_id[id(row.interaction)].multitasking_percentage)
                        for row in rows if id(row.interaction) in by_id]
                    if len(pool) != len(rows):
                        pool = None
                    mode = "uniform" if rows[0].probability_type == "Uniform" else "weighted"
            coverage = "actual_selector_pool" if pool is not None else "scored_input_only_pool_unavailable"
            pool = pool if pool is not None else context["inputs"]
            selected = context["selected_index"]
            if selected is None:
                winner = result if result is not None else rows[0].interaction if mode == "deterministic" and rows else None
                selected = index_is([row.interaction for row in pool], winner)
            stage = selection_stage([(row.score, row) for row in pool], selected,
                mode or "unknown", self.limit, lambda row: self.describe_scored(row, context))
            stage.update(stage_id=self.uid("stage"), kind="target" if context["prefix"] else "interaction",
                parent_stage_id=(context["chain"]["data"]["stages"][-1]["stage_id"] if context["chain"]["data"]["stages"] else None),
                selection_time=self.adapter.clock(), input_count=len(context["inputs"]), coverage=coverage)
            if coverage != "actual_selector_pool":
                stage.update(pool_count=None, omitted_probability=None, probability_basis="unavailable")
                for candidate in stage["candidates"]:
                    candidate["probability"] = None
            drawn = pool[selected] if selected is not None else None
            applicable = (not request.is_script_request and request.context.source == self.autonomy_source
                          and bool(drawn and drawn.interaction.is_super)) if drawn is not None else None
            roll = context["roll"] if context["roll"] is not None else number(rows[0].multitask_roll) if rows else None
            stage["multitasking"] = {"roll": roll, "threshold": number(drawn.multitasking_percentage) if drawn else None,
                "applicable": applicable, "passed": result is not None if applicable else None,
                "selector_returned_interaction": result is not None,
                "hook": context["roll_hook"],
                "roll_source": "native_getter" if context["roll"] is not None else "native_gsi" if roll is not None else "unavailable",
                "basis": "native_selector_return_and_observed_roll" if roll is not None else "native_selector_return_roll_unavailable"}
            context["chain"]["data"]["stages"].append(stage)
            if context["standalone"] and result is not None:
                self.stage_pending(context["chain"], result)
        finally:
            elapsed = (time.monotonic() - started) * 1000
            self.callback_ms += elapsed
            self.callback_max_ms = max(self.callback_max_ms, elapsed)

    def commit(self, interaction, gate, when=None):
        if not self.active(getattr(interaction, "sim", None)):
            return
        data = self.pending.pop(interaction)
        if data is None:
            return
        data["commit_time"] = when if when is not None else self.adapter.clock()
        data["retention_gate"] = gate
        data["cache_origin"] = bool(getattr(interaction, "_context_overlay_autonomy_cached", None) == self.runtime.session_id)
        data["cache_evidence"] = "cached_validation_call" if data["cache_origin"] else "no_cached_validation_observed"
        actor = data["actor"]
        event = self.recorder.fact("autonomy.decision", [actor], data, data["commit_time"],
            "AutonomyCapture." + gate, roles=[{"entity_key": actor["key"], "role": "actor", "basis": "selected_interaction_sim"}],
            tier=data["tier"], event_id=data["decision_id"], evidence="observed_selection_and_submission")
        if event:
            interaction._context_overlay_autonomy_decision = (self.runtime.session_id, event["event_id"])
            self.recorder.link_decision(data["interaction_event_id"], event["event_id"])
            self.counts["committed_" + gate] += 1

    def queued(self, args, kwargs, result):
        interaction = arg(args, kwargs, 1, "interaction")
        if self.pending.get(interaction) is not None:
            if result:
                self.commit(interaction, "queue_success")
            else:
                self.pending.discard(id(interaction), "queue_rejected")

    def invalidated(self, args, kwargs):
        self.pending.discard(id(args[0]), "interaction_invalidated")

    def cached(self, args, kwargs):
        interaction = arg(args, kwargs, 1, "interaction_to_run")
        if self.pending.get(interaction) is not None:
            interaction._context_overlay_autonomy_cached = self.runtime.session_id

    def immediate_before(self, args, kwargs):
        element = args[0]
        method = getattr(element, "pending_generator", None)
        interaction = getattr(method, "__self__", None)
        if (self.pending.get(interaction) is not None and getattr(getattr(interaction, "affordance", None), "immediate", False)
                and method == getattr(interaction, "_run_gen", None)):
            return (interaction, self.adapter.clock())

    def immediate_after(self, context, args, kwargs, result, error):
        if context is not None and getattr(args[0], "generator", None) is not None:
            self.commit(context[0], "immediate_entered", context[1])

    def execute_before(self, args, kwargs):
        interaction = arg(args, kwargs, 0, "interaction")
        if self.pending.get(interaction) is not None:
            return interaction

    def execute_after(self, interaction, args, kwargs, result, error):
        if interaction is None:
            return
        token = getattr(interaction, "_context_overlay_autonomy_decision", None)
        if token and token[0] == self.runtime.session_id:
            event = self.recorder.events.get(token[1])
            if event:
                data = copy_data(event["payload"])
                data["execution_result"] = {"returned_success": bool(result) if error is None else None,
                    "exception_type": type(error).__name__ if error is not None else None,
                    "meaning": "immediate_result" if interaction.affordance.immediate else "submission_result"}
                self.recorder.fact("autonomy.decision", event["participants"], data, self.adapter.clock(), event["source"],
                    roles=event["roles"], tier=event["tier"], event_id=event["event_id"], evidence=event["evidence_type"])

    def mixer_create(self, args, kwargs):
        request = args[0]._request
        if self.active(getattr(request, "sim", None)):
            return {"request": request, "stages": [], "attempts": 0}

    def mixer_leave(self, context):
        if context in self.mixers:
            self.mixers.remove(context)

    def mixer_finish(self, context, result, error):
        request = context["request"]
        if error is None and result is not None:
            self.evidence.put(request, {"stages": context["stages"], "attempts": context["attempts"]})
        else:
            self.evidence.pop(request)

    def provider_before(self, args, kwargs):
        if not self.mixers:
            return
        owner = args[0]
        positive, zero = owner._postive_scoring_mixer_providers, owner._zero_scoring_mixer_providers
        pool = [(weight, provider) for provider, weight in (positive or zero).items()]
        return (self.mixers[-1], owner, pool, "weighted" if positive else "uniform")

    def provider_after(self, context, args, kwargs, result, error):
        if context is None or error is not None:
            return
        frame, owner, pool, mode = context
        frame["attempts"] += 1
        frame["stages"] = []
        if result is None:
            return
        def describe(provider):
            native = (owner.gsi_mixer_provider_data or {}).get(provider)
            row = self.identity(getattr(provider, "_mixer_provider", provider))
            row["score_components"] = {"source": "GSIMixerProviderData", "values": {},
                "native_text": str(getattr(native, "_score_details", ""))[:4096],
                "missing": ["structured_provider_contributions"]}
            return row
        stage = selection_stage(pool, index_is([p for _, p in pool], result), mode, self.limit, describe)
        stage.update(stage_id=self.uid("stage"), parent_stage_id=None, kind="mixer_provider",
            selection_time=self.adapter.clock(), coverage="actual_provider_pool")
        frame["stages"].append(stage)

    def group_selected(self, args, kwargs, result):
        if not self.mixers:
            return
        pool = arg(args, kwargs, 0, "pairs")
        local = selector_locals(self.mixer_code)
        if local.get("interaction_mixer_group_weight") is not pool or result is None:
            return
        frame = self.mixers[-1]
        stage = selection_stage(pool, index_is([p for _, p in pool], result), "weighted", self.limit,
            lambda value: {"group": enum_name(value), "score_components": {"source": "SUBACTION_GROUP_WEIGHTING",
                "values": {}, "missing": [], "interpretation": "tuned_weight"}})
        stage.update(stage_id=self.uid("stage"), kind="mixer_group", selection_time=self.adapter.clock(),
            parent_stage_id=frame["stages"][-1]["stage_id"] if frame["stages"] else None, coverage="actual_group_pool")
        frame["stages"].append(stage)

    def mixer_scores_before(self, args, kwargs):
        if not self.mixers:
            return
        inputs = arg(args, kwargs, 2, "mixer_aops")
        gsi = arg(args, kwargs, 3, "gsi_mixer_scoring")
        return (list(inputs) if isinstance(inputs, (list, tuple)) else [], gsi, len(gsi) if gsi is not None else 0)

    def mixer_scores_after(self, context, args, kwargs, result, error):
        if context is None or error is not None or not result:
            return
        inputs, gsi, start = context
        for score, aop in result.values():
            matched = [item for item in inputs if item[1] is aop]
            text_rows = [row for row in (gsi[start:] if gsi is not None else [])
                if row[0] == score and row[2] == str(aop.affordance) and row[3] == str(aop.target)]
            values = {"mixer_weight": number(matched[0][0])} if len(matched) == 1 else {}
            self.evidence.put(aop, {
                "source": "mixer_scoring_call", "values": values, "score_time": self.adapter.clock(),
                "native_text": text_rows[0][4][:4096] if len(text_rows) == 1 else None,
                "native_text_basis": "unique_within_scoring_call_resource_target_and_score" if len(text_rows) == 1 else "unavailable_or_ambiguous",
                "missing": ["structured_content_score", "individual_service_multipliers"]})
