# ContextOverlay 0.6.0 试用版：数据与下游 MOD 接入

ContextOverlay 在同一个脚本 MOD 中提供当前状态采集、事件记录和中文解释。提供 **公共 API 1.1.0 + Python SDK 1.1.0**，兼容 0.5.0 引入的 API 1.0 接口，下游可以直接取得 Context，无须使用控制台或先导出文件。API 与 SDK 已完成离线契约验证；真实跨 MOD 接入和名称解析仍待实机测试。

0.6.0 试用版补充生活事件、动作与效果分组、历史身份与 FIFO，删除全部连续数值定时采样。当前需求和关系值使用 get_context；严格来源约束下的直接数值效果使用 statistic.direct。支持入口和字段见[覆盖说明](event-coverage-0.6.0.md)，不能从历史推算任意时刻的完整状态。

飞书附件仍为 0.3.2，使用公共 API 必须先升级提供方到 0.5.0 或兼容的后续版本。本机已安装 0.6.0 并取得首局样本；本地更新不代表已公开发布，其他玩家仍需自行升级提供方。

## 1. 推荐：公共 API 与 SDK

新增[附近实体查询](nearby-entities.md)，通过 `client.get_nearby_entities` 取得中心 Sim 周围的 Sim／物件、距离与空间关系。调用前检查 `context.nearby_entities` 能力；用既有 `get_context` 读取所选实体状态。

将 `sdk/context_overlay_client.py` 放进自己的包，例如 `my_overlay_mod/vendor/`，再随自己的 Python 3.7 MOD 打包。玩家单独安装一份 ContextOverlay。不要把提供方核心代码复制进下游，也不要将 SDK 文件作为独立 MOD 安装。

```python
from my_overlay_mod.vendor.context_overlay_client import Client, ContextOverlayError

client = Client()

def capture_for_overlay(sim_id):
    # 从自己的游戏线程交互或 alarm 回调调用。
    try:
        packet = client.get_context(
            "sim", str(sim_id), fields=["identity", "needs", "buffs", "interactions"],
            history_limit=15, representation="both")
    except ContextOverlayError as exc:
        return {"available": False, "error": exc.to_dict()}
    return {"available": True, "partial": packet["status"] == "partial", "packet": packet}
```

不需要可选依赖包装时，也可直接 `from context_overlay import api` 并调用 `api.get_context(...)`。旧示例访问的 `game_runtime._runtime`、Collector 和 Recorder 均为内部实现，下游现在只使用公共入口。

- [公共 API v1 文档](public-api-v1.md)：完整方案、函数、数据、线程、分页、错误码和兼容策略。
- [SDK 使用说明](../sdk/README.md)：放入自己 MOD 的方式、管理查询和后台处理模式。
- [直接调用示例](../examples/mod_consumer.py)及 [SDK 消费示例](../sdk/examples/consumer.py)。

## 2. 历史过滤和分页

```python
with client.history("sim", str(sim_id), page_size=15,
                    event_types=["interaction"], from_ticks=start_ticks) as query:
    first_packet = query.page
    events = first_packet["history"]["events"]
    if query.has_more:
        next_packet = query.next_page()
```

时间使用游戏 ticks，范围 `[from, to)`。可按首次观测、开始或结束时间及事件类型、变化字段、交互结果、tuning ID 筛选。第一页冻结事件版本；刷新需新建查询。默认 TTL 为 120 秒现实时间，暂停游戏也计时。

窗口跨多次回调时持有查询句柄，关闭／刷新时调用 `close()`；调用仍在游戏线程。读档、旅行或重启后旧游标失效，SDK 会给出明确错误。不要一次性取完所有事件后再交给界面分页。默认最多 8 个查询及其他预算由整个 MOD 和所有消费者共享。

## 3. 数据约定

| 位置 | 含义 |
| --- | --- |
| `api_version/module_version/schema_version` | 公共接口、提供方版本和数据协议；文件导出未必有 api_version |
| `session_id/request_id/recorded_at` | 运行、请求身份和现实记录时间 |
| `target/scope/read_started/read_finished` | 固定实体、范围和当前状态读取区间；历史页面没有当前状态读取时间 |
| `snapshot.<字段>.status/value/source/reason` | 字段可用性、值和依据 |
| `history.events` | 关联事件 ID、修订、参与者、阶段、来源和可观测结果 |
| `history.coverage/target_observation` | 记录器／写盘状态及目标观测边界；target_observation 在 history 内 |
| `history.cursor/next_cursor/has_more` | 仅分页查询具有；SDK 管理句柄负责传回和释放 |
| `rendered.current/rendered.history` | 逐项／逐条中文及原始字段或事件引用 |
| `provenance` | 构建、EA 参考及词表等来源摘要 |

所有 ID 和输出 ticks 作为字符串处理，避免精度损失。字段 unavailable、不适用、未实例化和未观测不能当成数值 0；需求是游戏内部数值，不是百分比。complete 也不代表全部历史都被观察、写盘已完成或名称完整解析。

中文由游戏本地化资源、动态参数和事件规则生成，不需要大语言模型。name 可带 hash、localization、template、source、unresolved 等依据；名称可能是字符串或带状态的字典。消费方可以直接使用 rendered，不必重复实现翻译。离线重解释的 semantic_view 与原 snapshot/history 分开，见[语义化说明](semanticizer.md)。

## 4. 调用时机与业务分工

`Client()` 构造安全；`get_api_info()` 可在加载存档前检查依赖和兼容性。实际读取必须等地块就绪，在游戏模拟线程的回调执行。后台网络或模型回调直接调用会被拒绝为 wrong_thread。

读完后可以把普通字典交给自己的后台处理。展示结果时回到自己的游戏线程回调，检查 session_id、request_id 和目标仍然适用。SDK 可以跨运行复用，查询句柄只能属于创建它的运行。模型请求、Prompt、显示窗口和刷新策略由下游负责。

本版没有 HTTP 服务、跨线程自动排队或事件推送，也不会自动上传数据。调用是同步的，应按玩家操作或合理的低频回调读取所需字段，避免每帧请求完整 Context。

## 5. 文件路径继续可用

无需实时接入的外部工具仍可消费游戏用户目录中的文件：

| 文件 | 用途 |
| --- | --- |
| `ContextOverlay/runtime.log` | 加载、运行启停和错误 |
| `ContextOverlay/runs/<session_id>/journal.jsonl` | 追加观测与事件修订 |
| `ContextOverlay/runs/<session_id>/context-<request_id>.json` | 单次导出的完整数据包 |
| `ContextOverlay/config.json` | 可选配置 |

控制台 `co.export sim active 15 false both true identity,needs,buffs,interactions` 返回 request_id 和目标 path；等待 co.status 中请求为 written，再读取完整 JSON。queued 只是入队，不表示写盘完成。运行时 API 直接返回字典，不写这些导出文件。

journal 同一事件有多次 event_revision，离线按 session_id + event_id 归并最新合法修订，不能每行都算一个新事件。重启、读档、换地块或 co.restart 开启独立运行，不自动合并旧历史。更多命令见[运行说明](runtime-usage.md)。

## 6. 当前范围

当前 Context 只读取地块内实例；历史可保留本地事件中的已离开实体身份，不持续采集场外状态。200,000 条数量满时 FIFO 淘汰首次接收最早的事件，修订不刷新年龄；内存、快照、写入队列与磁盘预算独立生效。重型事件可能先触发内存保护。get_status 的 event_coverage 区分已安装入口与不可用入口，不能把已安装理解为已实机验证。

公共 API / SDK 的离线证据见[接口验证记录](validation/2026-09-15-public-api.md)。首次跨 MOD 验收建议检查加载前调用、正常 Context、分页、关闭窗口、旅行后旧句柄和后台线程误用。
