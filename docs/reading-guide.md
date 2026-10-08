# 读取指南与能力矩阵

这页帮助你选择读取路径。第一次接入看[快速接入](quickstart.md)，准确参数、默认值和错误看 [API 参考](public-api-v2.md)。适用版本见[文档入口](index.md)。

0.13.0 起，Inspector、Context、附近查询和原生事件采集覆盖当前已加载区域内的非隐藏世界实例，包含门外、人行道和公共空间。库存内容及未加载区域不在当前状态范围内；已保留的历史仍可按已知 ID 查询。`scope.lot_id`、事件 `observation_scope.lot_id` 和 Context `location.lot_id` 是采集区域的活动地块信息，不代表人物实际在地块内。

## 先选读取路径

| 想知道什么 | 首选入口 | 关键选择 |
| --- | --- | --- |
| 某个 Sim / 物件现在怎样 | `get_context` | 实体、当前字段、是否附带近期历史 |
| 某个人物最近发送了哪些气泡 | `get_context(fields=["balloons"])` | 游戏时间窗口、返回上限；更多事件按返回参数分页 |
| 某段时间发生过哪些事件 | `query_history` / SDK `history` | 实体、时间、类型、结果、来源 |
| 从上次读取后新增或修订了什么 | `read_event_changes` / SDK `changes` | 来源筛选、checkpoint |
| 附近有哪些 Sim / 物件 | `get_nearby_entities` → `get_context` | 距离、楼层、房间、类型 |
| 当前镜头的近似视锥里有哪些实体 | [`get_camera_view`](camera-view.md) | 类型、FOV、宽高比、可选最远深度；检查 coverage 和相机时效 |
| 同一批事件的原始过程、活动结构或简短回顾 | `query_event_view` | `records` / `events` / `organized` / `recap` |

`query_history` 查询内存中仍保留的历史；四层视图读取当前 session 已写盘的固定截点，不受历史 FIFO 淘汰影响。API 2.6.0 的 `events` 视图支持时间、字段、角色和区域访问筛选；`records/organized/recap` 仍基于完整会话来源，避免裁掉组织所需的依赖事件。落盘截点可能暂时落后于已接收事件。

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
| `balloons` | 从事件系统查询本次区域访问的气泡，默认最近 5 个游戏分钟；返回继续查询的参数，不代表此刻正在显示 |

不传 `fields` 时，Sim 默认读前七项及 `balloons`，Object 默认读 `identity/time/location/object_states`。气泡内容、来源与覆盖边界见[气泡采集](balloons.md)。例如只显示当前需求和 Buff：

```python
context = client.get_context(
    "sim", "active", fields=["identity", "needs", "buffs"],
    include_history=False,
)
```

`include_history` 决定是否附带普通近期历史；用 `history_limit` 控制条数，`include_internal` 控制内部步骤，`origins/producers` 筛选附带历史的来源。这些参数不筛选 `balloons` 字段；气泡使用 `balloon_window`，默认最多返回 50 条。Context 不持有气泡游标，后续按 `history_query/durable_query` 创建分页请求。`representation` 选择 `raw/text/both`；文本形式也保留原始证据。

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
| 角色与区域访问 | `entity_role` 匹配目标在事件中的角色，气泡自身发送用 `subject`；`zone_visit` 限定某次区域访问 |
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
| `recap` | 一项活动、结果、关系观察、状态组、待核查动作或气泡 | 给使用者或 LLM 阅读 |

用 **2026-09-22、0.10.10 的 Nova Curious 历史样例**看差别：**3,653 records → 1,454 events → 890 organized → 70 recap**。一次“烹饪薄煎饼”的活动本体由 15 个交互事件、69 条修订组成；连同明确关联的结果、状态和决策回查时是 37 个事件、100 条修订。逐层分类、样例与计数口径见[四层事件实例](event-layers-example.md)。

这些数量的单位不同，也不是固定压缩比例。组织层保留全部来源的去向；recap 把细节留待按需展开。`recap` 当前仅支持 Sim，各层通过 `source_snapshot_id` 共用截点。用 `explain_event_view` 的 `units/events/revisions` 回查内容，`lineage/policy/labels` 回查来源、阅读去向和名称依据。请求、异步分页和关闭见[分层事件查询](event-views.md)。

## 空间查询与外部事件

`get_nearby_entities` 按 `kinds/radius/metric/same_level/same_room/include_self/limit` 返回空间候选，再对选中的实体读 Context。它不判断视线、寻路、目睹、听见或人物知情。

`get_camera_view` 以最近一次有效相机同步值计算近似视锥，按需返回当前区域全部已确认命中摘要，包括地块外和各楼层，忽略遮挡。默认角度与宽高比可覆盖；`far` 是相机前向深度。实体选中后可按 ID 读取 Context，包括仍在当前区域中的地块外世界实例；后续卸载、隐藏或切换区域会影响读取。`coverage.complete` 说明扫描完整性，`camera.freshness` 说明同步时效，两者都不代表渲染器精确可见性或 Sim 知情。

下游用 `append_event` 追加自己的 JSON，指定 `producer`、`entities`、`idempotency_key` 和 `expected_session_id`；用上表的来源条件读回来。外部事件不会覆盖游戏记录，实体关联也不证明该 Sim 实际参与。完整闭环见[快速接入](quickstart.md)。

## 给 LLM 选择材料

先用 Context 取得所需当前状态，再读 recap；有疑问时沿引用取证据。由下游将“当前状态、选中的经历、待核查内容、用户问题”组织成 Context Pack，再交给 Prompt 模板。当前 MOD 不调用模型；人物记忆、知情范围和人格由下游设计。

读取时保留三条边界：

- Context 是一次读取；目前没有需求／关系的定时差值历史。
- 没有事件记录不等于没有发生；结合采集范围、FIFO、写盘状态和 coverage 判断。
- 运行时接口以当前 session 为边界；旧 session 可离线分析。实体关联不等于参与或知情，recap 不补写人物动机。

查询的 `limit/page_size` 只限制本次返回数量；历史 FIFO 限制内存保留，落盘状态说明持久化进度。这三个条件不能互相替代，具体判断见[气泡窗口完整性](balloons.md)和[写盘完整性](run-output.md#怎样判断这一局是否写完整)。
