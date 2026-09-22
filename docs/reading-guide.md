# 读取指南与能力矩阵

当前源码为 **ContextOverlay 0.10.10**，公共 API / SDK 为 **2.2.0**，数据 schema 为 **2**。

这页回答的是“我想读取什么，应该选择哪个接口”，不重复完整的参数和错误契约。准确签名、字段结构和边界以 [API 参考](public-api-v2.md) 为准；四层事件视图的分页、预算和证据回查见[分层事件查询](event-views.md)。

## 先判断你要的是什么

| 目标 | 首选入口 | 读取层级 |
| --- | --- | --- |
| 读取当前 Sim 或物件的状态 | `get_context` | 当前快照，可选附带近期历史 |
| 查某个实体最近发生的事件 | `query_history` | 当前会话的历史事件 |
| 查全会话事件或无实体事件 | `query_history(None, None, ...)` | 当前会话的全局历史 |
| 持续接收新事件和修订 | `read_event_changes` / SDK `changes` | 增量事件 |
| 找附近的 Sim / 物件再读取状态 | `get_nearby_entities` → `get_context` | 空间候选加当前快照 |
| 审计原始记录和完整修订链 | 事件视图 `records` | 原始来源 |
| 读取每个事件的最新完整版本 | 事件视图 `events` | 最新事件 |
| 查看活动、结果和状态的组织关系 | 事件视图 `organized` | 结构化组织层 |
| 给 Overlay 或 LLM 提供短版经历材料 | 事件视图 `recap` | 默认阅读层 |

`query_history` 和事件视图不是同一件事：前者主要解决“按什么条件筛选历史”，后者主要解决“以什么抽象层级阅读同一份持久化来源”。

## 1. 读取当前 Context

### 可选择的当前字段

`get_context` 读取指定实体的当前状态。`fields` 控制当前快照读取哪些字段：

| 字段 | 读取内容 |
| --- | --- |
| `identity` | Sim 或物件的身份、ID、名称和名称解析状态 |
| `location` | 地块、坐标、楼层、routing surface 等位置数据 |
| `time` | 当前游戏时间、星期和相关时间信息 |
| `interactions` | 当前交互、阶段和可读取的交互信息 |
| `needs` | Sim 当前需求值；数值是游戏内部单位，不是百分比 |
| `buffs` | 当前 Buff 及其文本和来源状态 |
| `relationships` | 当前可读取的关系和关系标记 |
| `object_states` | 物件状态，例如品质、新鲜度、清洁或损坏 |

不传 `fields` 时，默认读取：

- Sim：`identity`、`location`、`time`、`interactions`、`needs`、`buffs`、`relationships`
- Object：`identity`、`time`、`location`、`object_states`

只读取部分字段时：

```python
context = client.get_context(
    "sim",
    "active",
    fields=["identity", "needs", "buffs", "interactions"],
    include_history=False,
)
```

### Context 还可以怎样收窄

| 参数 | 作用 |
| --- | --- |
| `kind` | 选择 `sim` 或 `object` |
| `identifier` | 选择实体；Sim 可以使用 `active`，也可以使用正的实例 ID |
| `include_history` | 是否在当前快照旁边附带近期历史 |
| `history_limit` | 近期历史最多返回多少条，默认 15，范围为 1–500 |
| `include_internal` | 是否包含内部步骤，默认不包含 |
| `representation` | `raw`、`text` 或 `both`；文本解释仍保留原始证据 |
| `origins` | 附带历史只看 `game` 或 `external` 等来源 |
| `producers` | 附带历史只看指定外部生产者 |
| `expected_session_id` | 限定必须属于指定运行，避免读档或重启后误用旧结果 |

`get_context` 要求有明确实体，不能用来查询全会话，也不提供任意时间范围或事件类型筛选。需要复杂历史条件时使用下一节的 `query_history`。

字段可能返回 `available`、`not_present`、`not_applicable`、`unsupported`、`out_of_scope`、`disabled` 或 `error` 等状态。`complete` 表示本次请求没有被模块或字段可用性阻断，不表示所有名称都成功翻译，也不表示历史完整。

## 2. 筛选历史 Events

### `query_history` 选择哪些事件

历史查询同时支持实体范围、时间范围、事件类型、变化字段、结果和来源筛选。不同条件取交集。

```python
with client.history(
    "sim",
    "active",
    origins=["game"],
    event_types=["interaction", "state_change"],
    outcomes=["completed", "failed"],
    time_field="first_observed",
    order="desc",
    page_size=15,
    expected_session_id=session_id,
) as query:
    first_page = query.page["history"]["events"]
```

| 选择维度 | 当前支持 |
| --- | --- |
| 实体范围 | 指定 Sim、Object，或使用 `query_history(None, None, ...)` 查询全会话和无实体事件 |
| 时间字段 | `first_observed`、`started`、`ended` |
| 时间范围 | `from_ticks`、`to_ticks`，使用游戏整数 ticks，范围为 `[from, to)` |
| 事件类型 | `interaction`、`state_change`、`game_event`、`external_event` |
| 变化字段 | 例如 `buffs`、`relationship.bits`、`skill.level`、`statistic.direct` |
| 交互结果 | `completed`、`cancelled`、`failed`、`unknown` |
| 交互定义 | `tuning_ids`，选择交互定义 ID，不是交互实例 ID |
| 来源 | `origins=["game"]` 或 `origins=["external"]` |
| 外部生产者 | `producers=["example.overlay"]` |
| 内部步骤 | `include_internal=True` |
| 顺序 | `asc` 或 `desc` |
| 效果归并 | `group_effects=True`，将明确关联的效果放入动作的 `effects` |

这里的 `fields` 和 Context 的 `fields` 含义不同：

- `get_context(fields=["needs", "relationships"])`：读取当前需求和关系状态。
- `query_history(fields=["relationship.bits", "statistic.direct"])`：筛选发生过相关变化的事件。

历史事件可能包含交互阶段、Buff 变化、关系标记、物件状态变化、游戏事件、自主决策和外部 Overlay 写入的 JSON。没有历史记录不证明游戏中没有发生对应事情；还要结合采集范围、FIFO、写盘状态和 `coverage` 判断完整性。

### 持续读取新增 Events

需要持续消费事件时使用 `read_event_changes`，而不是每次重新查询全部历史：

1. 第一次使用 `start="retained"` 初始化当前仍保留的事件。
2. 逐页消费并按 `(event_id, revision)` 做幂等处理。
3. 只有本轮所有页面成功处理后，才保存返回的 `checkpoint`。
4. 下一轮只传 `checkpoint`，继续读取新增或更新的事件。

增量读取仍受 FIFO 保留范围约束；如果 checkpoint 之前的内容已经被淘汰，会返回 `history_gap`，不会静默假装连续。

## 3. 选择四层事件视图

四层视图从同一份持久化来源生成，但阅读目的不同：

| 视图 | 主要内容 | 适合什么时候用 |
| --- | --- | --- |
| `records` | 原始记录、全部修订、相关 observation 和 session / zone 边界 | 审计采集过程，检查某个事件每次如何变化 |
| `events` | 按实体索引选中的每个事件的最新完整版本 | 下游要自行筛选和处理完整事件 |
| `organized` | 活动、结果、状态、决策、背景、依赖和独立来源 | 需要事件之间的组织关系和证据 |
| `recap` | 活动、结果、关系观察、状态组和待核查动作 | 默认展示、人物经历摘要或 LLM Context Pack 的事实来源 |

典型选择：

```text
想知道某个事件的原始修订过程 → records
想拿到完整最新事件自行分析 → events
想知道哪些事件组成一次活动 → organized
想给人或模型看一份压缩后的经历 → recap
```

事件视图首版使用当前 `durable_session` 的固定持久化截点；`recap` 首版只支持 Sim。请求建立后，后续追加不会改变已经打开的快照。页面读完后应关闭请求；具体状态、分页、预算和错误见[分层事件查询](event-views.md)。

已读取的视图条目可以使用 `explain_event_view` 回查：

| facet | 用途 |
| --- | --- |
| `lineage` | 来源 event、最新 revision、组织成员和范围外标记 |
| `policy` | 为什么进入正文、详情、待核查或独立来源 |
| `labels` | 原名称、解析状态、显示释义和文本来源 |
| `revisions` | 关联事件的完整日志修订 |
| `events` | 关联来源的完整最新事件 |
| `units` | 关联的组织单元 |

## 4. 先找附近实体，再读取 Context

`get_nearby_entities` 返回空间上满足条件的候选实体，不直接返回完整 Context。可按以下条件收窄：

| 参数 | 作用 |
| --- | --- |
| `kinds` | `sim`、`object` 或两者 |
| `radius` | 距离范围 |
| `metric` | `horizontal` 或 `euclidean` |
| `same_level` | 是否限定同一楼层 |
| `same_room` | 是否限定同一房间 |
| `include_self` | 是否包含中心 Sim |
| `limit` | 最多返回数量 |

它不判断视线、寻路、可交互性、是否听见或目睹，也不判断角色是否知情。需要状态时，应对返回的候选再次调用 `get_context`。

## 5. 外部 Events 如何写入和读取

下游可以使用 `append_event` 写入自由 JSON。外部事件只追加，不覆盖或删除游戏事件：

```python
receipt = client.append_event(
    "example.overlay",
    {"text": "Overlay 生成了一段旁白"},
    entities=["sim:123"],
    idempotency_key="result-001",
    expected_session_id=session_id,
)
```

读取时使用：

- `origins=["external"]`：只看外部事件；
- `producers=["example.overlay"]`：只看自己的事件；
- 不关联实体的外部事件：使用全会话 `query_history(None, None, ...)`；
- 持续接收自己的新事件：使用 `read_event_changes` 加 `producers`。

外部事件的实体关联不等于游戏采集已经观测到该实体，也不等于该 Sim 参与、知情或记住了这件事。

## 6. 为 LLM 组装 Context Pack

当前 MOD 不调用 LLM。推荐由下游按以下顺序组装文字 Context Pack：

1. 用 `get_context` 读取当前需要的状态，例如 `identity`、`location`、`needs`、`interactions`。
2. 用 `client.query_event_view("recap", "sim", ...)` 读取近期或会话范围的压缩经历。
3. 如果某个结论需要核实，再用 `explain_event_view` 或同源 `events` 回查证据。
4. 将当前状态、近期经历、待核查内容和任务问题按固定顺序拼成文字。
5. 将这段 Context 交给下游的 Prompt 模板，让模型只返回文字。

不要把 `recap`、Prompt 和角色记忆混成一个层级：

- `recap` 负责整理可观测事实；
- Context Pack 负责为具体任务选择事实；
- Prompt 负责规定回答方式和事实边界；
- 角色记忆、知情范围和模型人格属于下游逻辑。

## 7. 常见目标与推荐路径

| 想完成的事 | 推荐路径 |
| --- | --- |
| 显示当前 Sim 的需求和 Buff | `get_context(fields=["needs", "buffs"])` |
| 显示当前交互 | `get_context(fields=["interactions"])` |
| 显示当前物件品质和状态 | `get_context("object", object_id, fields=["identity", "object_states"])` |
| 查某个 Sim 最近的游戏交互 | `query_history(..., origins=["game"], event_types=["interaction"])` |
| 查某段时间内失败的交互 | `query_history(..., time_field=..., from_ticks=..., to_ticks=..., outcomes=["failed"])` |
| 查自己的 Overlay 事件 | `query_history(..., origins=["external"], producers=["example.overlay"])` |
| 监听新事件 | `read_event_changes(start="retained", ...)`，之后保存 checkpoint |
| 分析原始采集和修订 | 事件视图 `records` + `lineage` / `revisions` |
| 分析最新事件 | 事件视图 `events` |
| 生成活动和结果结构 | 事件视图 `organized` |
| 生成 LLM 的近期经历材料 | `recap` + 当前 Context + 必要证据回查 |

## 8. 读取边界

- Context 是一次读取，不是持续采样；当前没有需求或关系的定时差值历史。
- 历史只包含模块实际观察到并仍可查询的内容；没有记录不等于没有发生。
- 当前运行时 API 以 session 为边界；旧 session 文件可以离线分析，但运行时接口暂不跨 session 查询。
- 实体关联不自动代表参与、知情、目睹、听见或记忆。
- `get_context` 的 `fields` 选择当前状态；`query_history` 的 `fields` 选择事件变化字段，二者不能混用。
- `recap` 是压缩后的事实视图，不是最终 Prompt，也不应该自行补写人物动机。

完整的字段返回结构、错误、分页和线程约定见 [API 参考](public-api-v2.md)；第一次接入可从[快速接入](quickstart.md)开始。
