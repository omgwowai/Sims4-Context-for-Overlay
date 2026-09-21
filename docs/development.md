# 开发与调试

这一页给修改 ContextOverlay 本身的人看，命令在源码仓库根目录运行。试用 ZIP 只带安装器、SDK 和文档；接自己的 Overlay 不需要准备下面这些开发工具，先看[快速接入](quickstart.md)。

## 环境与来源

事件分层的共享核心位于 `src/context_overlay/experience/`，`scripts/experience_*.py` 和 `scripts/filter_events.py` 保留离线命令入口。规则 JSON 通过包资源读取；构建和安装 manifest 同时校验 Python 源码与规则资源。新 API 使用 `event_views.py` 的后台任务和 `view_source.py` 的固定日志前缀，契约与预算见[分层查询文档](event-views.md)。

开发驱动开启时，可用 `api_view/api_view_status/api_view_page/api_view_explain/api_view_close` 转发对应公共方法，参数放在请求的 `params`。响应的 `execution_ms` 只测该次游戏线程 API 调用。`frame_probe seconds=20` 采样有界的 `Zone.update` 间隔，再用不带 seconds 的 `frame_probe` 读取分位数；它不是渲染 FPS，不能代替完整性能分析，采样到期或退出运行时移除 Hook。

可复制的请求命令、同源四层对账及旅行／负载判据集中在[分层接口验收](event-views-validation.md)，由测试者手动加载游戏后执行。

游戏基线为 `1.126.73.1030`，嵌入式 Python 3.7，字节码魔数 `420d0d0a`。本机游戏在 `D:/Games/The Sims 4`，参考仓库在 `C:/sources/sims4-python`，主要源码为 `ea-source/EA/`；参考提交由 `src/context_overlay/__init__.py` 的 `EA_REFERENCE_COMMIT` 指定。

2026-09-21 确认：当前阶段固定使用上述游戏版本开发与验收，暂不实现运行时游戏版本自动识别。MOD 的资源规则继续使用构建参考版本；实际升级游戏前，需要重新核对资源、分类规则与验收结果。

先核对实际发送点、参数、返回、默认值、加载时机及反编译来源，必要时对照游戏字节码。Atlas 只用于定位，旧 Experience 只用于必要的实现经验；自研 MOD 示例不能当作游戏内置接口。是否可用最终由目标游戏版本中的行为验证。

## 检查与构建

0.10.1 的批量写盘、独立诊断和自动分层文件见[运行输出说明](run-output.md)。关闭一局的严格验收使用 `validate_run.py --require-closed`，不能把前缀完整性通过当作整局采集完整。

普通测试不需要运行游戏：

```powershell
python -B -X utf8 -m unittest discover -s tests -v
```

安装器测试使用临时用户目录和假游戏进程，不操作真实存档。公共数据、API 测试环境与子 Python 命令集中在 `tests/support.py`；子进程同样带 `-B`，不会重新生成字节码缓存。运行时源码需要兼容 Python 3.7；不要用开发机默认的新 Python 编译游戏脚本包。

下面的解释器、游戏安装目录和参考仓库路径是开发机示例，换成自己的路径。在仓库根目录执行：

```powershell
$py = "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe"
& $py -B -X utf8 scripts/build_resource_catalog.py --game "D:/Games/The Sims 4" --reference "C:/sources/sims4-python"
& $py -B -X utf8 scripts/build.py --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1 -NonInteractive
```

资源构建只读游戏及参考文件，按配置优先级和完整 TGI 选择官方简体中文 STBL／combined tuning，保存来源与冲突；不扫描第三方 MOD。输出位于忽略目录 `.local/resource-semantics/`：

| 文件 | 用途 |
| --- | --- |
| `strings_zh.json` | 已选文本字典，供构建与离线解析 |
| `string_sources.json` | 来源、冲突及字典哈希，随脚本包嵌入 |
| `resource_catalog.json` | v2 类型目录，包含名称／描述／tooltip 引用 |
| `manifest.json` | 游戏版本、来源与输出摘要 |
| `string-candidates.json` | 只有 `--audit` 才生成的完整候选文本审计 |

换游戏版本应重新提取资源；构建检查字典与来源元数据、游戏版本和编译魔数。普通构建仅保留文本选择、冲突和来源所需数据；逐条候选详情仅在 `--audit` 下生成。不带 `--audit` 的资源构建成功后，会清理输出目录中旧的候选文本审计文件。

编译使用自动清理的临时目录，只在 `dist/` 留下 `ContextOverlay.ts4script` 与 `build-manifest.json`。manifest 保存所有源文件、包、字典及来源摘要。安装前必须确认当前源码一致，游戏运行时不安装或关闭游戏。

## 离线报告

直接读取已结束运行的 `journal.jsonl`，或完整 Context／历史／附近实体 JSON。默认生成同目录的 Markdown；无需先生成中间 JSON：

```powershell
python -B -X utf8 scripts/translate.py "某次运行/journal.jsonl" "报告.md"
python -B -X utf8 scripts/translate.py "context-请求ID.json"
python -B -X utf8 scripts/translate.py "某次运行/journal.jsonl" "完整报告.md" --include-internal
python -B -X utf8 scripts/translate.py "某次运行/journal.jsonl" "完整数据.json" --format json
```

Markdown 默认展示主层事件与全部 Autonomy 决策；内部非决策事件通过 `--include-internal` 加入。时间、主体、行为及结果逐条显示，Autonomy 候选和评分置于 `<details>`；查看器不支持折叠时仍可读取正文。

JSONL 通过共用离线读取层扫描并计算整个输入文件的哈希，按日志顺序保留每个事件最终修订，包含曾被内存 FIFO 淘汰的事件。自 0.10.3 起，离线工具与 MOD 的固定前缀读取共用逐条校验：序号连续、同一会话、修订从 1 连续增长、记录类型及完整行合法；重复序号仅在 JSON 内容相同时跳过，允许空白和键顺序不同。即使选择 retained，FIFO 淘汰也不会重置来源修订校验。错误返回有效前缀与首个错误行，生成工具拒绝据此覆盖已有结果；诊断 observation 不转成生活事件。

输入约定为已完成日志或稳定副本，recap 生成额外重读哈希检查读取期间的变化。旧版仅有 hash、未记录 localization 证据的名称请使用 Git 中对应版本的工具处理。当前快照只有输入 Context 自身提供时才展示，不能从日志恢复完整历史时刻状态。

完整 JSON 必须显式选择 `--format json`，保留全部事件和原始事实，不受 Markdown 内部层筛选影响；输入文件不会被覆盖。旧的两个位置参数命令现在默认 Markdown，JSON 输出路径须同时加 `--format json`。

可显式应用 v2 目录重新解析文本：

```powershell
python -B -X utf8 scripts/translate.py "输入.json" "报告.md" --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json --catalog .local/resource-semantics/resource_catalog.json
```

未提供资源参数时使用日志内已有证据。提供目录时不会改原始事实；Markdown 直接渲染筛选后的内容，目标、事件和关联效果使用同一份重解释结果，区分静态参考、实测文本及缺失状态。只有完整 JSON 才组装 `semantic_view` 并计算规范化资源摘要。丢失的历史动态参数不靠当前游戏对象补全。

### 试验中的事件筛选

`scripts/filter_events.py` 在最终修订之上再做一层规则筛选，用来研究哪些细节不需要单独出现在人物经历里。它保留离线命令入口，核心已由新分层 API 的 organized/recap 共用；旧游戏历史及 Context 接口不应用这些规则。

输入目前只接受当前版本的一次运行 `journal.jsonl`，使用已结束运行的日志或稳定副本。包含全日志中已被内存淘汰的事件，以及内部层事件；按人物筛选采用实体关联索引，不等于该人物看见了这些事。

```powershell
python -B -X utf8 scripts/filter_events.py "某次运行/journal.jsonl" --entity sim:123
python -B -X utf8 scripts/filter_events.py "某次运行/journal.jsonl" --entity sim:123 --output .local/analysis/filtered.json
```

不传 `--entity` 分析整个会话；不传 `--output` 只打印数量和体积摘要。输出 JSON 的 `history.events` 是保留的原始事件，ID、修订、时间、角色和 payload 不改。`filtering.omitted` 保存每条省略记录的 ID、修订、规则与关联证据；`filtering.folds` 保存重复补值的代表记录、次数和时间范围，不能把这些补值加总成净变化。`source` 提供输入路径与 SHA-256，`policy_version` 标记规则版本。

当前 `diary_detail_v1` 的范围比较窄：

| 规则 | 省略条件 |
| --- | --- |
| 零时长技术步骤 | 已知站姿／社交调整，或未观测到持续执行的通用动画退出；有明确技术来源、没有关联结果 |
| 选择器 | 已知零时长选择器正常结束，能找到同人物、同次地块访问内已开始的续接交互 |
| 电脑／睡眠小动作 | 精确资源 ID 与 tuning 名匹配，正常结束、不超过 15 游戏分钟；决策里的 provider 实例能对应到实际运行的上层活动 |
| 重复小动作决策 | 保留同一上层活动的首条决策样本；其余仅在末级候选完整且全是已知小动作、没有选择重试或提交异常时省略 |
| Buff handles 维护 | 前后状态除 handles 外完全一致，不带其他新增 payload 字段 |
| 午睡期间重复补值 | 已知 Lazy 数值在同一次真实午睡内重复微量补到 100，间隔不超过 2 游戏分钟；每段保留首条相同补值，初始大幅变化另保留 |

资源规则来自游戏 `1.126.73.1030` 的样本，没有覆盖所有游戏行为。15 分钟与 2 分钟是本轮试验的保守阈值，不是游戏契约。只要记录里有其他事件把动作作为直接原因，就保留动作。外部 payload 不解释；未知资源、长时间技术交互、未结束动作、失败尝试与具体社交不因 `internal`、不可见或取消标签被统一丢弃。

筛选输出不是无损备份：详细执行证据和被省略的决策评分需要原日志回查。保留事件可能引用已省略或人物范围外的事件，不能假设输出里的引用全部闭合；省略审计可帮助定位原记录。日志不能证明用户实际看见了哪些事，规则筛选也不能替代经历归并。

### 试验中的离线经历视图

`scripts/experience_view.py` 在完整运行日志上建立活动关联、状态区间和决策补充，再按实体生成派生视图。它复用上面的基础筛选，但会在省略小动作决策前提取上层活动评分。该命令和游戏内分层 API 共用 `src/context_overlay/experience/` 核心，旧游戏历史接口的默认行为保持不变。

```powershell
python -B -X utf8 scripts/experience_view.py "某次运行/journal.jsonl" --entity sim:123 --game-version 1.126.73.1030 --output .local/analysis/experience.json --markdown .local/analysis/experience.md
```

输入必须是完整、稳定的单次运行日志；先读取全场记录，再筛选人物，才能使用不在人物索引里的 Buff 移除或关联原因。省略 `--entity` 可处理全场；省略输出参数只打印指标。输入、JSON、Markdown 必须使用不同路径，解析失败不会覆盖已有输出。

输出包含：

| 字段 | 用途 |
| --- | --- |
| `consumer_packet` | 供下游读取的紧凑事实摘要：活动内嵌结果和决策，状态按主体形成时间线，背景信号分组；时间按分钟展示 |
| `organized` | 详细的活动、结果、区间、决策和关联依据；保留原始时间精度 |
| `details / review / external` | 默认摘要未展开的执行细节、未解释内容、外部事件引用；外部 payload 仍从原日志读取，不按游戏事实解释 |
| `audit.evidence` | 短证据编号到事件 ID、修订、输出单元或省略原因的对应关系，标记从实体索引范围外补入的证据 |
| `audit.links / filter_folds` | 已核对的原生父子／provider 关联及基础筛选的重复组信息 |
| `source / resource_policy / metrics` | 输入路径与哈希、规则来源与版本、各层数量和实际体积 |

活动只沿同人物、同次地块访问的明确引用归并；续接步骤还须属于已知的同一活动类型。制作到用餐的续接保留活动边界；有唯一产物实例证据时另建关联。具体社交、普通闲聊小动作只有记录里的 provider 实例能指向真实聊天容器时才并入，尚不按时间窗口还原整场聚会。`started` 与退出类型、结果分支分别表达，取消不等于没做过，自然结束不保证玩法成功，未开始尝试和未结束睡眠均保留。

Buff 按实际主体、资源和地块访问配对；缺失、重复或不连续边界显式标记。情绪只有同 tick 完整往返、没有关联结果或同刻交互开始时才折叠。区间是已观测边界的组织，不宣称期间采集无缺口。局部数值变化保留观测序列，不合计成全天净变化。活动选择与执行分开；无法追到具体活动的选择仍作为未确认执行的背景。

决策摘要保留每层赢家和最多两个备选、赢家/备选的前三项非零 commodity 贡献；provider 按同一活动保留首末评分样本。原生评分文本最多摘取 1,200 字符，并报告省略与截断；已有结构化贡献时，默认摘要指向详情中的原生文本，缺少结构化贡献时才直接携带文本摘录。它不是人物想法，也不代表首末样本之间评分不变。完整候选、评分字段和被省略记录需回查原日志。

资源角色表 `src/context_overlay/experience/experience_resources.json` 来自游戏 `1.126.73.1030` 的单局四人物研究，按资源类型、ID、tuning 名精确匹配。当前有 447 条映射；本轮新增的 188 条在 `cross_sim_tuning_evidence` 中附有安装资源及展开 XML 哈希。内部频道计数、大学提示标记按已核实的具体资源归入内部用途，不把所有 `FULL_ASPIRATION` 或所有隐藏 trait 一概处理。Buff handles 维护只有前后非空且其余字段完全一致时才省略。

未识别内容保留待核查；不凭 hidden、显示名称或取消标签删除。传入不同 `--game-version` 会停用这批资源规则及基础省略规则；未传版本仍使用试验规则，**不代表自动验证了游戏版本或第三方覆盖**。目前待核查较多的新人物／玩法不能用摘要缺项推断“没有发生”。活动输出带 `action_tuning`，用于区分过于笼统或错误的本地化文本。经精确身份核实的“练习吉他”“研究死亡学”使用明确名称，并在 `observed_action_name` 中保留原记录名称；没有改写游戏原始日志。

体积指标比较相同紧凑 UTF-8 JSON 口径。可选 `--token-encoding o200k_base` 使用开发环境已安装的 `tiktoken` 实测 token；未安装时不估算。该计数不含提示词包装、审计、详情和待核查内容，也不绑定某个下游模型。事件数、活动单元数、摘要分组数不能互相等同。四人物原日志对照与隔离存档的实机观察见[验证摘要](validation.md#离线经历视图试验)。离散画面只能证明观察时的情形，精确起止由日志提供；不能据此宣称整天经历召回率。

## 游戏调试

常用命令见[安装与使用](install.md)。`co.export` 选择字段时用逗号分隔；`co.status` 核对当前 session、队列、记录器错误、窗口、事件源和 Autonomy 状态。`co.restart` 重读配置并开始新运行，旧查询失效。

Overlay 手动自检使用 `co.api_test` → `co.api_verify` → `co.api_inspect`。普通旅行后只执行 `co.api_verify` 与 `co.api_inspect`，核对历史续接；不要先重跑自检覆盖基准。`tests/test_travel_history.py` 使用真实 Runtime／Journal 和 EA 服务替身覆盖旅行、往返、读档隔离、清理失败及查询连续性，实机复测按[验收步骤](install.md#overlay-接口手动验收)。

### 分页历史查询

`co.history` 按最近更新顺序返回；筛选和分页使用：

```text
co.history_query sim active 50 false
co.history_query sim active 50 false ended none none interaction all completed
co.history_next 上次返回的next_cursor
co.history_close 此查询返回的任意cursor
```

完整参数顺序为：

```text
co.history_query [sim/object] [ID/active] [每页条数] [包含内部步骤] [时间字段] [from_ticks/none] [to_ticks/none] [事件类型/all] [变化字段/all] [交互结果/all] [tuning_ID/all] [asc/desc]
```

列表参数以逗号分隔，`all` 表示不筛选，时间边界 `none` 表示不设限。读取完使用 `co.history_close` 释放查询。字段含义、时间范围、冻结分页与错误契约统一见[公共 API 与 SDK](public-api-v2.md)。

## 配置与预算

用户目录 `ContextOverlay/config.json` 是可选 JSON 对象，缺省值来自 `game_runtime.DEFAULTS`：

| 项目 | 默认值 |
| --- | --- |
| `recorder_enabled / collector_enabled / semanticizer_enabled / inspector_enabled / autonomy_enabled` | true |
| `max_entities / max_interactions_per_sim / max_buffs_per_sim` | 4096 / 128 / 256 |
| `history_capacity / history_memory_mb` | 200000 / 1536 MiB |
| `history_query_limit / history_query_max_refs / history_query_memory_mb` | 8 / 100000 / 256 MiB |
| `history_query_ttl_seconds` | 120 秒现实时间，暂停也计时 |
| `writer_capacity / writer_memory_mb` | 2048 / 32 MiB |
| `run_output_mb / disk_reserve_mb` | 每运行 2048 MiB / 预留 1024 MiB |
| `external_rate_per_second / external_burst` | 全部外部生产者共用：每现实秒 20 条 / 突发 40 条 |
| `autonomy_top_n / autonomy_pending_capacity` | 每层 5 项 / 每组 256 条 |
| `autonomy_pending_memory_mb / autonomy_pending_ttl_seconds` | 两组共 8 MiB / 600 秒现实时间 |
| `development_driver` | false |

这些是不同预算，不可把 200000 条视为内存保证。旧 `record_need_changes`、`sample_interval_sim_minutes` 已不支持；需求与关系仅按 Context 请求读取当前值。

## 复测工具

- `scripts/validate_run.py`：日志完整性和实际观察到的事件统计；不等于场景全部验收。
- `scripts/audit_event_hooks.py`：用 Python 3.7 核验事件与 Autonomy 的原生字节码入口；受控行为测试统一由 `unittest` 运行。
- `scripts/audit_localization.py`：使用资源目录对已有文本审计。
- `scripts/benchmark.py`：同一负载下测量 FIFO、身份清理、近期查询与分页耗时。
- `scripts/game_request.py`：向显式开启的开发驱动提交白名单请求，不能执行任意代码。

上述核验、文本审计与压测工具默认只打印 JSON 摘要；显式传入 `--output 路径` 才直接写出完整报告，保留输入与输出路径不同的基本检查。压测中的检查点、逐项核验及文本变化明细保留在完整报告中。

`validate_run.py` 与 `audit_localization.py` 默认使用全日志最终修订，`--event-scope retained` 可改为应用 FIFO 淘汰后的保留集合；报告标记实际口径。文本审计仍会另外检查每份 Context 导出，导出与日志可能包含相同事实。压测沿用 `retained` 集合取样，并不声称完整会话覆盖。

```powershell
python -B -X utf8 scripts/validate_run.py "某次运行"
python -B -X utf8 scripts/validate_run.py "某次运行" --event-scope retained --output tmp/验证.json
python -B -X utf8 scripts/benchmark.py "某次运行/journal.jsonl" --capacity 10000 --extra 1000
```

工具共用 `scripts/offline.py` 的输入读取、资源目录重解释与离线组包，以及 `scripts/tool_support.py` 的报告写入、哈希、进程内存和字节码读取；它们不进入游戏脚本包。游戏内日志写入由 `storage.Journal` 负责，不加载离线读取模块。

### 实机测试环境与请求

游戏退出后，用一个入口准备或恢复环境；默认定位系统“文档”中的游戏用户目录，其他位置用 `-UserData` 指定。测试存档必须显式选择：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/game_test.ps1 prepare -SaveName Slot_00000001.save
python -B -X utf8 scripts/game_request.py status
python -B -X utf8 scripts/game_request.py entities match=阿明
python -B -X utf8 scripts/game_request.py export kind=sim id=active limit=15 history=false
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/game_test.ps1 restore
```

准备时暂存 Mods、存档、选项和原配置，在存档副本上测试并开启开发驱动。加载测试存档后才能发送请求。`game_request.py` 接受操作名与 `key=value`，JSON 值解析为对应类型，其余作为字符串；复杂请求仍可传 JSON 文件。用 `--user-data` 指定非默认目录（默认 `~/Documents/Electronic Arts/The Sims 4`），`--output` 按需保存响应。状态通过 `status` 请求获取，不再每秒生成 `status.json`；保留已消费请求标记，避免重启后重复执行。

恢复按字节还原选项和配置（原本没有配置文件则移除测试配置），核对存档哈希后删除临时测试副本及 `ContextOverlay/test-backup`，不保留逐次状态归档。恢复后默认安装当前包，`-SkipInstall` 可跳过。测试未恢复前不要删除备份目录；游戏日志仍按需保留，普通安装不调用测试环境工具。

## 短版经历回顾与证据回查

`scripts/experience_recap.py` 是离线消费层，输入完整、稳定的单 session 日志，复用经历组织器。默认 JSON 保留已组织活动、非数值结果、关系数值的局部变化次数、感受／情绪区间以及待核查提示；连续数值、评分与背景在详情中。未开始、已执行后取消、未见结束分别表达，不将交互退出写成玩法完成。

```powershell
python -B -X utf8 scripts/experience_recap.py build tmp/journal.jsonl --entity sim:123 --game-version 1.126.73.1030 --output tmp/recap-bundle.json --recap tmp/recap.json --markdown tmp/recap.md
python -B -X utf8 scripts/experience_recap.py query tmp/recap-bundle.json --snapshot SNAPSHOT_ID --ref r1 --facet evidence --limit 20
python -B -X utf8 scripts/experience_recap.py query tmp/recap-bundle.json --snapshot SNAPSHOT_ID --ref r1 --facet raw --journal tmp/journal.jsonl
```

将 `SNAPSHOT_ID` 替换为生成结果的 `snapshot_id`。`recap.json` 是默认输入；bundle 保存来源 manifest、完整组织单元、去向表和审计引用，不应整包作为默认模型输入。可选 `--token-encoding o200k_base` 使用已安装的 tiktoken 实测默认 JSON 与 Markdown，不从字节估算 token。3–8 千 token 是样例试验目标，不会触发截断。

查询支持短引用 `r1`、完整 unit ID、证据短编号 `e123` 和 `@activities/@facts/@states/@decisions/@background/@details/@review/@external` 分组。`@audit --facet evidence` 可分页查询全部证据，包含没有组织单元的筛选记录。facet 为 `units`、`decisions`、`evidence`、`raw` 或 `links`；`evidence/raw/links` 查询活动时包含已关联的结果、状态和决策。`units` 返回该条目的组织单元，其中关联 ID 也可直接查询。

每页最多 100 项，响应包含 `total`、`offset`、`next_offset`。按 `next_offset` 继续，直到为 null；内容不静默截断。原生评分保留在原日志，`decisions` 返回组织器摘取的评分样本，完整内容通过 `raw` 获取。原事件只返回指定日志的最终修订，不冒充中间修订历史。

快照绑定原日志哈希、session、人物、事件观测边界、组织规则、资源映射与实现哈希，以及实际输出内容。别的快照中的同名 `r1/e123` 不可混用；bundle 内容修改或原日志变化会报错。原始证据查询必须提供匹配日志。来源读取额外检查混合 session 与读取期间写入；应先冻结日志副本。快照是内容一致性校验，不是数字签名或来源认证。

时间为游戏周／日的分钟展示，精确 ticks 在详情中；观察到的最早／最晚事件不代表完整日历日或连续采集。默认活动角色和自然退出、状态已配对边界的省略语义在 `scope` 中说明。仅为依赖关联引入的其他人物活动会标明上下文，不能据此推断主角参与或知情。未知行为仍显示待核查，未知数值和背景可从对应分组展开。

对应测试为 `tests/test_experience_recap.py`。CLI 包装保留在 scripts，共享核心及规则资源进入游戏脚本包，供新分层 API 使用；实施依据见[计划](experience-recap-plan.md)，当前接口验收按[操作步骤](event-views-validation.md)执行。

0.10.2 增加 `build --quality/--quality-markdown`、`quality` 和同源 `compare` 命令，覆盖重要事件保留、活动阶段归并和问题原因对账。运行时自动导出也生成相同核心的质量报告；完整命令及计数口径见[事件整理对账](event-quality.md)，回归测试为 `tests/test_experience_quality.py`。

`experience_recap_v1_1` 将执行 `time` 与 `queued_at`／`observed_at` 分开：未见开始时 `time[0]` 为 null，不再拿首次观测代替开始。入队使用原交互的 queued observation，无此证据时仅说明首次观测。阅读表将行动者独立显示，同名同时间的不同实例继续保留。

名称经过 `scripts/experience_labels.py` 统一处理，压缩保留 `tuning_name`、`name_status` 和参数缺口；精确释义在 `src/context_overlay/experience/experience_labels.json`，原名、状态和来源在 `audit.labels`。使用 `--facet labels` 查询某条目的名称依据，或 `--ref @labels --facet labels` 分页读取名称质量记录。未解析／部分解析在 `recap.name_quality` 公开，名称规则文件哈希纳入 snapshot。原始结果与退出原因仍可用 `raw` 或 `units` 查询。案例、边界与对照见[debug 记录](experience-recap-debug.md)。

## 按需分发

普通玩家可用 `co.api_test` 和 `co.api_verify` 手动验收读写，见[操作步骤](install.md#overlay-接口手动验收)。开发驱动增加限定入口 `api_info/api_context/api_history/api_append/api_changes/api_page/api_close`，对应参数放入请求的 `params` 对象；仍须开启 development_driver 并在加载地块后使用，不提供任意函数调用。

只有明确需要分发时运行：

```powershell
python -B scripts/package.py windows
python -B scripts/package.py sdk
```

Windows 包包含脚本、manifest、无需 Python 的安装器、SDK、读写示例和文档；SDK 包包含客户端源码、示例及相同文档，不含游戏脚本。解压后先看根目录 README。源码开发命令仍需在仓库里运行；包内不带本机日志、存档或资源提取缓存。SDK 中没有游戏资源字典；Windows 脚本包含构建时已选的游戏文本。

日常验证不生成 ZIP，也不恢复旧中间产物。历史日志、审计结果和分发包按需保留，代码与当前文档进入 Git。
