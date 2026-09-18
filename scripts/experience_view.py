"""Offline compatibility entry point for the shared experience core."""

import argparse
import hashlib
import json
from pathlib import Path

from tool_support import ROOT, report, write_text
from tool_support import report as emit_report
from offline import read_journal
from context_overlay.experience.experience_view import *


def analyze(path, entity_key=None, game_version=None, token_encoding=None):
    if path.suffix.lower() != ".jsonl":
        raise ValueError("Use a complete stable journal.jsonl, not a filtered entity packet")
    loaded = read_journal(path, "all", include_observations=False)
    if not loaded["complete"]:
        raise ValueError("Invalid journal: " + str(loaded["errors"]))
    result = build_experiences(loaded["events"], loaded["session_id"], entity_key, game_version)
    result["source"] = {"path": str(path.resolve()), "sha256": loaded["sha256"], "metadata": loaded["metadata"]}
    if token_encoding:
        import tiktoken
        encoding = tiktoken.get_encoding(token_encoding)
        selected = [e for e in loaded["events"] if entity_key is None or entity_key in e.get("entities", [])]
        for name, value in (("source_tokens", selected), ("consumer_tokens", result["consumer_packet"])):
            result["metrics"][name] = len(encoding.encode(json.dumps(value, ensure_ascii=False, allow_nan=False,
                                                                    separators=(",", ":")), disallowed_special=()))
        result["metrics"]["token_encoding"] = token_encoding
    return result


def markdown(result):
    packet, metrics = result["organized"], result["metrics"]
    lines = ["# 离线经历视图", "", "这是规则组织的事实视图；不是人物知情范围、心理独白或已验证的玩家经历。", "",
             "- 原始关联事件：{}".format(metrics["selected_events"]),
             "- 输出单元：{}".format(json.dumps(metrics["units_by_lane"], ensure_ascii=False)),
             "- 紧凑 JSON 字节：{} → {}（consumer_packet，不含审计和待核查）".format(
                 metrics["selected_events_json_bytes"], metrics["consumer_packet_json_bytes"]), "",
             "## 活动与已记录结果", ""]
    facts = {unit["id"]: unit for unit in packet["facts"]}
    names = result["consumer_packet"]["entities"]
    summaries = {unit["id"]: unit for unit in result["consumer_packet"]["activities"]}
    def entity_name(key):
        observed = names.get(key, {}).get("names_observed", [])
        return " / ".join(observed) if observed else key
    for unit in packet["activities"]:
        summary = summaries[unit["id"]]
        name = summary.get("action") or "未解释活动"
        start = (unit.get("started") or unit.get("time") or {}).get("display", "开始未知")
        end = (unit.get("ended") or {}).get("display", "未记录结束")
        lines += ["### {} · {} → {}".format(name, start, end), "",
                  "{}；执行步骤 {}；退出 {}；根交互结果分支 {}。".format(
                    "有开始记录" if unit["execution"] == "started" else "未观测到开始，保留为尝试",
                    unit["step_count"], unit["exit"], unit["result_branch"]), "",
                  "角色：{}。".format("；".join("{}：{}".format(row["role"], entity_name(row["entity_key"])) for row in unit["roles"])), ""]
        if summary.get("observed_action_name"):
            lines += ["名称按 tuning 身份核实；原记录名称：{}（{}）。".format(
                summary["observed_action_name"], summary["action_tuning"]), ""]
        for identifier in unit["effects"]:
            fact = facts.get(identifier)
            if fact is not None:
                lines.append("- {}：{}".format(fact["type"], json.dumps(fact.get("payload", fact.get("observations")), ensure_ascii=False)))
        lines += ["", "证据：{}。".format(", ".join(unit["evidence"])), ""]
    lines += ["## 状态与边界", "", "按真正主体及地块访问配对。未知边界不延伸到日志起止；相邻状态不自动构成因果。", ""]
    for unit in packet["states"]:
        lines.append("- {} · {} · {} → {} · {}".format(unit["subject"], json.dumps(unit["state"], ensure_ascii=False),
            (unit.get("started") or {}).get("display", "开始未知"),
            (unit.get("ended") or {}).get("display", "结束未知"), unit["boundary"]))
    lines += ["", "## 核查范围", "", "待核查 {} 个单元，外部记录 {} 条；完整内容及逐事件去向见 JSON。".format(
        len(result["review"]), len(result["external"])), "",
        "评分是游戏机制证据；first/last 样本不表示其间数值不变。局部数值效果不求全天净变化。", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--entity")
    parser.add_argument("--game-version", help="A different version disables curated resource rules")
    parser.add_argument("--token-encoding", help="Optional tiktoken encoding, e.g. o200k_base; never estimates tokens from bytes")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args()
    paths = [path.resolve() for path in (args.input, args.output, args.markdown) if path is not None]
    if len(paths) != len(set(paths)):
        raise ValueError("Input, JSON and Markdown paths must be distinct")
    result = analyze(args.input, args.entity, args.game_version, args.token_encoding)
    emit_report(result, result["metrics"], args.output, [args.input])
    if args.markdown is not None:
        write_text(args.markdown, markdown(result), [args.input, args.output])


if __name__ == "__main__":
    main()
