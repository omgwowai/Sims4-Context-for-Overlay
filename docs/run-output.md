# 写盘状态与自动分层文件

从 ContextOverlay **0.10.1** 开始，正常结束一次运行时，MOD 在排空日志写入后生成分层文件。无需另外安装演示 MOD。API／SDK 仍为 2.2.0，原始日志和已有接口保持兼容。

## 去哪里查看

游戏用户目录下 `ContextOverlay/runs/<session>/` 包含：

| 文件 | 用途 |
| --- | --- |
| `journal.jsonl` | 原始观察与所有事件修订，持续追加 |
| `persistence-status.json` | 独立写盘诊断：接收／落盘序号、队列峰值、批次数、首次错误 |
| `run-status.json` | 运行阶段、结束原因、结束序号、是否排空及 `capture_complete` |
| `view-export-status.json` | 分层文件生成成功或失败，以及具体错误 |
| `views/latest.json` | 最近一次成功生成的快照目录、入口文件和来源截点 |
| `views/snapshot-…/README.md` | 可直接打开的人物阅读入口 |
| `views/snapshot-…/manifest.json` | 来源哈希、序号、字节截点、采集状态与所有输出文件哈希 |
| `views/snapshot-…/events.jsonl` | 此截点全会话的完整最新事件，不应用 FIFO 淘汰 |
| `views/snapshot-…/sim-<ID>/organized.jsonl` | 人物组织单元及未归入单元的 standalone 来源 |
| `views/snapshot-…/sim-<ID>/recap.md`、`recap.json` | 同一份短版回顾的可读形式与结构化形式 |
| `views/snapshot-…/sim-<ID>/details.bundle.json` | 组织详情、名称依据、阅读去向和引用回查 |
| `views/snapshot-…/quality.json` | 全局记录数量、来源和每位人物的对账摘要（0.10.2 起） |
| `views/snapshot-…/sim-<ID>/quality.md`、`quality.json` | 各层计数、每个源事件的去向、问题原因和重要事件保留检查（0.10.2 起） |

先查看 `view-export-status.json`，成功时按 `views/latest.json` 的 index 打开 README。latest 只在整份快照写完后更新；新一轮生成失败时仍保留上一次成功结果，不能仅凭旧 latest 文件推断本次生成成功。保留最近两份成功快照，原始 journal 不受清理影响。

默认人物范围是本次运行中观察过的当前家庭成员；全局 events 文件仍包含全场已记录事件。没有被记录的家庭成员在 manifest 中标为 `entity_not_recorded`。角色关联不等于参与或知情。

0.10.2 新增的对账文件参与同一次原子发布和文件哈希校验；旧版快照不会自动改写。待核查动作按原因分组阅读，原始单条引用保持独立。规则与同源版本比较命令见[事件整理对账](event-quality.md)。

## 何时生成

正常退出、读档、回主菜单或 `co.restart` 结束旧 session 时自动生成。普通旅行保持同一个 session，不提前把旅行边界标为整局结束。大日志的最终生成可能让退出过程多等待数秒；构建有时间和内存上限，超过限制会报告失败。

游戏进行中也可以在控制台输入：

```text
co.export_views
co.status
```

第一条只启动后台任务，第二条的 `view_exports` 查看进度。开发驱动也支持 `export_views` 请求。进行中生成的是固定截点片段，README／阅读版明确标注尚未完整结束；后续采集不改变已有快照。后台仅处理普通数据，公开的游戏内查询接口仍见[分层 API](event-views.md)。

默认 `export_views_on_stop=true`。可在现有 config.json 中设为 false 关闭结束时的自动生成，手动命令仍可调用。导出沿用 `event_view_source_mb`（128 MiB）、`event_view_memory_mb`（512 MiB 估算）和 `event_view_build_seconds`（120 秒）；单份输出另限 256 MiB。超限明确失败，不截短后伪装成完整结果。内存是估算预算，不是 RSS 硬隔离。

## 怎样判断这一局是否写完整

同时核对：

1. journal 中存在最后的 `session_end`。
2. run-status 的 `phase=closed`、`capture_complete=true`，且无记录器或清理错误。
3. `accepted_sequence=durable_sequence=session_end_sequence`，pending_bytes 为 0，durable_byte_offset 等于日志文件大小。
4. 最终分层 manifest 的来源序号／哈希与已关闭的日志一致。

`capture_complete` 只说明这次记录器正常持续工作并完成关闭，不代表游戏所有机制都接入了采集。未开始、未见结束、未知名称、待核查事件仍按原语义保留。

在源码目录可以执行严格结束验收：

```powershell
python -B -X utf8 scripts/validate_run.py "游戏用户目录/ContextOverlay/runs/SESSION" --require-closed
```

返回分别列出 `integrity_passed`（已保存前缀是否可读）和 `capture`（整局结束证据）。严格模式下缺少已验证的完整结束就返回失败。旧日志如果没有新 run-status，会说明缺少最终状态，不冒充完整验收。

## 积压和失败时的行为

写入线程将最多 128 条、约 512 KiB 的连续记录合并同步，聚合等待最多 20 毫秒；单条较大记录单独处理。只有整批同步成功，才同时公布 durable_sequence 和 durable_byte_offset。原始记录本身不归并、不删除，序号与顺序保留。

队列数量／内存或运行输出预算触顶时，记录器停止接收游戏记录并保留首次失败；写入线程继续排空已经接收的数据。真实 I/O 失败则停止写入，不确认未同步的批次。错误原因、接收／落盘差额和队列峰值通过独立状态文件及 runtime.log 保存，不依赖已经失败的日志入队路径。若磁盘本身也无法写诊断，运行日志会尝试报告该失败。

暂停采集后不会把后续没有记录的时间写成“没有事件”，也不会自动继续而掩盖中间缺口。请保留整个 session 目录和 runtime.log 以便定位。有限预算不能保证任意负载；是否适用于当前游戏组合需要真实运行验收。
