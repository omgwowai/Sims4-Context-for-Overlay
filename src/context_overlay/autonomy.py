"""Pure Autonomy selection summaries and bounded, identity-checked staging."""

import json
import math
import time
import weakref
from collections import Counter, OrderedDict


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if math.isfinite(result) else None
    return None


def selection_stage(pool, selected_index, mode, limit, describe):
    """pool contains the *observed* (weight, object) pairs, in engine order.

    describe is called only for retained rows. No game scoring or RNG runs here.
    """
    weights = [number(pair[0]) for pair in pool]
    total = sum(weights) if all(w is not None for w in weights) else None
    probabilities = None
    if mode == "weighted" and total is not None and total > 0 and all(w >= 0 for w in weights):
        probabilities = [w / total for w in weights]
    elif mode == "uniform" and pool:
        probabilities = [1.0 / len(pool)] * len(pool)
    elif mode == "deterministic" and selected_index is not None:
        probabilities = [float(i == selected_index) for i in range(len(pool))]
    order = sorted(range(len(pool)), key=lambda i: -(weights[i] if weights[i] is not None else -math.inf))
    retained = order[:limit]
    if selected_index is not None and selected_index not in retained:
        retained = order[:max(0, limit - 1)] + [selected_index]
        retained.sort(key=order.index)
    candidates = []
    for i in retained:
        row = describe(pool[i][1])
        row.update(candidate_index=i, rank=order.index(i) + 1, weight=weights[i],
                   tied_weight_count=weights.count(weights[i]), selected=i == selected_index,
                   probability=probabilities[i] if probabilities is not None else None)
        candidates.append(row)
    return {"mode": mode, "pool_count": len(pool), "total_weight": total,
            "selected_index": selected_index, "candidates": candidates,
            "omitted_count": len(pool) - len(candidates),
            "omitted_probability": (sum(p for i, p in enumerate(probabilities) if i not in retained)
                                     if probabilities is not None else None),
            "probability_basis": "complete_stage_pool" if probabilities is not None else "unavailable",
            "rank_basis": "descending_weight_stable_engine_order"}


class PendingDecisions:
    """Never hold a game object alive, and never match by Sim or recycled id."""
    def __init__(self, capacity=256, byte_limit=8 * 1024 * 1024, ttl=600, timer=time.monotonic):
        self.capacity, self.byte_limit, self.ttl, self.timer = capacity, byte_limit, ttl, timer
        self.entries = OrderedDict()
        self.bytes = self.peak_bytes = self.peak_count = 0
        self.drops = Counter()

    def discard(self, key, reason=None):
        entry = self.entries.pop(key, None)
        if entry is not None:
            self.bytes -= entry[3]
            if reason:
                self.drops[reason] += 1
        return entry

    def sweep(self, in_scope=None):
        now = self.timer()
        for key, (ref, data, when, charge) in list(self.entries.items()):
            interaction = ref()
            reason = ("object_released" if interaction is None else
                      "expired" if now - when > self.ttl else
                      "scope_exit" if in_scope and not in_scope(getattr(interaction, "sim", None)) else None)
            if reason:
                self.discard(key, reason)

    def put(self, interaction, data):
        self.sweep()
        # JSON size times four conservatively accounts for decoded containers.
        charge = len(json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")) * 4 + 512
        if charge > self.byte_limit:
            self.drops["oversized"] += 1
            return False
        try:
            ref = weakref.ref(interaction)
        except TypeError:
            self.drops["identity_not_weakrefable"] += 1
            return False
        self.discard(id(interaction), "superseded_same_instance")
        while self.entries and (len(self.entries) >= self.capacity or self.bytes + charge > self.byte_limit):
            self.discard(next(iter(self.entries)), "capacity")
        self.entries[id(interaction)] = (ref, data, self.timer(), charge)
        self.bytes += charge
        self.peak_bytes = max(self.bytes, self.peak_bytes)
        self.peak_count = max(len(self.entries), self.peak_count)
        return True

    def get(self, interaction):
        entry = self.entries.get(id(interaction))
        if entry is None:
            return None
        if entry[0]() is not interaction:
            self.discard(id(interaction), "identity_mismatch")
            return None
        if self.timer() - entry[2] > self.ttl:
            self.discard(id(interaction), "expired")
            return None
        return entry[1]

    def pop(self, interaction):
        data = self.get(interaction)
        if data is not None:
            self.discard(id(interaction))
        return data

    def clear(self, reason="session_end"):
        for key in list(self.entries):
            self.discard(key, reason)

    def status(self):
        return {"pending": len(self.entries), "estimated_bytes": self.bytes,
                "peak_pending": self.peak_count, "peak_estimated_bytes": self.peak_bytes,
                "capacity": self.capacity, "byte_limit": self.byte_limit, "ttl_seconds": self.ttl,
                "drops": dict(self.drops)}
