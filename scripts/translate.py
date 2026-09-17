"""Render exported JSON or a complete JSONL journal; Markdown by default."""

import argparse
import html
import json
from pathlib import Path
import re

from offline import load_catalog, read_packet, translate
from tool_support import write_text
from context_overlay.semanticizer import DETAIL_BASES, display, explain_event, game_time, render


def markdown_text(value):
    text = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]#!|])", r"\\\1", text).replace("\r", "").replace("\n", "  \n")


def markdown_report(packet, source, include_internal=False, catalog=None):
    events = packet.get("history", {}).get("events", [])
    visible = [event for event in events if include_internal or event.get("tier") != "internal"
               or event.get("category") == "autonomy.decision"]
    view = dict(packet, history=dict(packet.get("history", {}), events=visible))
    view = catalog.enrich(view) if catalog is not None else view
    rows = render(view)
    lines = ["# ContextOverlay 离线报告", "", "来源：" + markdown_text(source.name), "",
             "运行：" + markdown_text(packet.get("session_id", "未提供")), ""]
    if view.get("target"):
        lines += ["目标：" + markdown_text(display(view["target"])), ""]
    if packet.get("status"):
        lines += ["数据包状态：" + markdown_text(packet["status"]), ""]
    lines += ["事件：{} / {}（{}；每个事件保留最终修订）。".format(
        len(visible), len(events), "包含内部交互" if include_internal else "主层事件与 Autonomy"), "",
        "以下内容依据已记录数据生成；缺失记录不代表没有发生，事件日志不能恢复任意时刻的完整状态。", ""]
    lines += ["事件时间为该事件最后一次观测的游戏时间。", ""]
    if catalog:
        lines += ["文本使用显式提供的资源目录重新解释；静态资源参考不代表当时实测状态。", ""]
    if rows["current"]:
        lines += ["## 当前状态", ""]
        lines += ["- " + markdown_text(row["text"]) for row in rows["current"]]
        lines.append("")
    if packet.get("kind") == "nearby_entities":
        lines += ["## 附近实体", ""]
        for item in view.get("results", []):
            lines += ["- {}；水平距离 {} 游戏单位。".format(markdown_text(display(item["entity"])),
                       markdown_text(item.get("distance", {}).get("horizontal", "未知")))]
        lines.append("")
    lines += ["## 事件记录", ""]
    for event, row in zip(view["history"]["events"], rows["history"]):
        lines += ["### " + markdown_text(game_time(row.get("game_time"))), "", markdown_text(row["text"]), ""]
        if row.get("decision_details"):
            lines += ["<details>", "<summary>Autonomy 候选与评分</summary>", "",
                      markdown_text(row["decision_details"]), "", "</details>", ""]
        if "payload_json" in row:
            lines += ["<details>", "<summary>外部 JSON 内容</summary>", "",
                      "<pre>" + html.escape(row["payload_json"]) + "</pre>", "", "</details>", ""]
        if event.get("effects"):
            lines += ["<details>", "<summary>关联效果</summary>", ""]
            lines += ["- " + markdown_text(explain_event(effect)["text"]) for effect in event["effects"]]
            lines += ["", "</details>", ""]
        lines += ["事件：{}；修订 {}。".format(markdown_text(row["event_id"]), row["revision"]), ""]
    if not visible:
        lines += ["当前范围内没有可显示的事件。", ""]
    details = rows.get("resource_details", {})
    if details.get("items"):
        lines += ["## 资源说明", ""]
        for item in details["items"]:
            lines += ["- {}：{}（{}；{}）".format(markdown_text(item["label"]), markdown_text(item["text"]),
                      markdown_text(DETAIL_BASES[item["basis"]]), markdown_text(item["status"]))]
        if details.get("truncated"):
            lines += ["", "资源说明已截断，更多内容保留在输入或完整 JSON 中。"]
    return "\n".join(lines).rstrip() + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--include-internal", action="store_true", help="Include internal interactions in Markdown; JSON always keeps all events")
    parser.add_argument("--strings", type=Path)
    parser.add_argument("--catalog", type=Path, help="v2 resource_catalog.json from build_resource_catalog.py")
    parser.add_argument("--string-sources", type=Path)
    args = parser.parse_args()
    output = args.output or args.input.with_name(args.input.stem + (".md" if args.format == "md" else "-translated.json"))
    if args.format == "md" and output.suffix.lower() == ".json":
        parser.error("Use --format json for JSON output, or an .md output path")
    inputs = [path for path in (args.input, args.strings, args.catalog, args.string_sources) if path]
    catalog, _ = load_catalog(args.strings, args.catalog, args.string_sources)
    packet, _ = read_packet(args.input)
    text = (json.dumps(translate(packet, catalog), ensure_ascii=False, indent=2) + "\n" if args.format == "json"
            else markdown_report(packet, args.input, args.include_internal, catalog))
    write_text(output, text, inputs)
    print(str(output))


if __name__ == "__main__":
    main()
