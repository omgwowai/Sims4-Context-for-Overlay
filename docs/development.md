# 开发与调试

这一页给修改 ContextOverlay 本身的人看，命令在源码仓库根目录运行。试用 ZIP 只带安装器、SDK 和文档；接自己的 Overlay 不需要准备下面这些开发工具，先看[快速接入](quickstart.md)。

## 环境与来源

游戏基线为 `1.126.73.1030`，嵌入式 Python 3.7，字节码魔数 `420d0d0a`。本机游戏在 `D:/Games/The Sims 4`，参考仓库在 `C:/sources/sims4-python`，主要源码为 `ea-source/EA/`；参考提交由 `src/context_overlay/__init__.py` 的 `EA_REFERENCE_COMMIT` 指定。

先核对实际发送点、参数、返回、默认值、加载时机及反编译来源，必要时对照游戏字节码。Atlas 只用于定位，旧 Experience 只用于必要的实现经验；自研 MOD 示例不能当作游戏内置接口。是否可用最终由目标游戏版本中的行为验证。

## 检查与构建

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

JSONL 通过共用离线读取层扫描一次并计算输入哈希，按日志顺序保留每个事件最终修订，包含曾被内存 FIFO 淘汰的事件；重复写入的序号跳过，断号或中断的尾行报错，诊断 observation 不转成生活事件。旧版仅有 hash、未记录 localization 证据的名称请使用 Git 中对应版本的工具处理。输入约定为当前版本生成的一次运行的已完成日志或稳定副本，不额外检测混合会话、恶意修订或读取期间的文件变化。当前快照只有输入 Context 自身提供时才展示，不能从日志恢复完整历史时刻状态。

完整 JSON 必须显式选择 `--format json`，保留全部事件和原始事实，不受 Markdown 内部层筛选影响；输入文件不会被覆盖。旧的两个位置参数命令现在默认 Markdown，JSON 输出路径须同时加 `--format json`。

可显式应用 v2 目录重新解析文本：

```powershell
python -B -X utf8 scripts/translate.py "输入.json" "报告.md" --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json --catalog .local/resource-semantics/resource_catalog.json
```

未提供资源参数时使用日志内已有证据。提供目录时不会改原始事实；Markdown 直接渲染筛选后的内容，目标、事件和关联效果使用同一份重解释结果，区分静态参考、实测文本及缺失状态。只有完整 JSON 才组装 `semantic_view` 并计算规范化资源摘要。丢失的历史动态参数不靠当前游戏对象补全。

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

## 按需分发

普通玩家可用 `co.api_test` 和 `co.api_verify` 手动验收读写，见[操作步骤](install.md#overlay-接口手动验收)。开发驱动增加限定入口 `api_info/api_context/api_history/api_append/api_changes/api_page/api_close`，对应参数放入请求的 `params` 对象；仍须开启 development_driver 并在加载地块后使用，不提供任意函数调用。

只有明确需要分发时运行：

```powershell
python -B scripts/package.py windows
python -B scripts/package.py sdk
```

Windows 包包含脚本、manifest、无需 Python 的安装器、SDK、读写示例和文档；SDK 包包含客户端源码、示例及相同文档，不含游戏脚本。解压后先看根目录 README。源码开发命令仍需在仓库里运行；包内不带本机日志、存档或资源提取缓存。SDK 中没有游戏资源字典；Windows 脚本包含构建时已选的游戏文本。

日常验证不生成 ZIP，也不恢复旧中间产物。历史日志、审计结果和分发包按需保留，代码与当前文档进入 Git。
