"""Reusable test data and isolated API runtimes; no test cases live here."""

import copy
from pathlib import Path
import sys
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "scripts")]
from context_overlay.collector import Collector
from context_overlay.autonomy_capture import AutonomyCapture
from context_overlay.event_sources import EventSources
from context_overlay.model import entity, field
from context_overlay.recorder import Recorder

PYTHON = [sys.executable, "-B", "-X", "utf8"]


class MemoryJournal:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(copy.deepcopy(record))
        return len(self.records)

    def status(self):
        return {"error": None, "accepted_sequence": len(self.records), "durable_sequence": len(self.records)}


def facts(interaction_id=10, target_id=123, main=True):
    return {"actor": entity("sim", 18446744073709550001, "阿明"),
            "target": entity("object", target_id, "食物", 888),
            "interaction_id": str(interaction_id), "tuning_id": "321",
            "name": "吃饭", "tier": "main" if main else "internal",
            "trigger": {"name": "SCRIPT_WITH_USER_INTENT", "value": 10}}


class Adapter:
    def __init__(self):
        self.reads, self.resolutions, self.shared = 0, [], {"value": 42}

    def resolve(self, kind, identifier):
        self.resolutions.append((kind, identifier))
        if kind == "sim":
            return facts()["actor"] if identifier == "active" else entity("sim", identifier, "阿明")
        return entity("object", identifier, "食物")

    def scope(self):
        return {"kind": "active_lot_instantiated", "off_lot": "excluded"}

    def clock(self):
        return {"ticks": "100", "display": "test time"}

    def read(self, target, name):
        self.reads += 1
        return field(target if name == "identity" else self.shared)


def runtime_fixture(test, adapter=None, recorder=None, session_id="test-run", provenance=None):
    from context_overlay import game_runtime
    adapter = Adapter() if adapter is None else adapter
    recorder = Recorder(MemoryJournal(), session_id=session_id) if recorder is None else recorder
    runtime = game_runtime.Runtime.__new__(game_runtime.Runtime)
    runtime.__dict__.update(adapter=adapter, recorder=recorder,
        config=dict(game_runtime.DEFAULTS),
        collector=Collector(adapter, recorder, provenance=provenance), provenance=provenance or {},
        session_id=recorder.session_id, simulation_thread_id=threading.get_ident(), api_ready=True, closed=False)
    runtime.sources, runtime.autonomy = EventSources(runtime), AutonomyCapture(runtime)
    for context in (patch.object(game_runtime, "_runtime", runtime),
                    patch.object(game_runtime, "_startup_error", None)):
        context.start()
        test.addCleanup(context.stop)
    return runtime
