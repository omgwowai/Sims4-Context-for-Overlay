"""Offline display labels with explicit provenance and a fail-closed fallback.

These labels describe resources, never classify/merge events or complete missing
participants. Source localization remains unchanged in organized/raw evidence.
"""

import json
import re


from .resources import resource_bytes

CATALOG = json.loads(resource_bytes("experience_labels.json").decode("utf-8"))
LABELS = {(r["kind"], r["id"], r["tuning_name"]): r for r in CATALOG["labels"]}
GOOD_STATUSES = {None, "resolved", "rule_resolved", "raw_text"}


def resolve_label(value, kind=None, tuning=None, game_version=None):
    """Resolve one compact resource/localization value; never guess from a prefix."""
    if not isinstance(value, dict):
        value = {"name": value}
    observed = value.get("name") or value.get("text")
    status = value.get("name_status", value.get("status"))
    tuning = tuning or value.get("tuning_name")
    kind = kind or value.get("kind")
    identifier = value.get("id")
    identity = [kind, identifier, tuning]
    # The syntax check also handles old compact payloads that lost their status.
    # Do not treat arbitrary English or a Sim's custom name as an internal key.
    malformed = bool(isinstance(observed, str) and
                     ("〈未解析：" in observed or re.search(r"\{\d+\.[^}]*\}", observed)))
    internal = bool(tuning and observed == tuning or
                    not value.get("key") and identifier and isinstance(observed, str) and
                    re.fullmatch(r"[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+", observed))
    valid = bool(observed) and status in GOOD_STATUSES and not (malformed or internal or value.get("name_unresolved"))
    reviewed = LABELS.get(tuple(identity)) if game_version in (None, CATALOG["game_version"]) else None
    row = {"observed": observed, "source_status": status, "identity": identity}
    if reviewed and (not valid or reviewed.get("override")):
        text = reviewed["text"]
        partial = status == "unresolved_tokens" or malformed or value.get("name_unresolved")
        if partial:
            text += "（名称参数缺失）"
        return dict(row, text=text, basis="reviewed_partial" if partial else "reviewed_exact_identity",
                    source=reviewed["source"])
    if valid:
        return dict(row, text=str(observed), basis="observed_name")
    noun = {"interaction": "活动", "object": "物件", "statistic": "数值", "relbit": "关系标记",
            "buff": "状态", "objective": "目标"}.get(kind, "资源")
    return dict(row, text="{}名称未解析{}".format(noun, "（{}）".format(identifier) if identifier else ""),
                basis="unresolved")


class LabelRenderer:
    """Collect display-quality issues by unit so they can be queried with its ref."""

    def __init__(self, game_version=None):
        self.game_version = game_version
        self.unit = None
        self.audit = []

    def __call__(self, value, kind=None, tuning=None):
        row = resolve_label(value, kind, tuning, self.game_version)
        if row["basis"] != "observed_name":
            row = dict(row, unit=self.unit)
            if row not in self.audit:
                self.audit.append(row)
        return row["text"]
