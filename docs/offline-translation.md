# 离线翻译 JSONL 与导出的 JSON

日期：2026-09-16。

游戏内“查看状态与历史”使用语义模块的 display、explain_event 和 detail_text 生成状态与事件说明；普通 Context／历史导出通过 render 生成 rendered。采集阶段的名称与资源文本解析在 JSONL 写入之前已经执行，并保留当时的 hash、tokens、解析状态和来源。离线调用不导入游戏服务，也不要求游戏正在运行。

当前 semanticizer_enabled 开关控制 Context／历史 API 和导出是否附加 rendered；采集阶段的 Localizer 和 Inspector 自身的文字展示没有由此关闭。

## 一、整轮 JSONL → 事件数据包 → 中文说明

当前 scripts/translate.py 接受单个 JSON 数据包，不能直接传 journal.jsonl。以下完整命令在 PowerShell 中执行。示例输入是本轮已经复制并核验过的暂停日志快照；翻译其他轮次时只改 $journalPath 为对应 journal.jsonl 路径。输出放在 .validation/offline-translation，重复执行会更新这些派生文件，原日志不改写。

读取仍在增长的日志前，先使用已结束的运行文件或稳定副本。命令先验证日志序列、会话及修订完整性，遇到损坏停止；之后保留每个 event_id 的最新修订，不把一个交互的多个修订计成多条事件。包含主层、内部层和 Autonomy，不套用 Context 最近 50 条限制。诊断 observation 不转成事件说明。为覆盖磁盘中的整轮历史，整理阶段保留已被内存 FIFO 淘汰的事件；这不同于用于恢复内存保留范围的 replay 结果。

```powershell
Set-Location "C:\sources\Sims4-Context-for-Overlay"
$semanticPython = "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe"
$journalPath = ".validation/paused-live-20260916T073317840510Z/journal.jsonl"
$packetPath = ".validation/offline-translation/history-input.json"
$translatedPath = ".validation/offline-translation/history-readable.json"

@'
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path("src").resolve()))
from context_overlay.storage import replay

source, output = map(Path, sys.argv[1:3])
if source.resolve() == output.resolve():
    raise SystemExit("Use a separate output path")
checked = replay(source, include_observations=False)
if not checked["complete"]:
    raise SystemExit("Invalid journal: " + str(checked["errors"]))
session_id = checked["session_id"]
del checked

# Keep the latest revision of EVERY recorded event, including FIFO-evicted ones.
events = {}
with source.open(encoding="utf-8-sig") as stream:
    for line in stream:
        record = json.loads(line)
        if record["kind"] == "event_revision":
            event = record["event"]
            previous = events.get(event["event_id"])
            if previous is None or event["revision"] > previous["revision"]:
                events[event["event_id"]] = event

packet = {"kind": "history", "session_id": session_id,
          "history": {"events": list(events.values())}}
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
print("Prepared {} events: {}".format(len(events), output))
'@ | & $semanticPython -X utf8 - $journalPath $packetPath

if ($LASTEXITCODE -ne 0) { throw "JSONL conversion failed" }
& $semanticPython -X utf8 scripts/translate.py $packetPath $translatedPath
if ($LASTEXITCODE -ne 0) { throw "Translation failed" }
```

history-input.json 是整理后的事实数据包；history-readable.json 保留相同 history，并新增 rendered.history。每个事件有 text、event_id、revision 等，Autonomy 另有 decision_details。没有 snapshot，因为事件日志不能恢复任意时刻完整的 Context 当前快照。

## 二、用本地资源目录重新解析名称和说明

基础命令使用日志中已有的名称和描述生成事件说明。需要应用更新后的词表／本地化解析规则并补充静态资源参考时，接着执行：

```powershell
& $semanticPython -X utf8 scripts/translate.py $packetPath ".validation/offline-translation/history-reinterpreted.json" `
  --strings ".local/resource-semantics/strings_zh.json" `
  --string-sources ".local/resource-semantics/string_sources.json" `
  --catalog ".local/resource-semantics/resource_catalog.json"
```

本机上述三个目录文件已存在；换机器或游戏升级后，应按 [资源目录构建说明](resource-semantics.md#构建与离线使用) 准备与目标日志相匹配的资源。额外输入与其摘要会记录在输出中。

history-reinterpreted.json 的 history 仍是原始事实，semantic_view.history 是重新解析的视图，rendered 是中文说明。静态补充标记在 reference_semantics，不能当作当时实测文本。未采到的历史动态参数无法事后可靠补回；未知名称和不支持语法仍保留缺失标记。能为所有事件生成说明不等于所有资源文本均已完整解析。

## 三、直接翻译 Context／历史 JSON 导出

如果已有 context-*.json 等完整数据包，可以跳过第一节的 JSONL 整理：

```powershell
Set-Location "C:\sources\Sims4-Context-for-Overlay"
$semanticPython = "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe"
& $semanticPython -X utf8 scripts/translate.py "输入数据包.json" "新的中文数据包.json"
```

同样可以附加第二节的 --strings、--string-sources、--catalog。输入与输出必须使用不同路径。

2026-09-16 已用本轮 `9ba4280cb22d4c65b469cad11439a0c7` 的稳定日志快照执行第一、二节命令：两份结果均生成 8,057 条事件说明，逐项 event_id／revision 与原事件对应，原 history 完全一致，原 JSONL 的 SHA-256 未改变。基础输出约 96 MB；包含独立 semantic_view 的资源重解释输出约 211 MB。
