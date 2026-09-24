# API 参考：读状态、查历史、写事件

第一次接入先看[快速接入](quickstart.md)；不确定应该读取当前 Context、历史 Events 还是四层事件视图时，先看[读取指南与能力矩阵](reading-guide.md)。需要查准确参数时再回到这页。当前 API / SDK 是 **2.3.0**，schema 是 **2**；当前源码对应 **ContextOverlay 0.11.0**。

新增的 records/events/organized/recap 查询使用后台构建和同源分页，见[游戏内分层事件查询](event-views.md)。以下现有历史接口仍保持原语义。

直接调用用 `context_overlay.api`；希望统一处理“没安装、版本不匹配、分页关闭”等情况，可以用 `sdk/context_overlay_client.py`。两者提供同一套读写能力。

Context、历史、增量、附近实体与视锥查询同步返回普通 JSON 数据；游戏对象读取在模拟线程进行。SDK 不调用模型、不创建窗口、不发网络请求，也不自动跨线程调度或重试。默认历史同时包含游戏事件和外部事件。


## 版本与迁移边界

当前 SDK 为 2.3.0，适配 API 2.3.0 / schema 2。API 2 默认混合历史，新增 `external_event` 类型和 `origin/producer` 字段；下游 MOD 应使用当前 SDK 和能力声明，不依赖已删除的 v1 契约。离线工具仍可按输入日志自身的 schema 处理旧数据，但旧日志不代表当前运行时兼容性。

`get_context`、`query_history` 的 `origins=None` 表示全部来源，`["game"]`／`["external"]` 表示只查一类；`producers=["example.overlay"]` 仅匹配对应外部生产者，与其余条件取交集。数组不能为空，最多 64 项且不重复。类型／结果等游戏专用筛选自然排除不具备对应字段的外部记录。

`query_history()` 仍默认查询当前 Sim；显式 `query_history(None, None, ...)` 查询本次运行的全部实体及无实体记录，可组合来源／生产者筛选。`get_context` 继续要求实体。新写入不会出现在先前冻结的历史页里，请新建查询或读取增量。

## 安装和最小接入

先按[安装与使用](install.md)安装与当前源码匹配的 ContextOverlay，再把 `sdk/context_overlay_client.py` 复制进自己的包，例如 `my_overlay_mod/vendor/`，并按 Python 3.7 打包；各级目录需要自己的 `__init__.py`。SDK 与提供方应通过 `get_api_info()` 的版本和能力字段确认兼容，不要依赖旧版分发包名称。

```python
from my_overlay_mod.vendor.context_overlay_client import Client, ContextOverlayError

client = Client()  # 安全：此时不导入提供方或游戏服务。

def on_player_requests_context(sim_id):
    # 由下游 MOD 的游戏线程交互／alarm 回调执行。
    try:
        packet = client.get_context(
            "sim", str(sim_id), fields=["identity", "needs", "buffs", "interactions"],
            include_history=True, history_limit=15, representation="both")
    except ContextOverlayError as exc:
        return {"available": False, "error": exc.to_dict()}
    return {"available": True, "partial": packet["status"] == "partial", "packet": packet}
```

已有强制依赖加载机制的下游也可直接使用：

```python
from context_overlay import api

packet = api.get_context("sim", "active", fields=["identity", "needs"], history_limit=5)
```

直接入口抛出 `api.APIError`；SDK 转成 `ContextOverlayError`。接入时使用这两个公开入口，内部 `_runtime` 等对象会随实现调整。读写示例见 [quickstart.py](../sdk/examples/quickstart.py)，窗口回调示例见 [consumer.py](../sdk/examples/consumer.py)。

## 版本与能力发现

```python
info = client.get_api_info()
status = client.get_status()  # 有活动运行时须在游戏线程。
```

`get_api_info()` 是纯元数据查询，可在加载存档前或后台线程调用：

| 字段 | 含义 |
| --- | --- |
| `api_version` | 当前公共契约版本 `2.3.0` |
| `module_version` | 提供方 MOD 版本，以本次返回值为准 |
| `schema_version` | 数据协议版本 `2` |
| `capabilities` | 能力列表，例如 `context.read`、`history.query`、`events.append`、`history.changes`、`event_views.query`；按所用接口检查相应能力 |
| `context_fields`、`default_fields` | 支持的字段与 Sim／Object 的默认选择 |
| `nearby` | 附近查询类型、指标、单位、返回数、扫描预算和半径限制；能力为 `context.nearby_entities` |
| `resource_text` | 可选 name／description／tooltip 文本证据，能力为 `text.resource_details`；官方中文词表与 MOD 覆盖边界见[资源语义目录](architecture.md) |
| `max_history_page_size`、`max_context_history_limit` | 请求单页／近期条数上限，各为 500 |
| `thread_policy`、`transport` | `simulation_thread`、`in_process_python` |
| `scope`、`history_scope` | 当前地块已实例化实体、本次运行历史 |

能力存在不表示配置已启用。`get_status()` 返回 `ready`、`state`、`session_id`；状态可为 `waiting_for_zone`、`starting`、`startup_failed`、`closed`、`ready`。只有 ready 时附带 `modules`、`recorder` 和 `query_limits`。

`ready=true` 表示运行初始化完成，不意味着记录器一定健康。`modules` 包含 `collector_enabled`、`recorder_enabled`、`semanticizer_enabled`；记录器的 `state/error/persistence` 单独报告。字段、资源名称和单条事件也有各自可用性，不应压成一个全局成功布尔值。

SDK 检查 API 主版本为 2、schema 为 2，接受兼容的 2.x 小版本；不固定准确 MOD 版本。API 2.x 保持已公开的方法和现有字段含义，允许增加可选参数、字段和能力；消费者忽略未知附加字段，对状态／枚举保留未知分支。删除接口或改变既有语义需升级 API 主版本，数据不兼容变化需升级 schema。内部 Python 模块不属于这个承诺。

## 旅行与会话范围（API 2.1）

0.9.0 提供方新增 `history.travel` 能力和 `session_lifecycle` 元数据。正常旅行到其他地块或返回原地块保留同一个 `session_id`；游戏和外部事件、无实体记录、生产者筛选、去重键、FIFO 与增量 checkpoint 持续有效。读档、回主菜单、重启游戏或 `co.restart` 开启新会话，不自动读取磁盘中的旧会话。API 2.0 客户端可继续调用；需要跨地块保证时先检查 `history.travel`。

卸载到加载完成期间 `ready=false`，读写调用可能得到 `session_closed/not_ready`。等待 ready，再比较 session：相同则续读，不同则丢弃旧会话引用。`close_history` 仍可在模拟线程释放旅行中保留的查询。冻结查询仍受 120 秒现实时间 TTL 限制，过期后释放旧 batch，从最后已提交的 checkpoint 重读；已经处理的事件可能重复，消费者须按 `(event_id, revision)` 去重。FIFO 缺口仍返回 `history_gap`，不会因旅行跳过检查。

`get_status().recorder` 增加 `zone_visit`（本会话第几次地块加载）和 `observation_scope`（当前采集范围）。新事件附带同名字段，保留它在被记录时的 zone/lot；外部事件表示**接收位置**，不代表 payload 内容实际发生在此处。交互 ID 在不同 visit 之间独立，消费者须将 `event_id` 当作不透明标识。旅行前没有观测到结束的交互不补写推测结果。

当前 Context 和附近查询仍只读取当前地块实例；历史可按已知实体 ID 查询此前地块的保留事件。所有记录、查询、去重和输出容量贯穿整个会话，不因旅行重置。配置更新用 `co.restart` 生效。

`expected_session_id` 防止写入错误存档进度；旅行保持该值，不能代替模型结果的地点／时效检查。若结果只适用于请求时的地点，下游提交前应自行核对原 Context 的 `scope.zone_id` 与时间，并决定是否仍需写入。

## 当前 Context

公共函数和 SDK 的参数含义一致：

```python
get_context(kind="sim", identifier="active", *, fields=None,
            include_history=True, history_limit=15, include_internal=False,
            representation="both", expected_session_id=None, origins=None, producers=None)
```

| 参数 | 约定 |
| --- | --- |
| `kind` | `sim` 或 `object` |
| `identifier` | 正的 64 位实例 ID（十进制字符串或 Python int）；仅 Sim 支持 `active`。不接受游戏对象、对象定义 ID、float、0、bool |
| `fields` | 非空且不重复的字段 list／tuple。None 使用下表默认值；字符串 `"needs"` 不能代替 `["needs"]` |
| `include_history` | 是否附带近期历史，必须为 bool |
| `history_limit` | 1–500 的整数，默认 15；即便不请求历史也须合法 |
| `include_internal` | 是否包含内部步骤，默认 false |
| `representation` | `raw`、`text`、`both`；text/both 均附带原始证据及 rendered，并非返回单个字符串 |
| `expected_session_id` | 可选运行约束；与当前运行不同则拒绝，不自动改用新存档的数据 |

全部字段为 `identity`、`location`、`time`、`interactions`、`needs`、`buffs`、`relationships`、`object_states`。默认 Sim 请求前七项，Object 请求 `identity/time/location/object_states`。显式请求不适用的字段会得到 `not_applicable`，不会替换成 0。

`active` 在请求开始时解析为固定 Sim ID。请求随后读取该实体，返回 `read_started/read_finished`；这是同步读取区间，不承诺游戏世界的事务快照。历史按最近更新顺序取最多 N 条，不创建游标；更复杂的时间／类型查询使用下一节接口。

返回 ContextPacket 的关键结构：

```text
api_version / module_version / schema_version
session_id / request_id / recorded_at / kind="context"
target / scope / requested_fields / read_started / read_finished
status="complete"|"partial" / representation / provenance
snapshot.<field> = {status, value, source, reason?}
history = {status, events, coverage?, target_observation?, limit?, truncated?, ...}
rendered? = {language, rules_version, current, history} 或 {status:"disabled", reason}
```

`complete` 仅表示本次请求没有被模块／字段不可用阻断，不保证名称全部翻译成功、历史完整或全部事件已写盘。字段状态包括 `available`、`not_present`、`not_applicable`、`unsupported`、`out_of_scope`、`disabled`、`error`；名称的 `unresolved_tokens/no_display_name` 等是另一层状态。

关闭记录器或记录失败时，Context 仍可读取当前状态，所附历史明确标记 `disabled/failed`，包为 partial。关闭语义化时原始数据仍返回，rendered 标记 disabled。关闭 Collector 时 `get_context` 抛 `collector_disabled`，历史接口仍可调用。范围外实体的当前字段标为 out_of_scope，历史是否曾观测由 `target_observation` 说明。

返回值已复制为 JSON 数据，下游修改字典不会更改记录器。所有 ID 和 ticks 输出使用字符串以保留精度；数值需求是游戏内部单位，不是百分比。

## 历史查询、翻页和关闭

```python
query_history(kind="sim", identifier="active", *, page_size=15,
              include_internal=False, time_field="first_observed",
              from_ticks=None, to_ticks=None, event_types=None, fields=None,
              outcomes=None, tuning_ids=None, order="desc", group_effects=False,
              representation="both", expected_session_id=None, origins=None, producers=None)

get_history_page(cursor, *, expected_session_id, representation="both")
close_history(cursor, *, expected_session_id)
```

首个调用返回完整 HistoryPacket：外层含版本、request_id、session_id、target、provenance、status、representation；内层 `history` 含事件、游标与覆盖，`rendered.history` 为逐条中文。这个形状与 ContextPacket 中的历史区域一致。

| 参数 | 含义 |
| --- | --- |
| `page_size` | 每页 1–500 条；默认 15 |
| `time_field` | `first_observed`、`started` 或 `ended`，默认首次观测 |
| `from_ticks/to_ticks` | 游戏整数 ticks 或整数字符串，范围 `[from, to)`，None 不设边界。不是现实秒／Unix 时间 |
| `event_types` | 非空 list／tuple，可含 `interaction`、`state_change`、`game_event`、`external_event` |
| `fields` | 变化字段或新增 category，例如 `["buffs", "relationship.bits"]`、`["skill.level", "statistic.direct"]`；不是 Context 字段选择 |
| `outcomes` | 交互结果：`completed/cancelled/failed/unknown` |
| `tuning_ids` | 交互定义 ID 的字符串列表，不是交互实例 ID |
| `order` | `asc` 或 `desc`，默认倒序 |
| `group_effects` | 默认 false；true 将与同一结果集内动作明确关联的事实放入其 `effects`，未匹配的效果仍单独显示 |

列表筛选最多 64 个不重复的非空字符串；None 表示不筛选。不同条件取交集。没有开始／结束时间的事件不匹配对应时间筛选，状态变化按通知观测时间筛选。

当前不生成需求／关系定时差值，也不返回采样区间。`state_change` 保留 Buff、关系标记、物件状态前后值；新增 `game_event` 使用 `category/field/payload`。`statistic.direct` 的 `payload.before/after` 只来自明确 Loot 操作内真实通知，`cause` 保留可核验操作／交互依据。当前数值仍使用 `get_context(fields=["needs", "relationships"])`。没有记录不证明数值未变化。

新增能力标识为 `history.effects`、`history.retained_identity`、`history.fifo`、`events.gameplay`；`get_api_info()` 返回 `event_types/event_categories/retention_policy`，`get_status()` 返回具体源的 `event_coverage`。SDK 透传这些筛选参数。同时提供 `events.append`、`history.sources`、`history.global`、`history.changes`。

运行状态提供 `get_status().event_diagnostics`，包含 `callbacks`、`suppressed_statistics`、`suppression_policy` 和 `timing`。它们是适配器回调／计时通知的汇总数，不是事件数量或性能测量；目前 timing 为 not_measured。事件源健康与汇总在会话开始、结束落盘。TimeSince 计时统计不再发布为历史，当前 Context 不受影响。使用角色判断效果归属，不要把 entities 索引列表当作受影响者列表；事件语义见[架构与采集语义](architecture.md)。

按数字 ID 查询历史时先使用本运行保留的实体身份，不要求当前仍有可读取实例；Context 继续独立报告 out_of_scope。分组后 `total_matches` 是显示行数，快照预算仍计入全部效果事实。某动作未进入筛选结果或已被 FIFO 淘汰时，相关效果保持独立行。完整示例见 [SDK 事件示例](../sdk/examples/event_history.py)。

分页中的关键字段：

| 字段 | 含义 |
| --- | --- |
| `events` | 本页固定修订的事件；原始结构与现有日志／Context 一致 |
| `cursor` | 当前页游标，也可用于释放整个查询 |
| `next_cursor`、`has_more` | 下一页与是否存在下一页；没有下一页时 next_cursor 为 null |
| `total_matches`、`page_size`、`offset` | 本次查询匹配数及页位置 |
| `query_id`、`as_of_sequence` | 查询身份及创建时已接收序列 |
| `created_at`、`expires_in_seconds` | 创建现实时间与剩余有效秒数 |
| `filters`、`target_observation`、`coverage` | 实际筛选、目标观测情况和查询创建时的记录器／写盘状态 |

查询冻结首次创建时的事件成员及修订，后续新事件／新修订不改变已有页。再次使用同一个 next_cursor 可以重取该页；外层 request_id／recorded_at 是新请求，剩余有效时间会减少。游标是不可解析、不可拼装的句柄，消费方应原样传回。

分页必须携带首次响应中的 session_id 作为 expected_session_id。读档、重启或 `co.restart` 后旧查询失效。API 2.1 起普通旅行保留查询，加载期间暂不可用。默认 TTL 为 **120 秒现实时间**，暂停游戏也计时；翻页不续期。最后一页不会自动释放，关闭窗口或刷新时主动关闭。

`close_history` 幂等：首次成功为 `{released:true, reason:"closed"}`；已过期、已关闭或运行结束为 `{released:false, reason:原因}`。错误线程和非法游标仍会报错，不会默默执行。关闭旧运行的句柄不会关闭新运行的查询。

默认最多同时 8 个查询、合计 100,000 个事件引用和 256 MiB 估算预算；**这是所有下游及本 MOD 窗口共享的预算**，不是每个 SDK 客户端单独拥有。查询资源不是权限隔离机制。`query_limit/query_budget` 只拒绝新查询，不暂停采集；应缩小范围并及时释放，不应反复重试宽查询。

## 写入外部事件

```python
append_event(producer, payload, *, entities=None, idempotency_key=None,
             expected_session_id)
```

只追加，不提供覆盖或删除。下游用自己的 payload 协议表达更新或撤回；上游不规定对白、展示、角色知识或叙事状态字段。`producer` 是 1–128 个无空格的可打印 ASCII 字符；推荐稳定命名空间如 `example.overlay`，它是自报标识，不是权限认证。

`payload` 为合法 JSON：对象、数组、字符串、有限数字、bool 或 null。只接受普通 Python JSON 类型，字典键必须为字符串；不接受元组、循环引用或游戏对象。编码后上限 64 KiB，深度 16，最多 32,768 个值／键节点。外部 payload 不参与游戏资源重解释和状态判定。

`entities` 为至多 32 个 `sim:<uint64>`／`object:<uint64>` 字符串，默认空列表，按规范化 ID 去重。多实体共享同一个事件；合法但未观测的实体可建立关联，标为未核验，不扩大 Collector 范围、不伪造观测状态、不覆盖游戏身份。没有实体的事件通过全局查询读取。

系统生成 `event_id`、`revision=1`、`event_type="external_event"`、`origin="external"`、`producer`、接收时游戏时间、现实时间及序号。游戏采集事件为 `origin="game"`、`producer=null`；原来的 Hook／通知 `source` 保留。下游需要较早的生成时间时自行写入 payload，不能修改系统时间或伪造游戏因果。

```python
packet = client.get_context("sim", "active", origins=["game"])
receipt = client.append_event(
    "example.overlay", {"text": "今天发生了不少事情。", "private_state": [1, 2]},
    entities=[packet["target"]["key"]], idempotency_key="result-001",
    expected_session_id=packet["session_id"])
```

回执包含 `event_id/session_id/revision/accepted_sequence/duplicate/retained/persistence/persistence_error` 和版本信息。`persistence="accepted"` 表示日志队列已接收且新查询可读；`written` 表示已确认 fsync，后续可结合 `get_status().recorder.persistence.durable_sequence` 核对。游戏线程不等待写盘；稍后的物理写入失败仍可能发生，不能把 accepted 当作持久化承诺。

可选 `idempotency_key` 同样最多 128 个可打印 ASCII 字符。作用域为本次运行加生产者；同键、相同规范化实体集合和 JSON 内容返回原回执，不新增事件、修订或序号；同键不同内容报 `idempotency_conflict`。去重记录在本次运行内持续保留，即使事件被 FIFO 淘汰，重试也不重新插入。去重索引最多 10,000 项且最多 8 MiB；满时拒绝新的键，已有键可重试。无键的每次成功写入都是新事件。

全部外部写入共享每现实秒 20 条、突发 40 条的默认限流，`external_rate_per_second/external_burst` 可配置，实际值见 `get_status().recorder.external`。成功的去重重试不消耗新写入额度。外部事件共享原历史 FIFO 和日志预算，正常写入会占容量、推动旧事件淘汰。

非法请求、外部内存预检查失败、队列繁忙、限流或去重容量不足不暂停游戏采集。`write_busy/rate_limited` 提示稍后重试；输出总预算或单条内存无法满足时仅等待可能无效，应检查状态和配置。物理磁盘错误等共享故障仍会令记录器失败。

## 增量读取

```python
read_event_changes(checkpoint=None, *, start=None, kind=None, identifier=None,
                   origins=None, producers=None, include_internal=None,
                   page_size=50, representation="both", expected_session_id)
```

第一次调用不传 checkpoint：`start=None` 等价 `"now"`，只建立当前位置，不回放历史；`start="retained"` 返回当前保留事件的最新视图。默认不限制实体、混合来源、隐藏内部层；实体查询须同时给 kind 和 identifier。筛选含义与历史查询一致。

后续传 checkpoint，必须省略 `start/kind/identifier/origins/producers/include_internal`，因为检查点已绑定这些条件。页大小和表示形式仍可调整。检查点只在当前运行有效，无分页 TTL，也不会为每次轮询积累永久服务器对象；它仍受 FIFO 保留范围约束。不要解析或修改它。

每轮固定接收序号上界 H，选择上次位置 L 到 H 之间新增或更新且仍保留的事件，按最新接收序号升序返回。一个事件本轮只出现一次，内容固定为 H 时的最新修订；不交付每次中间修订。同一事件以后再次修订，会在下一轮返回。分页期间游戏继续产生的更新不会改变已有页面。

返回标准 HistoryPacket，额外字段位于 `history`：

| 字段 | 含义 |
| --- | --- |
| `scope` | `current_session_change_snapshot` |
| `change_range` | after_sequence、through_sequence、latest_per_event 策略及初始化方式 |
| `checkpoint` | 仅最后一页提供；之前为 null。全部页面处理成功后由下游保存为下一轮位置 |
| `cursor/next_cursor/has_more` | 本轮固定快照的分页位置，继续使用 get_history_page／close_history |
| `coverage` | 当前保留／淘汰和采集／写盘状态，不声称会话历史完整 |

没有匹配事件时仍返回可推进的 checkpoint。中途失败不提交新位置，同一有效页面可重试；消费逻辑用 `(event_id, revision)` 容忍重复。`cursor_expired` 后可从上次已提交的 checkpoint 重建本轮。查询页仍共享最多 8 个、100,000 引用、256 MiB 及 120 秒现实时间 TTL；必须及时释放。

FIFO 若已淘汰检查点之后新增或更新的事件，返回 `history_gap`，不静默跳过。缺口判断保守地覆盖全部来源，可能因被筛掉的其他生产者事件而触发；不承诺仅此来源丢失。由下游明确选择重新 `start="retained"` 或 `start="now"`。磁盘旧日志不能通过增量接口补送。记录器停用或失败时拒绝推进增量。

推荐用 `start="retained"` 完成历史初始化再接续其 checkpoint，避免先查历史、再获取当前位置的时间空窗。下例展示一轮消费；持续运行时可使用 [overlay_events.py](../sdk/examples/overlay_events.py) 中每次回调只处理一页的 `IncrementalReader`，不必一口气读完大批历史。

```python
checkpoint = None
with client.changes(start="retained", producers=["example.overlay"],
                    expected_session_id=session_id) as batch:
    while True:
        consume_idempotently(batch.page["history"]["events"])
        if not batch.has_more:
            checkpoint = batch.checkpoint
            break
        batch.next_page()
# 在以后的模拟线程回调中：
with client.changes(checkpoint, expected_session_id=session_id) as batch:
    # 同样处理所有页后再提交 batch.checkpoint。
    pass
```

## SDK 管理句柄

```python
with client.history("sim", str(sim_id), page_size=15,
                    event_types=["interaction"], from_ticks=start_ticks) as query:
    packet = query.page
    if query.has_more:
        packet = query.next_page()
```

`query.page` 是完整 HistoryPacket；`session_id` 固定，`has_more` 表示还有下一页，`next_page()` 到末尾返回 None。失败不会推进内部游标。`close()` 可重复调用；关闭后 `next_page()` 返回 `query_closed` 错误。修改返回页不会修改提供方或 SDK 保存的导航游标。

`with` 会在正常退出和消费逻辑抛异常时尝试关闭，不用垃圾回收析构函数触发游戏调用。跨多个 UI 回调时持有句柄，并在关闭回调显式释放。示例见 SDK 包的 `examples/consumer.py`。

## 错误契约

直接调用抛 `api.APIError`，SDK 抛 `ContextOverlayError`，均有 `code/message/details` 和 `to_dict()`。调用参数名称错误也转换成 invalid_request。按 code 分支，不解析 message 的措辞。

| code | 消费方处理 |
| --- | --- |
| `dependency_missing`（SDK） | 提示安装提供方，不展示空数据为成功 |
| `incompatible_api`（SDK） | 提示提供方／SDK 升级；不回退到私有接口 |
| `provider_error`（SDK） | 提供方导入、元数据或契约之外失败，保留诊断 |
| `not_ready` | 尚未进入地块、初始化中或启动失败；状态原因在 details；等待下一个合适的游戏回调 |
| `wrong_thread` | 把调用移回游戏模拟线程，不从网络回调重试 |
| `session_changed/session_closed` | session_changed 丢弃旧运行引用；session_closed 等待 ready 后比较 session，旅行恢复时可继续 |
| `collector_disabled` | 当前状态采集关闭；可单独查询历史或显示不可用 |
| `target_unavailable` | 例如没有当前操控 Sim；选择明确实体或等待其可用 |
| `invalid_request` | 修正参数名称、类型、ID、字段、条数等 |
| `invalid_query` | 修正历史时间／类型筛选或页大小 |
| `invalid_cursor` | 只使用原样返回的有效游标 |
| `cursor_expired` | 重新创建查询，不在旧快照上继续翻页 |
| `query_limit/query_budget` | 关闭旧查询、缩小时间范围／筛选 |
| `query_closed`（SDK） | SDK 句柄已关闭，创建新的句柄 |
| `internal_error` | 本 MOD 未预期失败，保留 details 和运行信息用于排查 |
| `payload_limit` | 缩小 payload 的编码大小、深度或节点数 |
| `idempotency_conflict` | 同一提交保持内容不变；新提交使用新键 |
| `dedup_capacity` | 本次运行的新去重键容量已满；不自动丢弃旧键 |
| `rate_limited/write_busy` | 根据 details 中可用的 retry_after_seconds 和运行状态稍后重试 |
| `recorder_disabled/recorder_failed` | 写入与增量无法继续；恢复记录能力，不把失败当空历史 |
| `invalid_checkpoint` | 原样传回检查点，不修改或自行拼接 |
| `history_gap` | 已有事件被淘汰；明确选择重新读 retained 或从 now 开始 |

字段不可用和历史采集失败通常在正常响应中形成 partial，并非全部变成异常。不得将 `disabled/error/out_of_scope/not_observed` 当成数值 0 或“没有发生”。

## 线程、频率和结果时效

附近接口另外使用 `target_out_of_scope` 和 `spatial_unavailable`；旧提供方不支持邻近能力时 SDK 返回 `capability_unavailable`。具体语义见[附近查询错误表](#附近实体查询)。

Runtime 记录初始化它的线程身份；有活动 Runtime 时，公共运行接口在任何游戏对象读取或查询表操作之前验证调用线程。嵌入式游戏线程不假定等于 Python 的 `main_thread()`。`get_api_info` 无此要求；尚未建立 Runtime 时状态可报告 waiting_for_zone，但这不意味着游戏调用支持后台线程。

一次 API 调用同步完成；历史查询的首次筛选会检查候选事件，不承诺零耗时。推荐按玩家操作或自己的低频游戏回调查询，选择所需字段和有限时间窗口，不每帧扫描全部实体或全部历史。

将返回的普通字典交给后台模型或网络逻辑；不要把游戏对象、SDK 查询句柄或 `_runtime` 交给后台。模型结果返回后，通过下游自己的游戏线程回调检查当前 session_id、目标身份和最近 request_id，再展示或丢弃。session_id 相同并不能证明同一次运行内的较旧请求仍然适合展示。


## 摄像机视锥查询

API 2.3 提供 [`get_camera_view`](camera-view.md)：按需返回当前区域近似视锥内的全部实体摘要，包括地块外及不同楼层，忽略遮挡。通过 `context.camera_view` 发现能力；可覆盖垂直 FOV、宽高比及最远深度。完整参数、相机时效、扫描覆盖和错误见[接口约定](camera-view.md)，已有证据与复测步骤见[验收记录](camera-view-validation.md)。此接口不改变 `get_context` 的当前地块限制。

## 附近实体查询

先通过能力标识 `context.nearby_entities` 判断支持情况。查询返回候选实体，再按需调用 `get_context`；不连续追踪位置。

### 参数

```python
get_nearby_entities(identifier="active", *, kinds=("sim",), radius=None,
                    metric="horizontal", same_level=True, same_room=False,
                    include_self=False, limit=32, expected_session_id=None)
```

| 参数 | 约定 |
| --- | --- |
| identifier | 中心 Sim，支持 `active` 或正的 64 位 Sim ID；不接收游戏对象。active 只解析一次 |
| kinds | 非空、不重复的 list／tuple，可选 `sim`、`object`；默认只查 Sim。Sim 按游戏 `is_sim` 分类，可能包含宠物等非人类 Sim |
| radius | 0–1,000,000 的有限数值，单位 `game_world_units`；边界包含。None 只允许与 same_room=True 同用 |
| metric | `horizontal`（默认，x/z 平面）或 `euclidean`（三维直线距离），同时决定半径筛选和排序 |
| same_level | 默认 True，要求游戏 level 相等；不使用高度差或路由表面相等代替楼层 |
| same_room | 默认 False；True 时增加同房间约束。与半径共同指定时取交集 |
| include_self | 默认 False；True 时中心 Sim 也须满足 kinds 和空间筛选条件 |
| limit | 整数 1–64，默认 32；不是候选扫描上限，不创建分页游标 |
| expected_session_id | 可选会话约束；读档／重启后拒绝旧 session；API 2.1 普通旅行保留 session |

同房间、不限定半径：

```python
client.get_nearby_entities("active", same_room=True, radius=None)
```

跨楼层按三维距离查询：

```python
client.get_nearby_entities("active", radius=12, metric="euclidean", same_level=False)
```

`get_api_info().nearby` 公布类型、距离指标、单位、最大返回数、候选扫描预算、最大半径及房间筛选能力。

### 返回结构与完整性

```text
kind = "nearby_entities"
api_version / module_version / schema_version
session_id / request_id / recorded_at / provenance
target                  固定的中心 Sim 身份
scope                   active_lot_instantiated，场外排除
query                   实际执行参数、单位、distance_basis、排序规则
read_started / read_finished
origin                  中心的 position、level、routing_surface、room
results[]
  entity                kind / id / key / name；物件有 definition_id
  identity_status       身份名称读取失败时保留 ID，状态为 error
  distance              horizontal / vertical / euclidean
  spatial               position / level / routing_surface / room
  relative              same_lot / same_level / same_room / same_routing_surface
count / matched_count / matched_count_exact / truncated
coverage / status
```

空间字段与相对关系使用 `{status, value, source, reason?}`。ID 和时钟 ticks 为字符串，楼层和距离为数值。vertical 是非负高度差。距离未四舍五入后再筛选，不承诺等于米、可行走路程或到家具外轮廓的距离。

位置使用实体的世界坐标点。routing_surface 保留 primary_id、secondary_id 和 type；与 level 分开。room.value 为 `{zone_id, id}`；同房间比较 zone、游戏房间 ID 和 level。房间名称／用途不在此接口范围内。

排序优先使用所选 metric，距离相同按 kind、数值 ID 排序。ID 不转换成浮点数。在扫描完整时，先考察全部候选再保留最近 limit 个；不会直接截取管理器的前 limit 个实体。

| 标记 | 含义 |
| --- | --- |
| count | 实际返回的结果数 |
| matched_count | 已核实匹配的数量，包含因 limit 未返回的条目 |
| matched_count_exact | 查询是否完成所有相关候选的判定；False 时 matched_count 仅为已知数量 |
| truncated | 已知匹配数超过 limit，结果经过数量截断；不代表有可用游标 |
| coverage.complete | 枚举完整，且没有候选因为读数／必需筛选字段不可用而无法判定 |
| coverage.enumeration_complete | 是否完成候选枚举；扫描预算耗尽或枚举器异常时为 False |
| coverage.scanned_count / candidate_count | 遍历到的管理器对象数／通过类型与范围筛选并去重后的候选数 |
| coverage.unresolved_count / reasons | 无法判定的候选数量，以及有限的原因计数 |
| status | `complete` 或 `partial`；可选空间信息不可用也会令整个包为 partial |

`truncated=False` 不保证查询完整，还要检查 coverage。`coverage.complete=True` 表示当前限定范围内的查询完整，不表示场外、库存或隐藏实体也被查询。

可出现 `status=partial` 且 `coverage.complete=True`：例如已确认所有半径和楼层条件，但室外房间信息未知。可选房间信息不足不改变已经核实的几何邻近结果。

中心必需空间信息不可用返回错误，不输出“正常但没有邻居”。个别候选不可判定时跳过该候选，返回已确认结果并标记 coverage 缺口。覆盖不完整时，最近 N 个仅指已成功判定的候选。

### 房间、库存与采集范围

使用当前对象管理器的非隐藏实例，复用当前地块范围检查。Sim 必须有活动实例；场外 Sim、未实例化 Sim 排除。非 Sim 对象包括可枚举的家具、食物和装饰等，不保证存在玩家可点击的交互。墙体、地板和纯客户端视觉元素不保证作为独立 GameObject 枚举。

另外检查实体及父对象是否处于库存；背包、冰箱等容器里的内容不算摆放在附近。桌面插槽物件、携带物件若仍是范围内非隐藏世界实例，可以按其世界坐标进入结果。父链异常会形成缺口。

EA Python 中存在 `build_buy.get_room_id` 原生别名，并在房间物件筛选中使用它。正的整数返回作为游戏房间标识；None、0、负值及异常保留不可用状态，整数哨兵值放在 raw_id。其室外／特殊场景含义尚未实机验证，不能把两个无效结果相等解释为同房间。正房间 ID 也不代表具有人类语义的“厨房”等房间用途。

默认只为中心和最终返回的实体查询房间；启用 same_room 时，对已通过半径／楼层筛选的候选查询房间。中心房间无可靠结果则抛 `spatial_unavailable`，不会降级为仅按距离查询。

本接口不判断视线、寻路、可交互性、听见／目睹或角色知情程度。

### 生命周期、开销与错误

一次调用同步完成，不缓存游戏对象、不创建历史快照或定时采样。扫描最多 10,000 个管理器条目，临时保留最多 64 个结果对象，扫描去重键也受扫描上限约束。扫描超限在 coverage 中报告。本次范围并不保证原子世界快照，保留读取时间区间；性能尚未实机测量，不建议每帧调用。

查询与后续 `get_context` 是两次读取。session 一致只证明同一次运行，角色在期间仍可能移动；需要最新邻近关系时重新查询。单次查询不影响事件 FIFO、历史页预算、写盘队列，也不依赖事件记录器／中文解释启用。Collector 关闭时返回 `collector_disabled`。

沿用 `invalid_request/not_ready/wrong_thread/session_changed/session_closed/collector_disabled`；新增相关错误：

| code | 含义 |
| --- | --- |
| target_unavailable | 无当前操控 Sim，或指定中心没有可读取实例 |
| target_out_of_scope | 中心实例不在当前采集范围 |
| spatial_unavailable | 中心缺少必需位置／楼层／房间；details 中保留字段与证据 |
| capability_unavailable | SDK 检测旧提供方未声明 context.nearby_entities |


## Autonomy 与文本详情

`events.autonomy_decision` 支持 `fields=["autonomy.decision"]` 的历史筛选；获取子行为决策时使用 `include_internal=True`。`payload.interaction_event_id` 与交互的 `facts.decision_event_id` 相互关联。`rendered.history[].decision_details` 提供分层候选与评分说明。

`text.resource_details` 表示可选 `rendered.resource_details`：包含 `items`、`truncated`、`limit`。每项有资源身份、`label/role/text/status/basis/evidence_ref`；资源说明与名称独立，静态参考不代表当时使用。每包最多 128 项、遍历最多 20,000 个节点，单个游戏详情最多 32 项。

当前字段、事实关联及概率解释见[架构与采集语义](architecture.md)。SDK 包附带[Context 合成样例](../sdk/examples/context-packet.json)、[附近实体合成样例](../sdk/examples/nearby-packet.json)和消费代码示例；样例不作为实机证据。

## 这轮怎么试

先给团队接到自己的 MOD 里用：读一份 Context，写一条自己的 JSON，再查回来。接着试来源筛选、无实体记录、增量、重复提交和旅行。安装包带 SDK 和例子，模型与 UI 由自己的 Overlay 负责。[快速接入](quickstart.md)可以从头跟着做。

游戏内现有实体历史窗口默认显示混合事件并支持来源筛选；外部行显示生产者、时间和关联实体，详情以纯文本 JSON 分段展示，不约定展示文本字段。无实体记录用全局 API 或离线报告查看。

有问题就把操作步骤、版本、错误码和相关日志片段发回来，payload 只分享排查需要的部分。我们先收一轮接入反馈，再决定下一步。当前测过什么看[验证摘要](validation.md)。
