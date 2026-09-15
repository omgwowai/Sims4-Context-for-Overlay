# 运行与调试（0.5.0）

日期：2026-09-15。0.5.0 增加[公共 API v1 与 SDK](public-api-v1.md)，完成离线验证，尚未安装或实机验收，见[接口验证记录](validation/2026-09-15-public-api.md)。本版包含 0.4.0 名称与动态参数改进，实机测试仍待进行，见[语义解析验证](validation/2026-09-15-semantic-resolution.md)。已安装／内部试用版仍为 0.3.2；窗口操作见[手动试验说明](inspector-manual-test.md)，实机范围见[布局验证记录](validation/2026-09-14-inspector-layout.md)。

## 1. 构建

使用 CPython 3.7。开发机的独立解释器位于 `%LOCALAPPDATA%/Sims4ContextDev/python37/python.exe`，来源为 Python 官方 3.7.9 Windows embedded distribution；不替换系统默认 Python。

在仓库根目录运行：

```powershell
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" scripts/build.py --strings C:/sources/sims4-python/data/strings/CHS_CN.json
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -m unittest discover -s tests -p 'test_*.py' -v
```

构建脚本比较本地 `simulation.zip` 的字节码魔数，输出 `dist/ContextOverlay.ts4script` 和 `dist/build-manifest.json`。manifest 记录源文件与本地中文词表的 SHA-256，并将游戏构建版本、源码参考提交和内容摘要写入包内 `build_info.json`，随查询结果导出。词表是本地构建输入，不复制进 Git。字节码匹配是静态构建检查，不能替代游戏加载验证。

## 2. 安装与输出

将脚本包放在实际用户数据目录的 `Mods/ContextOverlay/ContextOverlay.ts4script`。启动游戏并进入地块后，加载层建立独立运行 ID、注册事件并启动采样。

内部试用包完整解压后双击根目录 `Install.cmd`，无需 Python；支持自动发现实际文档目录、手动指定 `-Profile` 和 `-WhatIf` 预览。详见[安装说明](install.md)。开发机也保留 `scripts/install.py --profile "C:/Users/ZixuanMin/Documents/Electronic Arts/The Sims 4"`：该旧入口依赖 Python，回执保存在仓库 `.validation/`。普通安装不使用隔离实机验收的 prepare/restore 脚本。

普通点击实体的菜单中选择“查看状态与历史”；查看时直接调用运行时接口，无须先导出文件。首页采用原生文字对话框，近期事件直接逐行显示，按钮依次为“当前状态 → 历史事件 → 刷新 → 关闭”。状态、字段、历史和筛选为横向文字列表，点击一行直接进入，不再显示空图标列；快照时间／筛选／页数位于表头，完整说明在表头悬停提示中。长详情采用正文与返回／分段按钮。默认历史近 24 游戏小时、每页 15 条，详见手动试验说明。

输出位于实际用户数据目录的 `ContextOverlay/`：

| 路径 | 内容 |
| --- | --- |
| `runtime.log` | 包加载、运行启停与采集错误 |
| `config.json` | 可选启动配置；省略字段使用默认值 |
| `runs/<运行 ID>/journal.jsonl` | 追加观测和事件修订，带序号与运行身份 |
| `runs/<运行 ID>/context-<请求 ID>.json` | 完整写入后才出现的查询结果 |

运行 ID 在重启、读档、重新进入地块或明确重启采集时变化。历史保留在各运行目录中，不自动合并。当前查询使用本次运行的有界缓存；离线审计可以读取完整日志。

## 3. 游戏命令

在游戏控制台调用：

```text
co.status
co.inspect
co.export
co.export object 物件实例ID 50 false both true identity,location,object_states
co.export sim active 50 false raw false identity,needs,buffs,relationships
co.history sim active 50 true
co.restart
```

- `co.status`：当前运行、模块配置、写入进度、订阅数量和错误。
- `co.inspect [sim/object] [ID/active]`：游戏内窗口的备用入口，默认查看当前操控 Sim；不写 Context 导出文件。`co.status` 的 `inspector` 字段报告菜单与窗口状态。
- `co.export [类型] [ID/active] [历史条数] [包含内部步骤] [raw/text/both] [包含历史] [字段列表/all]`：查询当前数据并异步导出。
- `co.history [类型] [ID/active] [条数] [包含内部步骤]`：直接查询记录模块，Context 采集器关闭时仍可使用。
- `co.restart`：重新读取配置并开启独立运行；已有运行日志保留。

控制台返回 `queued` 仅表示导出已入队。通过 `co.status` 中对应请求的 `written` 状态和完整 JSON 文件确认完成。首次启动的实体登记也进入同一写入队列，实测导出曾超过 5 秒；消费者应等待写入确认并设置合理超时。`text` 表示提供文本表示，仍保留文本所引用的原始证据。

可选字段为 `identity`、`location`、`time`、`interactions`、`needs`、`buffs`、`relationships`、`object_states`。不适用、范围外、读取错误和未支持状态会分别返回。

### 分页历史查询

旧 `co.history` 保持按最近更新顺序返回的行为，但已改为查询实体关联索引。新命令按指定时间排序，冻结符合条件的事件版本后分页：

```text
co.history_query sim active 50 false
co.history_query sim active 50 false ended none none interaction all completed
co.history_query sim active 50 false first_observed 20181020 20215250 state_change needs.hunger
co.history_next 上次返回的next_cursor
co.history_close 此查询返回的任意cursor
```

完整参数顺序为：

```text
co.history_query [sim/object] [ID/active] [每页条数] [包含内部步骤] [时间字段] [from_ticks/none] [to_ticks/none] [事件类型/all] [变化字段/all] [交互结果/all] [tuning_ID/all] [asc/desc]
```

- 每页 1–500 条；时间范围是游戏 ticks 的 `[from, to)`，`none` 表示该端不设限。示例 ticks 来自验收样本，使用时应取目标运行的实际时间。
- 时间字段为 `first_observed`、`started`、`ended`，默认首次观测时间倒序；时间相同按事件首次接收顺序确定顺序。缺失开始/结束时间的事件不匹配相应查询。
- 事件类型为 `interaction`、`state_change`；变化字段可用 `buffs`、`relationship.bits`、`needs.hunger`、`relationships.friendship`、`object_states.15188` 等精确字段名。交互结果为 `completed`、`cancelled`、`failed`、`unknown`。列表参数以逗号分隔，多项条件之间取交集。
- 采样变化按发现差异的时刻筛选，仍保留原采样区间。此次未实现区间重叠查询或跨运行查询。
- 返回 `cursor`、`next_cursor`、`has_more`、`total_matches`；导出的 `history` 还包含筛选条件、`as_of_sequence`、观测范围和查询创建时的持久化状态。
- 所有页面固定为首次查询时的成员及修订版本；后续新事件或完成通知不会改变已有查询。需要最新内容时重新发起查询。
- 游标默认 120 秒现实时间后过期，暂停游戏也计时。最多同时保留 8 个查询，总引用数最多 100,000，并受 256 MiB 的保守版本保留预算限制。过宽查询明确返回 `query_budget`；可缩小时间范围、类型或关闭旧查询。
- 同一游标可以重试，最后一页不会立即释放快照；读取完可调用 `co.history_close`。换地块/重启释放所有查询，旧游标返回 `session_changed`；过期/已关闭返回 `cursor_expired`。

内部 `Collector.collect(..., history_query={...})` 可以把筛选后的第一页组合到 Context 中。下游 MOD 应使用 0.5.0 新增的 `context_overlay.api.get_context/query_history/get_history_page/close_history` 或轻量 SDK，不引用 Collector／HistoryIndex 的内部对象。后续历史页面保留第一次查询的事件版本，不代表重新读取当前快照。完整契约与游戏线程要求见[公共 API v1](public-api-v1.md)和[开发接入说明](mod-integration.md)。

## 4. 配置与限额

```json
{
  "recorder_enabled": true,
  "collector_enabled": true,
  "semanticizer_enabled": true,
  "sample_interval_sim_minutes": 5,
  "record_need_changes": false,
  "max_entities": 4096,
  "max_interactions_per_sim": 128,
  "max_buffs_per_sim": 256,
  "history_capacity": 200000,
  "history_memory_mb": 1536,
  "history_query_limit": 8,
  "history_query_max_refs": 100000,
  "history_query_memory_mb": 256,
  "history_query_ttl_seconds": 120,
  "writer_capacity": 2048,
  "writer_memory_mb": 32,
  "run_output_mb": 2048,
  "disk_reserve_mb": 1024,
  "development_driver": false,
  "inspector_enabled": true
}
```

配置中的 `*_mb` 按 MiB（1,048,576 字节）解释。数量上限与字节预算分别生效：200,000 是最大事件数，不保证所有事件组成都能达到该数量。记录内存预算估算事件正文和索引；快照预算保守计算其引用的完整事件版本，即使该版本同时仍在当前事件表中。它们不是对整个游戏进程 RSS 的硬上限，游戏自身、其他 MOD、临时分配和其他运行状态还会使用内存。

单次运行的日志与导出共用 2 GiB 输出预算，写入前检查磁盘仍能保留 1 GiB 空间；写入队列同时受 2,048 个任务和 32 MiB 预算约束。已有日志不自动删除。磁盘不足或预算耗尽会显式失败，未写完的记录不会标为持久化成功。

`record_need_changes` 默认 false，关闭连续需求的后台历史采样；设为 true 并重启可恢复。Context 仍可按请求读取当前需求。关系数值采样及 Buff／关系标记／物件状态等离散记录不受此开关影响，旧运行日志也不修改。

采样使用游戏时间，查询与验证请求由游戏线程上的每秒现实时间回调协调。当前预算通过离线容量测量，尚未做新版本的游戏负载测量。记录容量、记录内存、写盘或采集预算耗尽时明确报错并暂停受影响采集；仅查询快照超预算时拒绝该查询，采集继续。写入确认在后台文件刷新和 `fsync` 成功后推进。

目前使用对象管理器的可见对象集合，并通过 `is_on_active_lot()` 筛选；隐藏实例和场外实体明确排除，不能据此推断它们没有活动。关系仅查询范围内已存在的关系；友谊和浪漫主轨道按一对参与者采样一次。

实体进出范围由每秒回调观察，时间表示发现边界的时刻，不宣称精确到跨边界的那一帧。历史结果显示该实体最近的进出范围时间；任一关系参与者离场会清除关系采样基线。离场后再次出现时从新基线开始，不生成跨越观测空档的关系差异。

## 5. 离线语义化与日志审计

```powershell
python scripts/translate.py 输入数据包.json 新的中文数据包.json
python scripts/validate_run.py 运行目录 --output 审计结果.json
```

两项操作不导入游戏服务。不带资源参数的 `translate.py` 仅重排现有名称与事件措辞。需要从游戏资源补充名称时，先生成本地索引，再显式传入索引和词表：

```powershell
python scripts/build_name_catalog.py C:/sources/sims4-python/data/tuning .local/name-catalog.json
python scripts/translate.py 输入数据包.json .local/重新解释.json --strings C:/sources/sims4-python/data/strings/CHS_CN.json --catalog .local/name-catalog.json
python scripts/audit_localization.py "游戏用户目录/ContextOverlay/runs/运行ID" --strings C:/sources/sims4-python/data/strings/CHS_CN.json --catalog .local/name-catalog.json --output .local/名称审计.json
```

审计可传入多个运行目录，统计各日志最新事件版本及 Context 导出中的名称变化；导出可能重复日志中的事实，不是事件发生次数。原记录保持不变，新解释位于 `semantic_view` 和 `rendered`。旧日志缺少的动态参数明确留空；没有显示名称、缺少词表 key、尚未支持的 token 分别说明，见[语义化模块](semanticizer.md)。不要将本地 EA 词表或全量索引提交 Git。

日志审计检查序列、修订、结束证据与中文引用，并统计实际观测范围；统计不等同于全部场景验收通过。

## 6. 隔离的游戏验收

`scripts/prepare-game-test.ps1` 在游戏关闭时暂存原 Mods、存档与选项，用一份存档副本和本项目 MOD 建立测试环境，并把恢复信息写入 `.validation/environment.json`。`scripts/restore-game-test.ps1` 在游戏关闭后归档测试目录、恢复原文件并核对原存档哈希；默认保留本项目脚本包安装，`-SkipInstall` 可省略该安装。

仅测试配置启用 `development_driver`，通过本地文件交换状态、查询和白名单游戏操作，所有改变游戏状态的测试操作写入观测日志。它不执行任意 Python 或系统命令。恢复正常环境时关闭此选项。文件请求接收与执行结果分开记录，运行重启不重复执行旧请求。
