# 游戏内分层事件查询

当前源码为 MOD 0.10.10，API / SDK 为 2.2.0。提供 `event_views.query`、`event_views.explain` 和 `event_views.durable_session`；公共方法从游戏线程调用，后台任务只处理日志和普通数据，无需游戏外服务。原 `query_history`、`read_event_changes` 和 `get_context` 保持兼容。

先用 [Nova 的真实实例](event-layers-example.md)理解每层的条数、分类和来源关系。本页给出准确接口契约；实际调用与验收见[分层接口验收步骤](event-views-validation.md)。

## 四个视图

| view | items | 范围 |
| --- | --- | --- |
| `records` | `{item_id, record}`，record 是原日志 JSON | 人物最终关联事件的完整修订链、结构上关联的 observation、共享 session/zone 边界 |
| `events` | `{item_id, event}`，event 是完整最新修订 | 按实体索引选中的事件，包含内部事件，不应用内存 FIFO |
| `organized` | `{item_id, kind:"unit", lane, unit}` 或 `{item_id, kind:"standalone", event_id, revision, category, recap_disposition, evidence_ref}` | 全部来源具有单元成员关系或独立入口，补入依赖上下文 |
| `recap` | `{item_id, section, value}` | 默认阅读内容；section 为 activities/results/relationship_observations/states/review_actions |

组织层保留背景、技术细节、未知项、外部事件，并用引用压缩字段；完整候选评分、原始名称等从同源 events/revisions 回查。standalone 的 recap_disposition 表示默认阅读策略去向，不表示来源删除。实体索引相关不等于参与或知情。

组织器可补入人物索引外的依赖事件，通过组织项的解释接口读取，不扩大 events/records 的人物索引范围。计数口径与实例见[Nova 案例](event-layers-example.md)，历史验收对照见[验证摘要](validation.md)。

## 请求、状态、分页和关闭

```python
request = client.query_event_view(
    "recap", "sim", str(sim_id), expected_session_id=session_id,
    source="durable_session", profile="recap_v1", page_size=20,
)

# 同一来源截点打开另一层；保留原请求，直到新请求建立。
# source_snapshot_id 在 building 状态就可用于复用，不必等待首层完成。
organized = client.query_event_view(
    "organized", "sim", str(sim_id), source_snapshot_id=request["source_snapshot_id"],
    expected_session_id=session_id,
)
```

上述代码只创建请求。后续游戏回调中先调用 `get_event_view_status`，只有 `state=ready` 才取首个 cursor，再由后续回调使用 `get_event_view_page` 和 next_cursor 逐页读取；failed 时展示 error 并关闭请求。用完两层后分别 `close_event_view`。可直接采用[逐回调示例](../sdk/examples/event_views.py)，其 `tick()` 每次仅轮询状态或处理一页，同步 SDK 异常由调用方捕获。消费者处理并释放页面，避免把数千条完整记录同时保存在 UI 内存。

签名：`query_event_view(view="recap", kind="sim", identifier="active", *, source="durable_session", source_snapshot_id=None, profile="recap_v1", page_size=20, expected_session_id)`。支持 Sim/Object 的实例 ID，recap 首版仅支持 Sim。明确的历史 ID 不要求实例仍在地块或 FIFO；active 在请求开始时解析。

状态包含 schema_version（2）、view_schema_version（event_views_v1）、session_id、request_id、source_snapshot_id、view、state。ready 提供 snapshot_id、total_matches、首个 cursor、build_ms；failed 提供 error.code/message，失败不会返回空数据成功。关闭 building 请求即取消构建，也可释放 ready/failed 请求。

页面包含相同身份、scope、coverage、offset、total_matches、cursor/next_cursor、items。recap 页的 recap 元数据含人物字典、阅读约定、质量提示和详情数量。其中旧离线 snapshot_id 仅用于对应离线产物；公共回查使用页面顶层 snapshot_id。

每页 1–100 项，默认 20；512 KiB 编码内容上限可使一页少于 page_size。按整个返回字典的紧凑 UTF-8 JSON 计费，包括元数据、快照身份、游标、items 数组和分隔符；预留足够的偏移数字空间后确定分页边界。单项连同返回字段无法容纳时明确报 view_budget，不截断字段。游标不可自行构造，与旧 history 游标不通用。

organized 的单元按阅读引用 r1、r2、… 的数值顺序排列，再按 e1、e2、… 排列独立来源；lineage 也使用证据引用的数值顺序。首次构建、缓存命中和同源文件导出使用相同顺序，不依赖 JSON 对象键的迭代顺序。

## 同源解释

```python
# page 是已成功读取的页面，并且包含至少一个条目。
explanation = client.explain_event_view(
    page["snapshot_id"], page["items"][0]["item_id"], facet="revisions",
    page_size=20, expected_session_id=session_id,
)
# 同样用 get_event_view_status / get_event_view_page / close_event_view。
```

| facet | 返回 |
| --- | --- |
| lineage | 来源 event_id、最新 revision、修订 sequence；组织项另含成员和范围外标记 |
| policy | 正文／详情／待核查去向，或独立来源的分类、省略依据 |
| labels | 原名称、解析状态、显示释义与来源 |
| revisions | 关联事件全部日志修订；records 辅助项返回该记录 |
| events | 关联来源完整最新事件，包括补入依赖 |
| units | 关联组织单元 |

活动解释包含明确挂接的结果、状态转移、决策。r54 等短引用可作当前 recap 快照的别名，持久主键用 item_id。条目不属于指定视图时报 item_not_in_view；需要先打开同源另一层，不可将另一层 ID 直接套用。

## 来源与预算

来源固定在当前 session 已落盘的 `durable_sequence/durable_byte_offset`；尚未落盘的通知不包含在内。`scope` 给出 `as_of_sequence/source_sha256/source_byte_offset`，`coverage` 给出截点时记录器和持久化状态。连续序号、修订链、会话和完整行均须通过校验，相同 sequence 的重试内容必须相同；后续追加不会改变旧页。

首版仅支持 `source="durable_session"`、完整会话时间范围和 `profile="recap_v1"`。其他来源、profile 或时间参数明确拒绝；没有已落盘记录时报 `source_unavailable`，无人物事件时报 `entity_not_recorded`。派生视图增量替换／删除协议和跨 session 查询尚未开放。

| 限制 | 默认值 | 配置 |
| --- | --- | --- |
| 同时打开的请求 | 8 个 | `event_view_query_limit` |
| 请求未访问过期 | 300 秒 | `event_view_ttl_seconds` |
| 构建时限 | 120 秒 | `event_view_build_seconds` |
| 每个查询 store 的估算内存 | 4 GiB，按需增长 | `event_view_memory_mb=4096` |
| 来源记录数／单行 | 100,000 条／4 MiB | 固定 |
| 每页 | 1–100 项、完整编码后最多 512 KiB | `page_size`；字节限制固定 |

来源日志没有独立的总字节上限；旧 `event_view_source_mb` 被忽略，metrics 不再返回 `source_budget_bytes`。来源字节、索引、缓存和构建预留仍计入内存预算；超限报错，不回退 FIFO 或截断结果。有效配置的显式值优先于默认值，下次启动加载。

同源 records/events 共用冻结原字节；organized/recap 按预算共用已解码事件或按需解码，内容、顺序和身份一致。缓存计入 `estimated_bytes`，其中 `decoded_cache_bytes` 单列解码缓存。新来源或预算紧张时可释放解码缓存，已发布快照仍有效。取页返回独立数据，调用方修改它不会改变来源；无需先关闭原始层才能打开派生层。

内存预算是受控数据结构的估算，不是游戏进程 RSS 的硬限制；查询 store 与每个导出任务分别计费，可以同时存在，游戏及其他采集缓存另计。后台与游戏共享 GIL 和垃圾回收，大日志冷构建可能影响调度。常规 UI 优先读 recap，跨层复用 `source_snapshot_id`，避免每个回调创建新截点；实测见[验证摘要](validation.md)。

普通旅行保留 provider，载入期间等待 ready，关闭请求仍可执行；新 session 关闭旧任务。取消在后台检查点生效，全部相关请求关闭或过期后回收来源。records/events 的快照不绑定显示规则，organized/recap 绑定核心和规则资源哈希；离线工具与 MOD 共用 Python 3.7 核心，规则资源随脚本包和 manifest 校验。
