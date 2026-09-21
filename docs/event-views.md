# 游戏内分层事件查询

MOD 0.10.0 / API、SDK 2.2.0 新增 `event_views.query`、`event_views.explain`、`event_views.durable_session`。公共方法从游戏线程调用；后台任务只处理日志和普通数据，无需游戏外服务。原 `query_history`、`read_event_changes` 和 `get_context` 保持兼容。

实际调用与验收见[分层接口验收步骤](event-views-validation.md)，包含同源四层读取、证据回查和旅行检查。

## 四个视图

| view | items | 范围 |
| --- | --- | --- |
| `records` | `{item_id, record}`，record 是原日志 JSON | 人物最终关联事件的完整修订链、结构上关联的 observation、共享 session/zone 边界 |
| `events` | `{item_id, event}`，event 是完整最新修订 | 按实体索引选中的事件，包含内部事件，不应用内存 FIFO |
| `organized` | `{item_id, kind:"unit", lane, unit}` 或 `{item_id, kind:"standalone", event_id, revision, category, recap_disposition, evidence_ref}` | 全部来源具有单元成员关系或独立入口，补入依赖上下文 |
| `recap` | `{item_id, section, value}` | 默认阅读内容；section 为 activities/results/relationship_observations/states/review_actions |

组织层保留背景、技术细节、未知项、外部事件，并用引用压缩字段；完整候选评分、原始名称等从同源 events/revisions 回查。standalone 的 recap_disposition 表示默认阅读策略去向，不表示来源删除。实体索引相关不等于参与或知情。

Eddie 基线：records **3,843 项 = 3,806 条修订 + 37 条辅助记录**；events **1,707 项**；organized **1,166 项 = 469 个单元 + 697 个独立来源**；recap **118 项 = 116 个主内容项 + 2 个待核查动作**。组织器补入 20 个依赖事件，通过组织项的解释接口读取，不悄悄扩大 events/records 的人物索引范围。

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

每页 1–100 项，默认 20；512 KiB 编码内容上限可使一页少于 page_size。单项超限明确报 view_budget，不截断字段。游标不可自行构造，与旧 history 游标不通用。

0.10.6 起，organized 的单元按阅读引用 r1、r2、… 的数值顺序排列，再按 e1、e2、… 排列独立来源；lineage 也使用证据引用的数值顺序。首次构建、缓存命中和同源文件导出使用相同顺序，不依赖 JSON 对象键的迭代顺序。

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

- 固定当前 session 已持久化日志的 durable_sequence 与 durable_byte_offset；尚未写盘的通知不包含在内。scope 报告 as_of_sequence/source_sha256/source_byte_offset；coverage 附带截点时记录器与持久化状态。
- 严格核验连续序号、修订链、会话和完整行，相同 sequence 重试必须内容相同。后续追加不改变旧页；快照持有原字节，不用后来的来源替换旧修订。
- 首版仅支持 durable_session、完整会话时间范围、recap_v1。其他来源、profile、时间参数明确拒绝。未写入首条日志时报 source_unavailable；无人物事件时报 entity_not_recorded。
- 默认最多 8 请求、300 秒未访问过期，来源前缀最大 128 MiB / 100,000 条记录、单行最大 4 MiB、内存估算预算 4 GiB（`event_view_memory_mb=4096`）、构建时限 120 秒。config 字段：event_view_query_limit/event_view_ttl_seconds/event_view_source_mb/event_view_memory_mb/event_view_build_seconds。超预算明确失败，不回退 FIFO 或截断。
- 内存预算是受控数据结构的保守估算，不是进程 RSS 硬隔离。后台与游戏共享 GIL 和垃圾回收，大日志冷构建仍可能短时影响调度；需结合实机负载测量。常规 UI 优先请求 recap，跨层共用 source_snapshot_id，避免每个回调创建新截点。
- 0.10.6 的 records/events 页缓存引用同一来源已冻结的原字节，不再各自保留完整序列化副本；索引、描述符、派生缓存和构建预留仍计入预算。取页返回独立解码的数据，调用者修改页面或日志继续追加都不影响旧快照。页大小仍按完整编码后的内容检查。无需先关闭原始层才能打开派生层，但总来源／内存／请求限制仍然适用。
- 0.10.7 的 organized/recap 构建使用可重复遍历的事件 ID 序列，按需从同一原字节解码；全局排序保存键和 ID，避免整局决策候选池长驻。跨人物依赖、全局证据编号和完整来源校验仍参与处理。构建器只计算所需的筛选规则，不生成未使用的完整历史和字节指标；生成独立结果前释放全局构建索引。通过增加解码次数换取更低峰值内存，取消检查和时间限制继续生效。
- 4 GiB 是每个查询 store 或导出任务的受控缓存与保守构建估算上限，按需增长，不预分配，也不是游戏进程的 RSS 硬限制。查询与导出可以同时存在，预算不能理解为整个 MOD 合计最多 4 GiB。原始采集缓存、历史查询及游戏自身使用另计。来源 128 MiB、记录数、单页和磁盘输出限制独立存在；提高内存不等于无限会话。已有 config 显式设置值优先于默认值，修改配置需下次启动加载。
- 正常旅行保留 provider；载入期间读取等待 ready，关闭仍可执行。新 session 关闭旧任务。取消在后台检查点停止；全部相关请求关闭或过期后，后台回收来源。
- records/events 的快照不绑定显示规则，organized/recap 绑定核心和规则资源哈希。规则 JSON 随 ts4script 分发，源码 manifest 校验覆盖资源；离线工具和 MOD 共用 Python 3.7 核心。

没有模型调用，也不把游戏评分解释为人物心理。派生视图增量替换／删除协议和跨 session 查询尚未开放。
