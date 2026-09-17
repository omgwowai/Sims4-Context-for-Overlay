"""Retention/identity regressions and installed-game selection integration probes."""

import gc
import functools
import inspect
import json
import sys
import types
import unittest
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import MemoryJournal
from tool_support import GameBytecode
from context_overlay.autonomy import PendingDecisions, selection_stage
from context_overlay.autonomy_capture import AutonomyCapture
from context_overlay.hooks import Hooks
from context_overlay.model import entity
from context_overlay.recorder import Recorder
from context_overlay.semanticizer import explain_event


class Item:
    pass


def state(**kwargs):
    item = Item()
    item.__dict__.update(kwargs)
    return item


class BufferChecks(unittest.TestCase):
    def test_forced_winner_keeps_original_rank_and_probability(self):
        stage = selection_stage([(i, i) for i in range(1, 9)], 0, "weighted", 5, lambda i: {"id": i})
        self.assertEqual([row["id"] for row in stage["candidates"]], [8, 7, 6, 5, 1])
        self.assertEqual(stage["candidates"][-1]["rank"], 8)
        self.assertAlmostEqual(stage["candidates"][-1]["probability"], 1 / 36)
        self.assertAlmostEqual(stage["omitted_probability"], 9 / 36)

    def test_single_limit_ties_uniform_and_deterministic(self):
        pool = [(0, i) for i in range(4)]
        stage = selection_stage(pool, 3, "uniform", 1, lambda i: {"id": i})
        self.assertEqual([r["id"] for r in stage["candidates"]], [3])
        self.assertEqual(stage["omitted_probability"], .75)
        self.assertEqual(stage["candidates"][0]["rank"], 4)
        deterministic = selection_stage(pool, 3, "deterministic", 5, lambda i: {"id": i})
        self.assertEqual([r["probability"] for r in deterministic["candidates"]], [0, 0, 0, 1])

    def test_expiry_capacity_gc_and_session_cleanup(self):
        now = [0]
        buffer = PendingDecisions(2, 5000, 10, lambda: now[0])
        a, b, c = Item(), Item(), Item()
        self.assertTrue(buffer.put(a, {"id": "a"}))
        buffer.put(b, {"id": "b"})
        buffer.put(c, {"id": "c"})
        self.assertIsNone(buffer.get(a))
        self.assertEqual(buffer.get(b)["id"], "b")
        del b
        gc.collect()
        buffer.sweep()
        self.assertEqual(buffer.status()["pending"], 1)
        now[0] = 11
        self.assertIsNone(buffer.pop(c))
        self.assertEqual(buffer.bytes, 0)
        buffer.put(c, {"id": "c"})
        buffer.clear()
        self.assertEqual(buffer.drops, {"capacity": 1, "object_released": 1, "expired": 1, "session_end": 1})

    def test_memory_limit_scope_and_recycled_identity(self):
        buffer = PendingDecisions(byte_limit=1000)
        a, b = Item(), Item()
        a.sim = object()
        self.assertFalse(buffer.put(a, {"x": "a" * 1000}))
        buffer.put(a, {})
        buffer.entries[id(b)] = buffer.entries.pop(id(a))
        self.assertIsNone(buffer.get(b))
        buffer.put(a, {})
        buffer.sweep(lambda sim: False)
        self.assertEqual(buffer.bytes, 0)
        self.assertEqual(buffer.drops, {"oversized": 1, "identity_mismatch": 1, "scope_exit": 1})


class HookChecks(unittest.TestCase):
    def test_descriptors_inheritance_and_external_wrapper(self):
        class Base:
            @staticmethod
            def static(value):
                return value + 1
            @classmethod
            def cls(cls, value):
                return cls, value
        class Child(Base):
            pass
        hooks, seen = Hooks(self.fail), []
        original = vars(Base)["static"]
        hooks.after(Base, "static", lambda args, kw, result: seen.append(result))
        hooks.after(Child, "cls", lambda args, kw, result: seen.append(result))
        self.assertEqual(Base.static(1), 2)
        self.assertEqual(Base().static(2), 3)
        self.assertEqual(Child.cls(4), (Child, 4))
        hooks.remove()
        self.assertIs(vars(Base)["static"], original)
        self.assertNotIn("cls", vars(Child))
        hooks.after(Base, "static", lambda *args: self.fail("inactive hook called"))
        inner = Base.static
        Base.static = staticmethod(lambda x: inner(x))
        hooks.remove()
        self.assertEqual(Base.static(7), 8)

    def test_generators_interleave_send_throw_close_and_return(self):
        active, finished = [], []
        class Engine:
            def run(self, label):
                self.assert_active(label)
                try:
                    value = yield label
                    self.assert_active(label)
                    try:
                        yield value
                    except ValueError:
                        self.assert_active(label)
                        return 42
                finally:
                    self.assert_active(label)
            assert_active = lambda _, label: self.assertEqual(active, [label])
        hooks = Hooks(self.fail)
        hooks.generator(Engine, "run", lambda args, kwargs: args[1], active.append,
            lambda ctx: active.remove(ctx), lambda ctx, result, error: finished.append((ctx, result, type(error).__name__)))
        self.assertTrue(inspect.isgeneratorfunction(Engine.run))
        a, b = Engine().run("a"), Engine().run("b")
        self.assertEqual(next(a), "a")
        self.assertEqual(active, [])
        self.assertEqual(next(b), "b")
        self.assertEqual(a.send(9), 9)
        with self.assertRaises(StopIteration) as done:
            a.throw(ValueError("injected"))
        self.assertEqual(done.exception.value, 42)
        b.close()
        self.assertEqual(active, [])
        self.assertEqual(finished, [("a", 42, "NoneType"), ("b", None, "GeneratorExit")])
        hooks.remove()

    def test_original_exception_and_callback_failure_remain_isolated(self):
        error, errors = ValueError("engine"), []
        class Engine:
            def call(self):
                raise error
        hooks = Hooks(errors.append)
        hooks.around(Engine, "call", lambda *args: 1, lambda *args: 1 / 0)
        with self.assertRaises(ValueError) as caught:
            Engine().call()
        self.assertIs(caught.exception, error)
        self.assertEqual(len(errors), 1)
        hooks.remove()


NATIVE_READER = GameBytecode()
NATIVE_AVAILABLE = NATIVE_READER.available
native_code = NATIVE_READER.symbol


def native(member, suffix, env, defaults=(), archive="simulation.zip"):
    env.setdefault("__builtins__", __builtins__)
    code = native_code(member, suffix, archive)
    return types.FunctionType(code, env, code.co_name, defaults)


def native_component_export(component, container, methods):
    """Execute EA's frozen-function exporter and concrete-owner class patcher."""
    component_name = NS(instance_attr="autonomy_component")
    env = {"__builtins__": __builtins__, "ComponentContainer": container,
        "_update_wrapper": lambda original, wrapper, note: functools.update_wrapper(wrapper, original),
        "sims4": NS(reload=NS(_getattr_exact=lambda *args: None)),
        "logger": NS(warn=lambda *args: None), "restore_component_methods": lambda *args: None}
    def cell(value):
        return (lambda: value).__closure__[0]
    def closed(suffix, values):
        code = native_code("objects/components/__init__.pyc", "ComponentMetaclass.__new__." + suffix)
        return types.FunctionType(code, env, code.co_name, None,
            tuple(cell(values[name]) for name in code.co_freevars))
    build = closed("build_exported_func", {"cls": component, "component_name": component_name})
    return closed("apply_component_methods", {"cls": component, "component_name": component_name,
        "component_methods": {name: build(method) for name, method in methods.items()}, "patched_owner_classes": set()})


@unittest.skipUnless(NATIVE_AVAILABLE, "Requires installed Sims 4 CPython 3.7 bytecode")
class NativeChecks(unittest.TestCase):
    def setUp(self):
        self.now, self.rng_calls, self.roll_calls, self.fraction = 1, 0, 0, 0.0
        self.roll_value = .2
        self.keys = NS(PROBABILITY_KEY="probability", ADDITIONAL_RESULT_INFO="additional",
                       AFFORDANCE_KEY="affordances", OBJECTS_KEY="objects")
        self.Scored = namedtuple("Scored", "score route_time multitasking_percentage interaction")
        probability = namedtuple("Probability", "interaction score probability multitask_roll probability_type interaction_prefix")
        self.default = object()
        def uniform(low, high):
            self.rng_calls += 1
            return low + (high - low) * self.fraction
        self.random = NS(uniform=uniform, randint=lambda lo, hi: int(uniform(lo, hi)), choice=lambda values: values[0])
        random_env = {}
        for name, defaults in (("_weighted", (self.random, False)), ("weighted_random_index", (self.random,)), ("weighted_random_item", (self.random, False))):
            random_env[name] = native("sims4/random.pyc", name, random_env, defaults, "core.zip")
        self.sim_random = NS(**random_env)
        # Index and item functions call _weighted through their shared native globals.
        class Archiver:
            enabled = False
            def archive_enable_fn(self, enableLog=False):
                self.enabled = enableLog
        self.archiver = Archiver()
        handlers = NS(archiver=self.archiver, archive_autonomy_data=lambda *args: None)
        self.env = {"DEFAULT": self.default, "GSIDataKeys": self.keys, "AutonomyProbabilityData": probability,
            "ScoredInteractionData": self.Scored, "autonomy": NS(settings=NS(AutonomyRandomization=NS(ENABLED=1))),
            "sims4": NS(random=self.sim_random), "random": self.random, "gsi_handlers": NS(autonomy_handlers=handlers),
            "interactions": NS(context=NS(InteractionContext=NS(SOURCE_AUTONOMY=1))),
            "logger": NS(error=lambda *a: None, warn=lambda *a: None, assert_log=lambda *a: None, assert_raise=lambda *a: None)}
        class Service:
            NUM_INTERACTIONS = 5
            _ARTIFICIAL_MAX_ROUTE_TIME_INCREMENT = .0001
        for name, defaults in (("choose_best_interaction", (False, self.default, "")),
            ("_select_best_result", (False, self.default, True)), ("_recalculate_scores_based_on_route_time", ()),
            ("_calculate_score_based_on_route_time", ())):
            setattr(Service, name, native("autonomy/autonomy_service.pyc", "AutonomyService." + name, self.env, defaults))
        self.Service, self.service = Service, Service()
        harness = self
        class Sim:
            id = 1
            sim_info = NS(sim_id=1)
            _component_reload_hooks = None
            def get_autonomy_randomization_setting(self):
                return 1
        class Component:
            def get_multitasking_roll(self):
                harness.roll_calls += 1
                return harness.roll_value
            def _should_run_cached_interaction(self, interaction_to_run):
                return True
        # EA installs the forwarding method on the concrete owner type, not Sim.
        self.BaseSim, self.Component = Sim, Component
        self.apply_component_methods = native_component_export(Component, Sim,
            {"get_multitasking_roll": Component.get_multitasking_roll})
        class ConcreteSim(Sim):
            pass
        self.apply_component_methods(ConcreteSim, False)
        self.sim = ConcreteSim()
        self.component = self.sim.autonomy_component = Component()
        class Queue:
            def append(self, interaction):
                harness.record(interaction, "queued")
                return getattr(interaction, "queue_result", True)
        self.sim.queue = Queue()
        class Interaction:
            is_super = True
            use_best_scoring_aop = True
            def invalidate(self):
                self.invalidated = True
            def _trigger_interaction_start_event(self):
                harness.record(self, "started")
            def _trigger_interaction_complete_test_event(self):
                pass
            def _run_gen(self, timeline):
                if getattr(self, "run_error", None):
                    raise self.run_error
                return getattr(self, "run_result", False)
                yield
            def _exit(self, timeline, allow_yield):
                return
                yield
        self.Interaction = Interaction
        class GeneratorBase:
            def _get_generator(self):
                return self.pending_generator
            @staticmethod
            def _result_value(exc):
                return True if exc.value is None else exc.value
        GeneratorBase._run = native("elements.pyc", "GeneratorElementBase._run",
            {"GeneratorElementBase": GeneratorBase, "_check_yield": lambda x: True})
        class Generator(GeneratorBase):
            def __init__(self, method):
                self.pending_generator, self.generator = method, None
        class Result:
            def __init__(self, child):
                self.child, self.result = child, None
        class Timeline:
            heap, now = [], 0
            def schedule(self, element):
                self.element = element
            def simulate(self, now):
                self.element.result = self.element.child._run(self)
        class ExecuteResult(namedtuple("ExecuteResult", "result interaction reason")):
            def __bool__(self):
                return bool(self.result)
        class AOP:
            pass
        AOP.execute_interaction = staticmethod(native("interactions/aop.pyc", "AffordanceObjectPair.execute_interaction", {
            "ExecuteResult": ExecuteResult, "elements": NS(ResultElement=Result, GeneratorElement=Generator),
            "services": NS(time_service=lambda: NS(sim_timeline=NS(get_sub_timeline=Timeline))),
            "logger": self.env["logger"], "log_interaction": lambda *a: None}))
        self.AOP, self.Generator, self.Timeline = AOP, Generator, Timeline
        class Mixer:
            def _run_gen(self, timeline, timeslice):
                # Explicit suspension tests observer context lifetime.
                chosen = self.provider.get_mixer_provider()
                interaction_mixer_group_weight = [(1, "friendly"), (9, "funny")]
                self.group = harness.sim_random.weighted_random_item(interaction_mixer_group_weight)
                yield "pause"
                return chosen
            def _create_and_score_mixers(self, mixer_provider, mixer_aops, gsi_mixer_scoring):
                return {row[1].affordance: (row[0], row[1]) for row in mixer_aops}
        self.Mixer = Mixer
        class Provider:
            pass
        Provider.get_mixer_provider = native("autonomy/autonomy_mixer_provider_scoring.pyc", "_MixerProviderScoring.get_mixer_provider",
            {"sims4": NS(random=self.sim_random), "random": self.random})
        self.Provider = Provider
        adapter = NS(clock=lambda: {"ticks": str(self.now)}, in_scope=lambda sim: sim is self.sim,
            reference=lambda sim: entity("sim", sim.id, "小明"),
            event_reference=lambda value: entity("object", value.id, "目标") if getattr(value, "id", None) else None,
            interaction_name=lambda item, *args: {"text": "行为" + str(item.id), "status": "resolved"},
            resource=lambda item, **kwargs: {"name": getattr(item, "__name__", str(item))} if item is not None else None)
        self.recorder = Recorder(MemoryJournal(), "native-probe")
        self.runtime = NS(config={}, adapter=adapter, recorder=self.recorder, session_id="native-probe", closed=False)
        self.capture = AutonomyCapture(self.runtime)
        self.expected_error = None
        modules = {"autonomy.autonomy_service": NS(AutonomyService=Service),
            "autonomy.autonomy_component": NS(AutonomyComponent=Component), "sims.sim": NS(Sim=Sim),
            "autonomy.autonomy_modes": NS(_MixerAutonomy=Mixer),
            "autonomy.autonomy_mixer_provider_scoring": NS(_MixerProviderScoring=Provider),
            "random": self.random, "sims4.random": self.sim_random,
            "interactions.interaction_queue": NS(InteractionQueue=Queue), "interactions.aop": NS(AffordanceObjectPair=AOP),
            "interactions.base.interaction": NS(Interaction=Interaction), "elements": NS(GeneratorElementBase=GeneratorBase),
            "autonomy.autonomy_gsi_enums": NS(GSIDataKeys=self.keys), "gsi_handlers.autonomy_handlers": handlers,
            "interactions.context": NS(InteractionContext=NS(SOURCE_AUTONOMY=1))}
        with patch("context_overlay.autonomy_capture.importlib.import_module", side_effect=modules.__getitem__):
            self.capture.install()
        self.assertTrue(self.capture.enabled, self.capture.last_error)
        self.addCleanup(self.capture.close)
        self.inputs = [self.scored(i) for i in range(1, 9)]

    def tearDown(self):
        self.assertEqual(self.capture.last_error, self.expected_error)
        self.assertFalse(self.recorder.paused)
        self.assertEqual(self.capture.choices, [])
        self.assertEqual(self.capture.chains, [])

    def scored(self, value, identifier=None, route=0):
        item = self.Interaction()
        item.id, item.sim, item.context = identifier or value, self.sim, NS(source=1, sim=self.sim)
        item.affordance = type("Action" + str(item.id), (), {"guid64": 1000 + item.id, "immediate": False, "cheat": False})
        item.aop = state(target=NS(id=item.id + 100), affordance=item.affordance)
        item.target = item.aop.target
        return self.Scored(value, route, .5, item)

    def request(self, zero=False):
        return state(sim=self.sim, context=NS(sim=self.sim, source=1), is_script_request=False,
            autonomy_mode_label="FullAutonomy", autonomy_mode=NS(allows_routing=lambda: True),
            consider_scores_of_zero=zero, gsi_data={"probability": [], "affordances": [], "objects": []},
            similar_aop_cache={}, invalidate_created_interactions=lambda excluded_si: None)

    def choose(self, req=None, inputs=None, all_options=True, randomized=1):
        return self.service._select_best_result(self.inputs if inputs is None else inputs, req or self.request(), all_options, randomized, False)

    def replace_sim(self, sim, component=None):
        sim.queue = self.sim.queue
        sim.autonomy_component = component or self.component
        self.sim = sim
        self.inputs = [self.scored(i) for i in range(1, 9)]

    def decisions(self):
        return [event for event in self.recorder.events.values() if event.get("category") == "autonomy.decision"]

    def record(self, interaction, phase, **extra):
        facts = {"actor": entity("sim", 1, "小明"), "target": entity("object", interaction.target.id, "目标"),
            "interaction_id": interaction.id, "name": "动作", "tier": "main", "trigger": {"name": "AUTONOMY"}}
        facts.update(extra)
        return self.recorder.interaction(phase, facts, {"ticks": str(self.now)}, "native_test")

    def test_actual_pool_top_five_and_forced_lowest(self):
        result = self.choose()
        self.assertEqual(result.id, 1)
        self.assertEqual(self.decisions(), [])
        self.assertTrue(self.AOP.execute_interaction(result))
        event = self.decisions()[0]
        stage = event["payload"]["stages"][0]
        self.assertEqual(stage["pool_count"], 8)
        self.assertEqual([row["rank"] for row in stage["candidates"]], [1, 2, 3, 4, 8])
        self.assertAlmostEqual(stage["omitted_probability"], .25)
        self.assertEqual((self.rng_calls, self.roll_calls), (1, 1))
        self.assertEqual(event["entities"], ["sim:1"])
        self.assertNotIn("object:108", self.recorder.index.entities)
        text = explain_event(event)
        self.assertIn("成功入队", text["text"])
        self.assertIn("第 8 名", text["decision_details"])
        self.assertIn("2.78%", text["decision_details"])
        self.assertIn("未提供", text["decision_details"])

    def test_concrete_export_without_base_method_captures_deterministic_pool(self):
        self.assertFalse(hasattr(self.BaseSim, "get_multitasking_roll"))
        owner = type(self.sim)
        original = owner.get_multitasking_roll
        self.assertEqual(self.capture.status()["coverage"]["multitasking_roll"], "pending_sim_type")
        self.archiver.archive_enable_fn(enableLog=False)
        for _ in range(2):
            req = self.request()
            req.gsi_data = None
            result = self.choose(req, all_options=False, randomized=0)
            stage = self.capture.pending.get(result)["stages"][0]
            self.assertEqual(result.id, 8)
            self.assertEqual(stage["pool_count"], 5)
            self.assertEqual(stage["coverage"], "actual_selector_pool")
            self.assertEqual(stage["multitasking"]["roll"], .2)
            self.assertEqual(stage["multitasking"]["roll_source"], "native_getter")
        self.assertEqual((self.rng_calls, self.roll_calls), (0, 2))
        self.assertEqual(self.capture.coverage["multitasking_hooked_types"], 1)
        self.assertEqual(len(self.capture.roll_hooks.entries), 1)
        self.capture.close()
        self.assertIs(owner.get_multitasking_roll, original)
        self.assertFalse(hasattr(self.BaseSim, "get_multitasking_roll"))

    def test_type_created_after_install_is_observed_before_its_first_choice(self):
        first = self.choose()
        self.AOP.execute_interaction(first)
        class LateSim(self.BaseSim):
            pass
        self.assertFalse(hasattr(LateSim, "get_multitasking_roll"))
        self.apply_component_methods(LateSim, False)
        original = LateSim.get_multitasking_roll
        self.replace_sim(LateSim())
        self.fraction = .999
        result = self.choose()
        self.AOP.execute_interaction(result)
        self.assertEqual(result.id, 8)
        self.assertEqual(len(self.decisions()), 2)
        self.assertEqual(self.decisions()[-1]["payload"]["stages"][0]["multitasking"]["roll_source"], "native_getter")
        self.assertEqual(self.capture.coverage["multitasking_hooked_types"], 2)
        self.assertEqual((self.rng_calls, self.roll_calls), (2, 2))
        self.capture.close()
        self.assertIs(LateSim.get_multitasking_roll, original)

    def test_inherited_observer_is_not_wrapped_twice(self):
        self.choose()
        class ChildSim(type(self.sim)):
            pass
        self.replace_sim(ChildSim())
        result = self.choose()
        self.assertEqual(len(self.capture.roll_hooks.entries), 1)
        self.assertNotIn("get_multitasking_roll", vars(ChildSim))
        self.assertEqual((self.rng_calls, self.roll_calls), (2, 2))
        self.assertEqual(self.capture.pending.get(result)["stages"][0]["multitasking"]["roll_source"], "native_getter")
        self.capture.close()
        self.assertNotIn("get_multitasking_roll", vars(ChildSim))

    def test_missing_static_export_degrades_then_late_export_recovers(self):
        class LateExportSim(self.BaseSim):
            pass
        self.replace_sim(LateExportSim())
        # A native/third-party instance-level forwarding entry remains callable.
        self.sim.get_multitasking_roll = self.component.get_multitasking_roll
        result = self.choose()
        self.AOP.execute_interaction(result)
        stage = self.decisions()[0]["payload"]["stages"][0]
        self.expected_error = "multitasking hook: AttributeError: get_multitasking_roll"
        self.assertEqual(self.capture.last_error, self.expected_error)
        self.assertTrue(self.capture.enabled)
        self.assertEqual(self.capture.coverage["state"], "installed")
        self.assertEqual(self.capture.coverage["multitasking_roll"], "partial")
        self.assertEqual((stage["multitasking"]["hook"], stage["multitasking"]["roll_source"]), ("unavailable", "native_gsi"))
        self.assertEqual(stage["pool_count"], 8)
        del self.sim.get_multitasking_roll
        self.apply_component_methods(LateExportSim, False)
        self.fraction = .999
        result = self.choose()
        self.AOP.execute_interaction(result)
        stage = self.decisions()[-1]["payload"]["stages"][0]
        self.assertEqual((stage["multitasking"]["hook"], stage["multitasking"]["roll_source"]), ("installed", "native_getter"))
        self.assertEqual(self.capture.counts["multitasking_hook_unavailable"], 1)
        self.assertEqual((self.rng_calls, self.roll_calls), (2, 2))

    def test_optional_hook_failure_keeps_decisions_and_marks_missing_roll(self):
        req = self.request()
        req.gsi_data = None
        with patch.object(self.capture.roll_hooks, "after", side_effect=RuntimeError("read-only type")):
            result = self.choose(req)
        self.expected_error = "multitasking hook: RuntimeError: read-only type"
        self.AOP.execute_interaction(result)
        self.assertTrue(self.capture.enabled)
        stage = self.decisions()[0]["payload"]["stages"][0]
        self.assertEqual(stage["pool_count"], 8)
        self.assertEqual(stage["multitasking"]["roll_source"], "unavailable")
        self.assertIsNone(stage["multitasking"]["roll"])
        self.assertEqual(stage["multitasking"]["basis"], "native_selector_return_roll_unavailable")
        self.assertEqual((self.rng_calls, self.roll_calls), (1, 1))

    def test_native_lazy_getter_preserves_draws_and_results_when_disabled(self):
        unset, lazy_draws = object(), []
        harness = self
        def draw():
            lazy_draws.append(.2)
            return .2
        getter = native("autonomy/autonomy_component.pyc", "AutonomyComponent.get_multitasking_roll",
            {"UNSET": unset, "random": NS(random=draw)})
        class LazyComponent:
            _multitasking_roll = unset
            def get_multitasking_roll(self):
                harness.roll_calls += 1
                return getter(self)
        class LazySim(self.BaseSim):
            pass
        export = native_component_export(LazyComponent, self.BaseSim,
            {"get_multitasking_roll": LazyComponent.get_multitasking_roll})
        export(LazySim, False)
        component = LazyComponent()
        self.replace_sim(LazySim(), component)
        selected = [self.choose(), self.choose()]
        enabled_counts = (self.rng_calls, self.roll_calls, len(lazy_draws))
        self.assertEqual(enabled_counts, (2, 2, 1))
        for item in selected:
            self.assertEqual(self.capture.pending.get(item)["stages"][0]["multitasking"]["roll_source"], "native_getter")
        self.capture.close()
        component._multitasking_roll = unset
        lazy_draws[:] = []
        self.rng_calls = self.roll_calls = 0
        actual = [self.choose(), self.choose()]
        self.assertEqual(actual, selected)
        self.assertEqual((self.rng_calls, self.roll_calls, len(lazy_draws)), enabled_counts)

    def test_engine_cutoff_deterministic_and_uniform(self):
        cutoff = self.choose(all_options=False)
        self.assertEqual(cutoff.id, 4)
        self.assertEqual(self.capture.pending.get(cutoff)["stages"][0]["pool_count"], 5)
        deterministic = self.choose(randomized=0)
        stage = self.capture.pending.get(deterministic)["stages"][0]
        self.assertEqual((stage["pool_count"], stage["mode"]), (8, "deterministic"))
        self.assertEqual(stage["candidates"][0]["probability"], 1)
        self.assertEqual(stage["omitted_probability"], 0)
        uniform = self.choose(self.request(True), [self.scored(0, i) for i in range(10, 14)])
        stage = self.capture.pending.get(uniform)["stages"][0]
        self.assertEqual(stage["mode"], "uniform")
        self.assertEqual(stage["candidates"][0]["probability"], .25)

    def test_route_weight_and_raw_score_stay_separate(self):
        result = self.choose(self.request(True), [self.scored(0, i, route=i) for i in range(10, 18)])
        stage = self.capture.pending.get(result)["stages"][0]
        self.assertEqual(stage["mode"], "weighted")
        self.assertTrue(all(c["raw_score"] == 0 and c["weight"] > 0 for c in stage["candidates"]))

    def test_multitask_rejection_does_not_commit_and_script_bypasses_gate(self):
        req = self.request()
        self.roll_value = .8
        self.assertIsNone(self.choose(req))
        self.assertEqual(len(req.gsi_data["probability"]), 8)
        self.assertEqual(self.capture.pending.status()["pending"], 0)
        self.assertEqual(self.decisions(), [])
        req.is_script_request = True
        result = self.choose(req)
        self.AOP.execute_interaction(result)
        self.assertFalse(self.decisions()[0]["payload"]["stages"][0]["multitasking"]["applicable"])
        self.assertTrue(self.decisions()[0]["payload"]["is_script_request"])

    def test_queue_rejection_after_notification_and_cancelled_before_start(self):
        result = self.choose()
        result.queue_result = False
        self.assertFalse(self.AOP.execute_interaction(result))
        self.assertEqual(self.decisions(), [])
        result = self.choose()
        result.queue_result = True
        self.AOP.execute_interaction(result)
        ended = self.record(result, "exited", finishing_type="USER_CANCEL")
        self.assertIsNone(ended["started_time"])
        self.assertEqual(ended["outcome"], "cancelled")
        self.assertEqual(ended["facts"]["decision_event_id"], self.decisions()[0]["event_id"])

    def test_same_request_multiple_choices_cache_executes_in_reverse(self):
        req = self.request()
        first = self.choose(req)
        first_id = self.capture.pending.get(first)["decision_id"]
        self.fraction = .999
        second = self.choose(req)
        self.now = 50
        self.AOP.execute_interaction(second)
        self.component._should_run_cached_interaction(first)
        self.AOP.execute_interaction(first)
        events = self.decisions()
        self.assertEqual(len(events), 2)
        self.assertNotEqual(events[0]["event_id"], events[1]["event_id"])
        self.assertEqual(events[1]["event_id"], first_id)
        self.assertTrue(events[1]["payload"]["cache_origin"])
        self.assertEqual(events[1]["payload"]["selection_time"], {"ticks": "1"})
        self.assertEqual(events[1]["payload"]["commit_time"], {"ticks": "50"})
        self.assertEqual(events[0]["payload"]["stages"][0]["pool_count"], 8)

    def test_two_native_stages_and_unselected_instances_invalidated(self):
        req = self.request()
        selected = self.inputs[0].interaction
        selected.use_best_scoring_aop = False
        req.similar_aop_cache[selected.affordance] = self.inputs[2:]
        req.invalidate_created_interactions = lambda excluded_si: [row.interaction.invalidate() for row in self.inputs if row.interaction is not excluded_si]
        result = self.choose(req)
        self.assertEqual(result.id, 3)
        self.AOP.execute_interaction(result)
        stages = self.decisions()[0]["payload"]["stages"]
        self.assertEqual([s["pool_count"] for s in stages], [8, 6])
        self.assertEqual([len(s["candidates"]) for s in stages], [5, 5])
        self.assertEqual(stages[1]["parent_stage_id"], stages[0]["stage_id"])
        self.assertTrue(selected.invalidated)

    def test_immediate_false_and_exception_keep_entered_decision(self):
        result = self.choose()
        result.affordance.immediate = True
        self.assertFalse(self.AOP.execute_interaction(result))
        payload = self.decisions()[0]["payload"]
        self.assertEqual(payload["retention_gate"], "immediate_entered")
        self.assertFalse(payload["execution_result"]["returned_success"])
        self.fraction = .999
        result = self.choose()
        result.affordance.immediate, result.run_error = True, ValueError("run failed")
        with self.assertRaises(ValueError) as caught:
            self.AOP.execute_interaction(result)
        self.assertIs(caught.exception, result.run_error)
        self.assertEqual(self.decisions()[1]["payload"]["execution_result"]["exception_type"], "ValueError")

    def test_start_notification_alone_is_not_immediate_execution(self):
        result = self.choose()
        result.affordance.immediate = True
        result._trigger_interaction_start_event()
        self.assertEqual(self.decisions(), [])
        result.invalidate()
        self.assertIsNone(self.capture.pending.get(result))

    def test_generator_provider_group_and_action_have_separate_pools(self):
        req = self.request()
        # Real providers are hashable objects.
        values = []
        for row in self.inputs:
            provider = Item()
            provider._mixer_provider = row.interaction
            values.append(provider)
        scoring = self.Provider()
        scoring._postive_scoring_mixer_providers = {value: i + 1 for i, value in enumerate(values)}
        scoring._zero_scoring_mixer_providers, scoring.gsi_mixer_provider_data = {}, {}
        mixer = self.Mixer()
        mixer._request, mixer.provider = req, scoring
        generator = mixer._run_gen(None, None)
        self.assertEqual(next(generator), "pause")
        self.assertEqual(self.capture.mixers, [])
        self.sim_random.weighted_random_item([(1, "unrelated")])
        with self.assertRaises(StopIteration):
            next(generator)
        result = self.choose(req)
        result.is_super = False
        # Real native subaction results are already mixers at selection time.
        self.capture.pending.get(result)["tier"] = "internal"
        self.AOP.execute_interaction(result)
        stages = self.decisions()[0]["payload"]["stages"]
        self.assertEqual([s["kind"] for s in stages], ["mixer_provider", "mixer_group", "interaction"])
        self.assertEqual([s["pool_count"] for s in stages], [8, 2, 8])
        self.assertEqual(stages[2]["parent_stage_id"], stages[1]["stage_id"])

    def test_native_mixer_generator_uses_cached_scores_and_new_selection_occurrences(self):
        class Valid:
            def __init__(self):
                self.scores = []
            def add(self, value):
                self.scores.append(value)
        env = dict(self.env, ValidInteractions=Valid)
        class NativeMixer:
            SUBACTION_GROUP_WEIGHTING = {"friendly": 1, "funny": 9}
            SUBACTION_GROUP_UNTUNED_WEIGHT = 1
            def _cache_mixer_provider_scoring(self, enabled):
                pass
        NativeMixer._run_gen = native("autonomy/autonomy_modes.pyc", "_MixerAutonomy._run_gen", env)
        capture = self.capture
        capture.mixer_code = NativeMixer._run_gen.__code__
        capture.hooks.generator(NativeMixer, "_run_gen", capture.mixer_create, capture.mixers.append,
            capture.mixer_leave, capture.mixer_finish)
        req, mixer = self.request(), NativeMixer()
        req.skipped_affordance_list = []
        mixer._request, mixer._run_gen_call_count, mixer._gsi_mixer_scoring = req, 0, None
        providers = []
        for row in self.inputs:
            provider = Item()
            provider._mixer_provider = row.interaction
            provider.mixer_interaction_groups = lambda: ("friendly", "funny")
            providers.append(provider)
            row.interaction.is_super = False
            row.interaction.aop.interaction_factory = lambda context, item=row.interaction: NS(interaction=item)
        scoring = self.Provider()
        scoring._postive_scoring_mixer_providers = {value: i + 1 for i, value in enumerate(providers)}
        scoring._zero_scoring_mixer_providers, scoring.gsi_mixer_provider_data = {}, {}
        scoring.is_valid = lambda: True
        scoring.is_mixer_group_valid = lambda provider, group: True
        mixer._mixer_provider_scoring = scoring
        mixer._mixer_provider_and_group_to_scored_mixer_aops = {
            (provider, group): {row.interaction.affordance: (row.score, row.interaction.aop) for row in self.inputs}
            for provider in providers for group in ("friendly", "funny")}
        first = None
        for _ in range(2):
            with self.assertRaises(StopIteration) as stopped:
                next(mixer._run_gen(None, None))
            result = self.choose(req, stopped.exception.value.scores)
            pending = capture.pending.get(result)
            self.assertEqual([stage["kind"] for stage in pending["stages"]], ["mixer_provider", "mixer_group", "interaction"])
            if first:
                self.assertNotEqual(pending["decision_id"], first["decision_id"])
                self.assertNotEqual(pending["stages"][0]["stage_id"], first["stages"][0]["stage_id"])
            first = pending
            self.fraction = .999
        self.assertEqual(mixer._run_gen_call_count, 2)
        self.AOP.execute_interaction(result)
        self.assertEqual(self.decisions()[0]["tier"], "internal")

    def test_native_mixer_scoring_attaches_available_breakdown_without_recalculation(self):
        class NativeMixer:
            _MINIMUM_SCORE = 0
        services = NS(get_club_service=lambda: None, dynasty_service=lambda: None,
            business_service=lambda: NS(get_business_manager_for_zone=lambda zone: None),
            custom_schedule_service=lambda: None, current_zone_id=lambda: 1)
        NativeMixer._create_and_score_mixers = native("autonomy/autonomy_modes.pyc", "_MixerAutonomy._create_and_score_mixers",
            {"services": services})
        self.capture.hooks.around(NativeMixer, "_create_and_score_mixers", self.capture.mixer_scores_before, self.capture.mixer_scores_after)
        frame = {"request": self.request(), "stages": [], "attempts": 0}
        self.capture.mixers.append(frame)
        inputs = []
        for row in self.inputs:
            row.interaction.aop.affordance.sub_action = NS(mixer_group="friendly")
            row.interaction.is_super = False
            inputs.append((row.score, row.interaction.aop, None))
        gsi = []
        result = NativeMixer()._create_and_score_mixers(NS(is_social=False), inputs, gsi)
        self.capture.mixers.remove(frame)
        self.assertEqual(len(result), 8)
        chosen = self.choose()
        values = self.capture.pending.get(chosen)["stages"][0]["candidates"]
        for candidate in values:
            detail = candidate["score_components"]
            self.assertEqual(detail["values"]["mixer_weight"], candidate["raw_score"])
            self.assertTrue(detail["native_text"].startswith("Weight:"))
            self.assertEqual(detail["score_time"], {"ticks": "1"})

    def test_missing_gsi_still_captures_weighted_pool_and_marks_score_gaps(self):
        req = self.request()
        req.gsi_data = None
        result = self.choose(req)
        data = self.capture.pending.get(result)
        self.assertEqual(data["filters"]["coverage"], "GSI_unavailable")
        self.assertEqual(data["stages"][0]["pool_count"], 8)
        self.assertEqual(data["stages"][0]["mode"], "weighted")

    def test_selection_does_not_attach_to_unrelated_or_continuation_interaction(self):
        result = self.choose()
        other = self.scored(10, 99).interaction
        other.context.source_interaction_id = result.id
        self.AOP.execute_interaction(other)
        self.assertEqual(self.decisions(), [])
        self.assertIsNone(getattr(other, "_context_overlay_autonomy_decision", None))
        self.AOP.execute_interaction(result)
        self.assertEqual(len(self.decisions()), 1)

    def test_score_components_exact_instance_and_rejections_are_aggregated(self):
        req = self.request()
        class Score(float):
            pass
        for row in self.inputs:
            score = Score(row.score)
            score.interaction, score.commodity_scores = row.interaction, []
            score.rel_utility_score, score.buff_utility_score = 1.5, 2.0
            score.opportunity_costs = {}
            req.gsi_data["affordances"].append(NS(interaction=row.interaction.aop, score=score))
        for _ in range(3):
            req.gsi_data["affordances"].append(NS(_reason="route failed {}", _reason_args=(object(),), stage="route"))
        result = self.choose(req)
        self.AOP.execute_interaction(result)
        payload = self.decisions()[0]["payload"]
        self.assertEqual(payload["filters"]["rejections"], [{"stage": "route", "reason_template": "route failed {}", "count": 3}])
        for candidate in payload["stages"][0]["candidates"]:
            self.assertEqual(candidate["score_components"]["values"]["rel_utility_score"], 1.5)
            self.assertEqual(candidate["score_components"]["values"]["buff_utility_score"], 2)
            self.assertIn("efficiency", candidate["score_components"]["missing"])
        json.dumps(payload, allow_nan=False)

    def test_disabled_observer_preserves_native_returns_and_rng_counts(self):
        selected = self.choose()
        enabled_counts = self.rng_calls, self.roll_calls
        self.capture.close()
        self.rng_calls = self.roll_calls = 0
        result = self.choose()
        self.assertIs(result, selected)
        self.assertEqual((self.rng_calls, self.roll_calls), enabled_counts)
        self.assertFalse(self.archiver.enabled)

    def test_external_archiver_consumer_is_not_disabled_on_close(self):
        self.archiver.archive_enable_fn(enableLog=True)
        self.capture.close()
        self.assertTrue(self.archiver.enabled)


if __name__ == "__main__":
    unittest.main(verbosity=2)
