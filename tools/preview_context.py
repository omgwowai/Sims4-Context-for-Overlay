"""Read one Experience recording directory and export an offline context preview.

Standard library only. Never imports the game or the reference MOD. The snapshot
is explicitly unavailable: this tool demonstrates history selection and rendering,
not a live game integration. See docs/context-query-and-output-mvp.md.
"""

import argparse
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid


SCHEMA_VERSION = "sims4-context/0.1"
PROFILE = "interaction_records_v1"
MAPPER_VERSION = "preview-zh-CN/0.1"
SIM_TUNINGS = {"object_sim", "SimInfo"}
# Prototype glosses, not official game localization or inferred story outcomes.
VERBS = {
    "sim_Chat": "聊天",
    "socialMixer_Greetings_Wave": "挥手打招呼",
    "chess_practice": "练习国际象棋",
    "Food_Eat_Active": "进食（主动交互）",
    "Food_Eat_Passive": "进食（被动交互）",
    "reaction_SmellGood": "对气味作出正面反应",
    "stand_Passive": "站立（内部姿态交互）",
    "social_adjustment": "社交位置调整（内部步骤）",
    "Emotion_Idle": "情绪待机（内部交互）",
    "Idle_Chatting_STC": "聊天待机（内部交互）",
    "idle_Chatting_ListenWithPhone_STC": "持手机倾听（聊天内部交互）",
    "npc_leave_lot_now": "离开地块",
    "NPCLeaveLotNow_NPC_WaveGoodBye": "离场时挥手告别",
    "NPCLeaveLot_Player_WaveGoodBye": "挥手告别",
}
OBJECTS = {"object_chess_table": "国际象棋桌", "object_Food_GrilledSteak": "烤牛排"}
GAME_TIME = re.compile(r"^(\d+):(\d+):(\d+)(?:\.(\d+))? day:(\d+) week:(\d+)$")


def game_milliseconds(value):
    match = GAME_TIME.fullmatch(value or "")
    if match is None:
        return None
    hour, minute, second, fraction, day, week = match.groups()
    hour, minute, second, day, week = map(int, (hour, minute, second, day, week))
    if hour >= 24 or minute >= 60 or second >= 60 or day >= 7:
        return None
    return (((week * 7 + day) * 24 + hour) * 3600 + minute * 60 + second) * 1000 + int(((fraction or "") + "000")[:3])


def wire_value(value, key=""):
    """Keep 64-bit identities exact when a JSON consumer uses JavaScript numbers."""
    if isinstance(value, dict):
        return {k: wire_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [wire_value(v, "sim_id" if key == "participants" else key) for v in value]
    if isinstance(value, int) and not isinstance(value, bool):
        if key in {"id", "guid64", "by"} or key.endswith("_id") or abs(value) > 9007199254740991:
            return str(value)
    return value


def load_events(directory):
    directory = Path(directory)
    files = sorted(directory.glob("*.jsonl"))
    if not files:
        raise ValueError("No JSONL files: choose one recording's leaf events directory.")
    rows, locations = [], {}
    for path in files:
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except ValueError as exc:
                raise ValueError("Invalid JSON at {}:{}: {}".format(path.name, line_number, exc))
            if not isinstance(event, dict) or not isinstance(event.get("event_id"), str):
                raise ValueError("Missing event_id at {}:{}".format(path.name, line_number))
            rows.append(event)
            locations.setdefault(event["event_id"], []).append({"file": path.name, "line": line_number})
    return rows, locations


def _sim_name(sim_id, names):
    entry = names.get(str(sim_id), {})
    return entry.get("name") or "Sim {}".format(sim_id if sim_id is not None else "未知")


def _ref_matches(ref, kind, identity):
    if not isinstance(ref, dict) or ref.get("id") is None:
        return False
    ref_kind = "sim" if ref.get("tuning_name") in SIM_TUNINGS else "object"
    # Untyped references cannot safely be assigned to the Object namespace.
    return bool(ref.get("tuning_name")) and ref_kind == kind and str(ref["id"]) == identity


def _ref_name(ref, names):
    if ref.get("tuning_name") in SIM_TUNINGS:
        return _sim_name(ref.get("id"), names)
    raw = ref.get("tuning_name") or "未解析物件"
    return "{}（{}）".format(OBJECTS.get(raw, raw), ref.get("id", "未知 ID"))


def _roles(events, kind, identity):
    roles = set()
    for event in events:
        if kind == "sim":
            if str(event.get("sim_id")) == identity:
                roles.add("actor" if event["kind"] == "did" else "recorded_participant")
            if event["kind"] == "received" and str(event.get("by")) == identity:
                roles.add("actor_reference")
            if any(str(sid) == identity for sid in event.get("participants") or []):
                roles.add("participant_reference")
        if _ref_matches(event.get("target"), kind, identity):
            roles.add("direct_target")
    return sorted(roles)


def _recorded_order(event):
    stamp = event.get("ts_real")
    if isinstance(stamp, bool) or not isinstance(stamp, (float, int)) or not math.isfinite(stamp):
        raise ValueError("Invalid ts_real in {}".format(event["event_id"]))
    sequence = re.fullmatch(r"e-(\d+)-(\d+)", event["event_id"])
    tie = tuple(map(int, sequence.groups())) if sequence else (-1, -1)
    return (stamp, tie, event["event_id"])


def _render(event, names):
    raw_status = str(event.get("status") or "unknown")
    status = raw_status.lower()
    if status in {"complete", "finished", "success"}:
        outcome, phrase = "completed", "交互完成"
    elif status in {"canceled", "cancelled", "user_cancel"}:
        outcome, phrase = "cancelled", "交互被取消"
    elif status in {"failure", "failed"}:
        outcome, phrase = "failed", "交互失败"
    else:
        outcome, phrase = "unknown", "记录状态为 {}（未确认完成）".format(raw_status)
    verb = event.get("verb", "")
    label = VERBS.get(verb, "未映射交互：{}".format(verb))
    if event["kind"] == "received":
        text = "记录到 {} 作为参与方的“{}”经历，发起者 {}；{}。".format(
            _sim_name(event.get("sim_id"), names), label, _sim_name(event.get("by"), names), phrase)
    else:
        text = "记录到 {} 的“{}”{}。".format(_sim_name(event.get("sim_id"), names), label, phrase)
    if event.get("target"):
        text += "交互目标：{}。".format(_ref_name(event["target"], names))
    if event.get("ts_game"):
        match = GAME_TIME.fullmatch(event["ts_game"])
        time_text = event["ts_game"]
        if match:
            hour, minute, second, _, day, week = match.groups()
            time_text = "第 {} 周·第 {} 日 {}:{}:{}".format(week, day, hour, minute, second)
        text = "[{}] {}".format(time_text, text)
    return {"text": text, "locale": "zh-CN", "mapper_version": MAPPER_VERSION,
            "label_source": "prototype_gloss" if verb in VERBS else "raw_fallback",
            "outcome": outcome, "raw_status": raw_status}


def build_preview(events, target_kind, target_id, source_scope, names=None, limit=5, locations=None):
    if target_kind not in {"sim", "object"} or not str(target_id).isdigit() or not source_scope:
        raise ValueError("A typed numeric identity and explicit source_scope are required.")
    if not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    target_id, names, locations = str(target_id), names or {}, locations or {}
    by_id, duplicates = {}, 0
    for event in events:
        eid = event["event_id"]
        if eid in by_id:
            if event != by_id[eid]:
                raise ValueError("Conflicting payloads for event_id {}".format(eid))
            duplicates += 1
        by_id[eid] = event
    # Only a checked received -> did link identifies a second view of one record.
    # Other parent links express causes/containers and must not be folded this way.
    groups, unlinked_received = {}, set()
    for event in by_id.values():
        if event.get("kind") not in {"did", "received"}:
            continue
        anchor = event
        if event["kind"] == "received":
            parent = by_id.get(event.get("parent_id"))
            if (parent is not None and parent.get("kind") == "did"
                    and event.get("by") is not None
                    and str(parent.get("sim_id")) == str(event["by"])
                    and parent.get("verb") == event.get("verb")):
                anchor = parent
            else:
                unlinked_received.add(event["event_id"])
        groups.setdefault(anchor["event_id"], {"anchor": anchor, "views": []})["views"].append(event)
    matching = []
    for group in groups.values():
        group["roles"] = _roles(group["views"], target_kind, target_id)
        if group["roles"]:
            matching.append(group)
    game_ordered = all(game_milliseconds(g["anchor"].get("ts_game")) is not None for g in matching)
    def sort_key(group):
        event = group["anchor"]
        return ((game_milliseconds(event.get("ts_game")),) if game_ordered else ()) + _recorded_order(event)
    matching.sort(key=sort_key, reverse=True)
    selected = matching[:limit]
    items, evidence = [], {}
    for group in selected:
        anchor, views = group["anchor"], group["views"]
        view_ids = sorted({e["event_id"] for e in views})
        rendering = _render(anchor, names)
        issues = []
        if anchor["event_id"] in unlinked_received:
            issues.append("unresolved_actor_record")
        if len({str(e.get("status") or "unknown").lower() for e in views}) > 1:
            issues.append("view_status_disagreement")
            rendering["text"] += "各参与视角的状态记录有差异；这里显示主记录的状态。"
        if target_kind == "sim" and str(anchor.get("sim_id")) != target_id:
            if "recorded_participant" in group["roles"]:
                role_text = "记录中的参与方"
            elif "direct_target" in group["roles"]:
                role_text = "交互目标"
            elif "actor_reference" in group["roles"]:
                role_text = "记录中引用的发起者"
            else:
                role_text = "参与者列表中的一员"
            rendering["text"] += "{} 是{}。".format(_sim_name(target_id, names), role_text)
        items.append({"record_id": anchor["event_id"], "relevance": group["roles"],
                      "recorded_at_game": anchor.get("ts_game"), "rendering": rendering,
                      "evidence_ids": view_ids, "issues": issues})
        for event in views:
            evidence[event["event_id"]] = {"event": wire_value(event), "locations": locations.get(event["event_id"], [])}
    ref = next((e["target"] for e in by_id.values()
                if _ref_matches(e.get("target"), target_kind, target_id)), {"id": target_id})
    target_name = _sim_name(target_id, names) if target_kind == "sim" else _ref_name(ref, names)
    valid_times = [(game_milliseconds(e.get("ts_game")), e["ts_game"]) for e in by_id.values()
                   if game_milliseconds(e.get("ts_game")) is not None]
    return {
        "schema_version": SCHEMA_VERSION, "context_id": "ctx-" + uuid.uuid4().hex,
        "mode": "offline_preview",
        "scope": {"source_scope": source_scope, "save_id": None, "branch_id": None,
                  "continuity": "unverified"},
        "target": {"kind": target_kind, "id": target_id, "display_name": target_name,
                   "name_basis": "catalog_or_tuning_gloss_not_historical_name"},
        "request": {"history_profile": PROFILE, "limit": limit, "perspective": "player_observer"},
        "snapshot": {"status": "unavailable", "reason": "offline_history_only",
                     "captured_at_game": None, "fields": {}},
        "history": {"items": items, "returned": len(items), "matching_records": len(matching),
                    "has_more": len(matching) > limit,
                    "ordering": "game_time_then_recorded_time" if game_ordered else "recorded_time_fallback",
                    "evidence_closure": "selected_interactions_and_received_views_only"},
        "evidence": evidence,
        "coverage": {"source_records": len(by_id), "identical_duplicates_removed": duplicates,
                     "observed_game_time_min": min(valid_times)[1] if valid_times else None,
                     "observed_game_time_max": max(valid_times)[1] if valid_times else None,
                     "completeness": "unknown", "live_buffer_included": False,
                     "limitations": ["single_recording_input_not_verified_save_branch",
                                     "recorded_terminal_interactions_only_including_internal_steps",
                                     "object_direct_targets_only",
                                     "no_general_lifecycle_deduplication_or_causal_closure",
                                     "names_may_differ_from_names_at_event_time"]},
    }


def write_packet(packet, output):
    """File sink: publish a whole UTF-8 JSON packet using an atomic replacement."""
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".context-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(packet, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events-dir", required=True, type=Path)
    parser.add_argument("--names", type=Path)
    parser.add_argument("--source-scope", required=True)
    parser.add_argument("--target-kind", choices=("sim", "object"), required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        events, locations = load_events(args.events_dir)
        names = json.loads(args.names.read_text(encoding="utf-8")).get("sims", {}) if args.names else {}
        packet = build_preview(events, args.target_kind, args.target_id, args.source_scope,
                               names=names, limit=args.limit, locations=locations)
        write_packet(packet, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
    print("Wrote {} records to {}".format(packet["history"]["returned"], args.output))


if __name__ == "__main__":
    main()
