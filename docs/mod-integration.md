# ContextOverlay 0.3.2：数据与其他 MOD 接入

ContextOverlay 在同一个脚本 MOD 中提供当前状态采集、事件记录和规则中文解释。调用者选择实体和字段，取得可 JSON 序列化的数据，再自行组织 Prompt、调用模型并展示 Overlay。

当前有两条接入路径：**导出 JSON 文件**适合先做玩法原型和离线验证；**游戏线程内直接调用**适合其他脚本 MOD 的即时需求。游戏内查看窗口已使用直接调用方式。0.3.2 尚未提供稳定公共 SDK、HTTP 服务或事件推送订阅接口，直接调用示例明确绑定本版内部接口。

## 1. 数据保存在哪里

运行数据在游戏用户目录的 `ContextOverlay/`，与 `Mods/ContextOverlay/` 中的程序文件分开：

| 文件 | 用途 |
| --- | --- |
| `runtime.log` | 加载、运行启停、查看窗口和错误信息 |
| `runs/<session_id>/journal.jsonl` | 每行一个 JSON，按序追加观测与事件修订 |
| `runs/<session_id>/context-<request_id>.json` | 单次 Context 或历史导出的完整 JSON 数据包 |
| `config.json` | 可选配置；不存在时使用默认值 |

重启、读档、换地块或 `co.restart` 都开启独立运行，当前历史查询只覆盖本次运行。旧日志保留，不自动跨存档或跨运行合并。

## 2. 消费哪些字段

| 位置 | 含义 |
| --- | --- |
| `schema_version`、`module_version`、`session_id`、`request_id` | 数据协议、MOD 版本、运行与请求身份 |
| `target` | 实体种类、十进制字符串 ID、名称及 `sim:ID` / `object:ID` 键 |
| `scope`、`read_started`、`read_finished` | 观测范围和本次读取前后的游戏时间 |
| `snapshot.<字段>.status/value/source/reason` | 当前字段的可用性、值、来源和缺失原因 |
| `history.events` | 关联该实体的事件；包含事件 ID、修订、参与实体、阶段、来源和可观测结果 |
| `history.coverage`、`target_observation` | 记录器状态、持久化进度与该实体的观测边界；`target_observation` 位于 `history` 内 |
| `rendered.current`、`rendered.history` | 逐项／逐条中文解释，引用原始字段或事件 ID 与修订 |
| `provenance` | 构建版本、EA 源码参考、源码和中文词表摘要等依据 |

Context 当前可选字段：`identity`、`time`、`location`、`interactions`、`needs`、`buffs`、`relationships`、`object_states`。按实体类型选择，不能把 `unsupported`、`out_of_scope`、`disabled` 或 `error` 当成数值 0。“complete”也不表示历史上所有事实都已被观察。

中文解释由**资源字典和规则模板**生成，无需大语言模型。无法解析的名称保留原始标识，未知结果保留未知；不会从时间相近推断行为导致了某个状态变化。即使选择 `text`，仍附带可追溯的原始证据。

所有实体、资源和交互 ID 均作为字符串处理，避免 JavaScript 数值精度损失；游戏 ticks 与现实时间分开处理。需求值是游戏内部数值，不是百分比。

`journal.jsonl` 的 `kind` 区分 `observation` 与 `event_revision`。同一个事件会随排队、开始、结束等证据追加新修订，不能把每个修订都计成一个新事件。离线按 `session_id + event_id` 归并并保留最新合法修订；可复用 `context_overlay.storage.replay`，或使用 `scripts/validate_run.py` 审计。原始日志会保留旧修订和观测证据。

## 3. 先用文件做原型

游戏中按 `Ctrl + Shift + C` 打开控制台，输入：

```text
co.status
co.export sim active 15 false both true identity,time,needs,buffs,relationships,interactions
```

这会导出当前操控 Sim 的选定状态、最近 15 条主要事件及中文解释。命令返回 `request_id`、`path` 和 `status: queued`；等待该请求在 `co.status` 中变为 `written` 并出现完整目标文件后，再消费内容。**入队成功不是写盘完成**。不要读取临时文件，也不要用“最新修改文件”猜测请求归属。

外部 Python 程序读取命令返回的具体路径：

```python
import json
from pathlib import Path

packet = json.loads(Path(export_path).read_text(encoding="utf-8"))
assert packet["schema_version"] == "1"
current = packet.get("rendered", {}).get("current", [])
history = packet.get("rendered", {}).get("history", [])
lines = [item["text"] for item in current + history]
context_text = "\n".join(lines)
# 将所需的原始数据、context_text 和自己的 Prompt 交给玩法程序。
```

只读历史可用 `co.history sim active 15 false`。需要分页时：

```text
co.history_query sim active 15 false
co.history_next 返回的next_cursor
co.history_close 此查询返回的cursor
```

每个历史页也导出为 JSON。完整过滤参数和配置见[运行与调试](runtime-usage.md)。外部文件消费者目前不会自动触发游戏内查询；实时自动调用应由自己的游戏 MOD 在游戏线程上发起。

## 4. 其他 MOD 在运行时直接调用

在自己的 Python 3.7 脚本 MOD 中引用已安装的 `context_overlay`，不要复制整个核心包造成重复加载。示例见 [examples/mod_consumer.py](../examples/mod_consumer.py)。该文件可放进自己的命名空间，并从自己的交互或游戏回调调用：

```python
from my_mod.mod_consumer import capture_context

packet = capture_context("sim", "active")
# packet 是普通字典；不经过控制台、不要求先落盘。
```

核心调用为：

```python
from context_overlay import game_runtime

runtime = game_runtime._runtime
# 完整示例先检查版本、运行就绪和 closed 状态。
packet = runtime.collector.collect(
    "sim", "active",
    fields=("identity", "needs", "buffs", "interactions"),
    include_history=True, history_limit=15,
    include_internal=False, representation="both")
```

接入规则：

- 等地块加载完成，在**游戏模拟线程**的回调内采集；不要在模块导入阶段、后台线程或模型网络回调中直接访问游戏对象。
- 每次调用重新获取当前 runtime；旅行、读档或重启后旧引用和游标失效。检查 `session_id`，也检查返回字段状态与记录器的失败／关闭状态。
- 返回的 JSON 数据可交给自己的后台处理。模型请求、网络和重计算放在后台；展示或游戏对象操作再经自己 MOD 的游戏线程回调执行，避免阻塞游戏。
- 模块缺失时会有 `ImportError`，运行未就绪、字段或查询无效会报错；消费方应捕获并显示暂不可用，不默认返回空白成功。`HistoryError.code` 区分游标过期、运行改变和查询预算等原因。
- 原始页面不会自动生成你自己的 Overlay。Prompt、模型连接、显示窗口与刷新时机由消费 MOD 负责；本 MOD 不自动上传这些数据。

按实体、时间和类型分页的示例：

```python
from my_mod.mod_consumer import open_history, next_history, close_history

handle = open_history("sim", "active", event_types=["interaction"])
page = handle["page"]
# 使用 page["events"] 展示第一页；按需翻页，不一次取完全部历史。
if page["has_more"]:
    page = next_history(handle["session_id"], page["next_cursor"])
# 用户关闭自己的窗口时释放；任何同查询的 cursor 都可用于关闭。
close_history(handle["session_id"], page["cursor"])
```

时间过滤使用 `from_ticks`、`to_ticks`，区间为 `[from, to)`，`time_field` 可为 `first_observed`、`started`、`ended`。每页 1–500 条，例子使用 15 条。查询固定第一次创建时的事件版本；刷新需新建查询。游标默认 120 秒现实时间后过期，暂停游戏也计时；消费端应捕获过期后重新查询。读取第一页后也要及时释放未使用的查询，关闭异常可在自己 MOD 的清理逻辑中处理。

示例通过离线替身检查调用契约，不代表已完成任意第三方 MOD 的兼容验收。初步接入先固定 0.3.2，再根据实际需求整理稳定的公共接口。

## 5. 当前边界

默认最多记录 200,000 个事件，并有记录内存、查询快照和磁盘预算；超预算会明确报错，不能理解为无限历史。默认关闭连续需求变化历史，仍可即时查询需求；Buff、关系标记、物件状态等已接入事件继续记录。当前范围是本地块已实例化的实体，不包括场外、跨运行记忆或任意历史时刻的完整状态重建。
