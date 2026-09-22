# 读取指南与能力矩阵

这页帮助你选择读取路径。第一次接入看[快速接入](quickstart.md)，准确参数、默认值和错误看 [API 参考](public-api-v2.md)。适用版本见[文档入口](index.md)。

## 先选读取路径

| 想知道什么 | 首选入口 | 关键选择 |
| --- | --- | --- |
| 某个 Sim / 物件现在怎样 | `get_context` | 实体、当前字段、是否附带近期历史 |
| 某段时间发生过哪些事件 | `query_history` / SDK `history` | 实体、时间、类型、结果、来源 |
| 从上次读取后新增或修订了什么 | `read_event_changes` / SDK `changes` | 来源筛选、checkpoint |
| 附近有哪些 Sim / 物件 | `get_nearby_entities` → `get_context` | 距离、楼层、房间、类型 |
| 同一批事件的原始过程、活动结构或简短回顾 | `query_event_view` | `records` / `events` / `organized` / `recap` |

`query_history` 适合按条件查仍保留的历史；四层视图适合阅读当前 session 已写盘的固定截点，不受历史 FIFO 淘汰影响。**四层首版只支持完整会话时间范围**；按时间筛选历史用 `query_history`，或由下游从取得的 recap 中选择条目。

## Context：选择当前状态

| `fields` | 读取内容 |
| --- | --- |
| `identity` | 身份、ID、名称和名称解析状态 |
| `location` | 地块、坐标、楼层等位置数据 |
| `time` | 当前游戏时间和星期 |
| `interactions` | 当前交互及其阶段 |
| `needs` | Sim 需求值，使用游戏内部单位 |
| `buffs` | 当前 Buff 及文本、来源状态 |
| `relationships` | 当前可读取的关系与关系标记 |
| `object_states` | 物件品质、新鲜度、清洁或损坏等状态 |

不传 `fields` 时，Sim 默认读前七项，Object 默认读 `identity/time/location/object_states`。例如只显示当前需求和 Buff：

```python
context = client.get_context(
    "sim", "active", fields=["identity", "needs", "buffs"],
    include_history=False,
)
```

`include_history` 决定是否附带近期历史；用 `history_limit` 控制条数，`include_internal` 控制内部步骤，`origins/producers` 筛选附带历史的来源。`representation` 选择 `raw/text/both`；文本形式也保留原始证据，不是单个字符串。

Context 要求指定实体。字段不可用时读取其 `status/reason`，不要把缺失当成 0；`complete` 也不保证名称全部解析或历史完整。

## Events：选择发生过的事情

### 按条件查历史

| 维度 | 选择方式 |
| --- | --- |
| 实体 | 指定 Sim / Object；`query_history(None, None, ...)` 查全会话及无实体事件 |
| 时间 | `time_field=first_observed/started/ended`，`from_ticks/to_ticks` 为游戏 ticks，范围 `[from, to)` |
| 类型 | `event_types`：`interaction/state_change/game_event/external_event` |
| 变化内容 | `fields`：例如 `buffs`、`relationship.bits`、`skill.level`、`statistic.direct` |
| 交互结果与定义 | `outcomes`：`completed/cancelled/failed/unknown`；`tuning_ids`：交互定义 ID |
| 来源 | `origins=["game"]` / `["external"]`，或 `producers=["example.overlay"]` |
| 展示 | `include_internal` 包含内部步骤，`order` 排序，`group_effects` 归并明确关联的效果 |

不同筛选条件取交集。下面只读取最近的游戏交互；使用 `session_id` 前先从 `client.get_status()` 取得当前会话身份：

```python
with client.history(
    "sim", "active", origins=["game"], event_types=["interaction"],
    order="desc", page_size=15, expected_session_id=session_id,
) as query:
    first_page = query.page["history"]["events"]
```

注意两种 `fields`：`get_context(fields=["relationships"])` 读取当前关系；`query_history(fields=["relationship.bits"])` 筛选关系标记变化。它们不能互换。

### 持续读取变化

首次用 `read_event_changes(start="retained", ...)` 读取仍保留的事件；逐页处理，并按 `(event_id, revision)` 幂等消费。整轮处理成功后保存 `checkpoint`，下一轮用它续读。若已越过 FIFO 保留范围，接口返回 `history_gap`。完整处理方式见 [API 增量契约](public-api-v2.md#增量读取)。

### 选择四层的阅读深度

| 视图 | 一项代表什么 | 适合用途 |
| --- | --- | --- |
| `records` | 一次事件修订，或一条相关观察／共享边界记录 | 回查采集过程 |
| `events` | 一个事件的最新完整版本，仍包含内部事件 | 自行处理完整事件 |
| `organized` | 一个组织单元，或一个独立保留的来源事件 | 看哪些步骤、结果和状态属于同一活动 |
| `recap` | 一项活动、结果、关系观察、状态组或待核查动作 | 给使用者或 LLM 阅读 |

用新一局 **Nova Curious** 的实际数据看差别：**3,653 records → 1,454 events → 890 organized → 70 recap**。一次“烹饪薄煎饼”的活动本体由 15 个交互事件、69 条修订组成；连同明确关联的结果、状态和决策回查时是 37 个事件、100 条修订。逐层分类、样例与计数口径见[四层事件实例](event-layers-example.md)。

这些数量的单位不同，也不是固定压缩比例。组织层保留全部来源的去向；recap 把细节留待按需展开。`recap` 当前仅支持 Sim，各层通过 `source_snapshot_id` 共用截点。用 `explain_event_view` 的 `units/events/revisions` 回查内容，`lineage/policy/labels` 回查来源、阅读去向和名称依据。请求、异步分页和关闭见[分层事件查询](event-views.md)。

## 附近实体与外部事件

`get_nearby_entities` 按 `kinds/radius/metric/same_level/same_room/include_self/limit` 返回空间候选，再对选中的实体读 Context。它不判断视线、寻路、目睹、听见或人物知情。

下游用 `append_event` 追加自己的 JSON，指定 `producer`、`entities`、`idempotency_key` 和 `expected_session_id`；用上表的来源条件读回来。外部事件不会覆盖游戏记录，实体关联也不证明该 Sim 实际参与。完整闭环见[快速接入](quickstart.md)。

## 给 LLM 选择材料

先用 Context 取得所需当前状态，再读 recap；有疑问时沿引用取证据。由下游将“当前状态、选中的经历、待核查内容、用户问题”组织成 Context Pack，再交给 Prompt 模板。当前 MOD 不调用模型；人物记忆、知情范围和人格由下游设计。

读取时保留三条边界：

- Context 是一次读取；目前没有需求／关系的定时差值历史。
- 没有事件记录不等于没有发生；结合采集范围、FIFO、写盘状态和 coverage 判断。
- 运行时接口以当前 session 为边界；旧 session 可离线分析。实体关联不等于参与或知情，recap 不补写人物动机。
