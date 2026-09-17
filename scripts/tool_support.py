"""Small shared utilities for offline tools; never installed in the game."""

import ctypes
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path
import sys
from types import CodeType
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
GAME = Path("D:/Games/The Sims 4")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_text(output, text, inputs=()):
    if any(path is not None and output.resolve() == path.resolve() for path in inputs):
        raise ValueError("Use a separate output path to retain original inputs")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(text, encoding="utf-8")


def report(result, summary, output=None, inputs=()):
    if output is not None:
        write_text(output, json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2) + "\n", inputs)
    print(json.dumps(dict(summary, report=str(output) if output else None), ensure_ascii=False), flush=True)


def process_memory():
    if os.name != "nt":
        return None
    size = ctypes.c_size_t
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
            (name, size) for name in ("peak_working_set", "working_set", "peak_paged_pool", "paged_pool",
                                     "peak_nonpaged_pool", "nonpaged_pool", "pagefile", "peak_pagefile", "private_bytes")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return {key: getattr(counters, key) for key in ("working_set", "peak_working_set", "private_bytes")}


class GameBytecode:
    """Read and identify code objects, without importing or executing EA code."""
    def __init__(self, game=GAME):
        self.directory = game / "Data/Simulation/Gameplay"
        self.hashes = {}
        self.modules = {}

    @property
    def available(self):
        return sys.version_info[:2] == (3, 7) and (self.directory / "simulation.zip").is_file()

    def symbol(self, member, suffix, archive="simulation.zip"):
        key = archive + ":" + member
        if key not in self.modules:
            with ZipFile(str(self.directory / archive)) as source:
                raw = source.read(member)
            if len(raw) < 16 or raw[:4] != importlib.util.MAGIC_NUMBER:
                raise ValueError("Bytecode magic differs from interpreter")
            code = marshal.loads(raw[16:])
            self.modules[key] = code
            self.hashes[key] = hashlib.sha256(raw).hexdigest()

        def walk(code, path=""):
            path += code.co_name
            if path == suffix or path.endswith("." + suffix):
                return code
            for child in code.co_consts:
                if isinstance(child, CodeType):
                    found = walk(child, path + ".")
                    if found is not None:
                        return found
        found = walk(self.modules[key])
        if found is None:
            raise LookupError("Symbol not found: " + suffix)
        return found
