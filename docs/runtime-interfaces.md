# 首版接口与证据登记

日期：2026-09-14。当前代码使用以下 EA 接口，依据 `sims4-python` 提交 `12718ed96470fc2edffbc7875d10cf537b1f0e57`。此表说明实现来源；游戏验证状态单独记录。

0.2.0 新增的历史索引、过滤和分页仅完成离线验证。EA 采集入口沿用 0.1.0；新增命令仍需将来在游戏内验证。本轮未建立跨 MOD 的公开 SDK。

0.3.0 增加游戏内查看层，用户已确认可以打开；当前布局实现为 0.3.2。纯导航位于 `inspector.py`，EA 接入位于 `native_ui.py`。窗口直接调用 `Collector.collect(..., include_history=False, representation="raw")` 和 Recorder 的查询／分页接口，不经控制台或文件。UI 是三个核心模块的内部消费者，不是第四个采集模块或稳定公开 SDK。

内部试用提供[消费 MOD 示例](../examples/mod_consumer.py)和[开发接入说明](mod-integration.md)，展示现有 Collector／Recorder 的读取、分页和释放。示例固定 0.3.2，要求游戏线程和已就绪的运行；没有新增稳定 SDK 或网络服务。

## 原生查看窗口（0.3.2）

| 能力 | EA 来源 | 本版处理 |
| --- | --- | --- |
| 实体点击菜单 | [ScriptObject.potential_interactions](../../sims4-python/ea-source/EA/simulation/objects/script_object.py)、[Sim.potential_interactions](../../sims4-python/ea-source/EA/simulation/sims/sim.py) | 保留原生成器，补充一个 AOP；仅玩家普通点击、当前地块内实例，去重并避免转发对象的重复入口 |
| 查看交互 | [ImmediateSuperInteraction](../../sims4-python/ea-source/EA/simulation/interactions/base/immediate_interaction.py)、[create_tuningless_superinteraction](../../sims4-python/ea-source/EA/simulation/interactions/base/tuningless_interaction.py) | 无 tuning 资源的即时交互，稳定专用 ID；仅用户触发，不路由、不播放动作、不保存到存档。执行前重新检查目标范围 |
| 原生列表窗口 | [UiRecipePicker / GridPickerRow / PickerColumn](../../sims4-python/ea-source/EA/simulation/ui/ui_dialog_picker.py) | 一个 TEXT 列，名称与摘要横向显示，无图标列；关闭配料、技能、资金及食谱分类；单选，按结果 tag 分发导航 |
| 文本与响应 | [UiDialog](../../sims4-python/ea-source/EA/simulation/ui/ui_dialog.py)、[UiDialogService](../../sims4-python/ea-source/EA/simulation/ui/ui_dialog_service.py) | 动态本地化原始文本、关闭／确认分开、替换窗口忽略旧回调；EA 响应回调之后负责取消旧窗口，避免二次取消 |

窗口固定被点击的实体 ID，首次打开或显式刷新才读取当前状态；首页最多 5 条最近更新的主要事件。历史默认近 24 游戏小时，按首次观测时间倒序，每页 15 条；支持类型、时间、内部层筛选和事件详情。单个窗口只持有一个历史查询，返回概览、调整筛选、关闭或运行结束时释放；游标过期显示恢复操作。状态条目也分页，长说明分段。

查看交互用 `_context_overlay_tool` 标记，Runtime 的事件捕获与 EAAdapter 的当前交互快照明确排除；工具打开写 runtime.log。UI 初始化错误单独登记，不因 UI 失败暂停记录器。`inspector_enabled` 控制入口；`co.inspect` 提供备用打开命令。

0.3.1 将首页切换为 `UiDialogOkCancel` 的文字正文，近期事件直接逐行显示。0.3.2 用一个子类将默认 `responses` 置空，再显式传入全部按钮的 `sort_order`，避免 EA 默认关闭按钮插在自定义操作之间。首页从上到下为当前状态、历史事件、刷新、关闭；状态字段与事件的长详情也使用文字正文，按段导航。文本响应按 response ID 分发，取消／关闭与旧回调隔离继续生效。

0.3.2 的状态分类、字段列表、历史及筛选页统一使用 TEXT 列。当前原生客户端在此配置下不展示 `text`／`subtitle`，因此时间、筛选和页数摘要放入可见列标题，完整说明放入列标题的 tooltip。列表项合并名称与摘要，摘要已包含在名称中时不重复添加。实机观察与截图单独登记于[布局验证记录](validation/2026-09-14-inspector-layout.md)，不能将当前测试组合推广为所有分辨率或其他 MOD 的兼容结论。

默认 `record_need_changes=false`：`EAAdapter.continuous()` 省略需求读取与 `needs.*` 采样值，继续返回关系数值。`read_needs()` 与 Collector 的当前需求读取独立保留；需求读取不可用也不再阻断关系采样。Runtime 的配置和 session_start 日志记录该开关，改动不回写旧日志。

| 能力 | 入口与来源 | 本版处理 |
| --- | --- | --- |
| 启停 | [Zone](../../sims4-python/ea-source/EA/simulation/zone.py) 的 `on_loading_screen_animation_finished`、`on_teardown` | 加载后启用；原 teardown 前注销订阅并关闭当前运行 |
| 地块对象 | [ObjectManager](../../sims4-python/ea-source/EA/simulation/objects/object_manager.py) 的 `valid_objects()`，对象的 `is_on_active_lot()` | 仅当前地块可见实例；离开范围记截止 |
| 当前交互 | [Interaction](../../sims4-python/ea-source/EA/simulation/interactions/base/interaction.py)、Sim 的 `si_state` 和 `queue` | 实例 ID、tuning ID、阶段、目标与触发来源分别保存 |
| 交互通知 | [EventManagerService](../../sims4-python/ea-source/EA/simulation/event_testing/event_manager_service.py) 的 `register_single_event` | 订阅 `InteractionStart`、`InteractionExitedPipeline` |
| 排队与退出补充 | `Interaction.on_added_to_queue`、`_exited_pipeline` | 保留原返回和异常的 after hook；同事件的不同证据源保留引用 |
| 结果 | [InteractionFinisher](../../sims4-python/ea-source/EA/simulation/interactions/interaction_finisher.py) 的 `finishing_type` | `NATURAL` 与确认退出共同支持自然结束；取消、失败、重置和未知分开 |
| 触发来源 | [InteractionContext](../../sims4-python/ea-source/EA/simulation/interactions/context.py) 的 `source`、`source_interaction_id`、`continuation_id` | 保留原值，按已确认规则解释；不从时间相近推断主从或因果 |
| 需求 | [BaseStatisticTracker](../../sims4-python/ea-source/EA/simulation/statistics/base_statistic_tracker.py) 的 `get_statistic(add=False)` | 读取已实例化的 Hunger/Energy/Fun/Social/Hygiene/Bladder，缺失不使用默认值 |
| Buff | [BuffComponent](../../sims4-python/ea-source/EA/simulation/objects/components/buff_component.py) 的枚举与 `BuffBeganEvent`、`BuffEndedEvent` | 当前 Buff 与逐次增减分别记录 |
| 关系 | [RelationshipTracker](../../sims4-python/ea-source/EA/simulation/relationships/relationship_tracker.py) 的 `has_relationship`、`get_relationship_track(add=False)`、`get_all_bits` | 友谊/浪漫主轨道定期采样；关系位通过 Add/RemoveRelationshipBit 记录 |
| 物件状态 | [StateComponent](../../sims4-python/ea-source/EA/simulation/objects/components/state.py) 的 `values()`、`_trigger_on_state_changed` | 仅匹配下表中 ID 与名称均一致的 8 个状态类型；其他状态排除 |
| 名称 | [Definition](../../sims4-python/ea-source/EA/simulation/objects/definition.py) 引用的 `build_buy.get_object_catalog_name`，交互 `get_name()`、Buff `buff_name` | 用本地中文 STBL 导出解释 hash；复杂 token 未解析时保留原身份并标记 |

`InteractionComplete` 的发送条件是交互曾进入运行阶段，不保证自然完成。本版不把该通知当作成功依据。

即时交互（例如食谱选择器）只有开始通知时标为 `triggered/unknown`，不宣称持续运行或已完成。超级交互进入主要历史；原生 `AnimationInteraction`、非超级交互及姿态/携带取消衔接归入内部层。此层次是游戏结构的第一版映射，尚不聚合成“整次做饭”这一活动。

双向关系位以排序后的双方实例 ID、关系位 ID 和存在状态去重；原生对双方分别发送的通知引用同一个变化事件，重复通知另外保留为观测。单向关系位保留报告方身份。移除、重新新增及离场后的新通知不与此前变化合并。

## 物件状态目录

定义位于 `src/context_overlay/profiles.py`，版本 `common_object_states_v1`。状态类型和值共用 EA 的 `OBJECT_STATE` manager。启动时校验类型 ID 与名称；不匹配时当前查询明确返回 `unsupported`。

| ID | EA 名称 | 含义 |
| --- | --- | --- |
| 15075 | Broken | 损坏程度所处状态 |
| 15079 | BrokenState | 损坏状态 |
| 15128 | Dirty | 清洁程度所处状态 |
| 15129 | DirtyState | 清洁状态 |
| 15188 | Freshness | 新鲜度 |
| 15303 | Quality | 品质 |
| 15319 | Servings | 份量档位，不等同于精确剩余份数 |
| 28123 | Consuming | 食用状态 |

每个物件仅返回实际持有的状态。`Dirty` 与 `DirtyState`、`Broken` 与 `BrokenState` 是不同字段，不合并数值或含义。值的中文优先采用可解析 STBL；少量核验过的别名要求 ID/名称共同匹配，保留原名称与规则来源，其余明确标注名称未完整解析。

## 数据契约 v1

- 所有实体、tuning、定义与交互 ID 使用十进制字符串；类型与实例/定义身份分开。
- 日志带 `schema_version`、`module_version`、`session_id`、`sequence` 和现实时间。
- `event_revision` 保存稳定事件 ID、递增修订、参与实体及证据；`observation` 保存覆盖、样本与调试信息。
- 当前数据包带固定目标、读取前后游戏时间、所选字段、历史覆盖与可选 `rendered`。字段的 `status` 与 `value` 分开。
- 历史返回写入状态、条数限制、模块状态与覆盖说明。`target_observation` 保存该实体的首次/最近进场、最近离场、进场次数与当前观测状态；完整边界留在日志中。已接收记录不标为已确认写入。
- 连续状态差异带区间；不声称区间内只发生过一次变化，也不将差异自动归因为活动。
- 任一关系参与者离场都会清除该关系的采样基线；重新进场后的首个值不与场外空档之前的值计算差异。
- 查询和历史导出带 `provenance`：构建时游戏版本、所依据的 EA 源码提交、代码/中文词表摘要、实际运行解释器和可用资料片列表。构建时版本不冒充动态读取到的游戏版本，其他 MOD 清单由场景验证另行登记。
- 运行停止时分别尝试取消定时器、移除自有 Hook 和注销每项订阅；单项失败不阻止其余清理。退出边界等待后台写入收尾，最长 5 秒；失败明确报告。

## 历史索引与分页（0.2.0）

`HistoryIndex` 保存一份事件最新版本表；每个实体拥有去重的事件 ID 容器和按首次观测时间排序的引用。事件新修订不复制到参与者容器中；后续发现新参与者时补充关联。实体离场后历史关联保留。主线程发布事件版本，查询输出是独立副本。

旧 `Recorder.history` 使用实体索引，保持最近更新顺序。新增 `Recorder.query_history(entity_key, ...)` 支持 `page_size`、`include_internal`、`time_field`、`from_ticks`、`to_ticks`、`event_types`、`fields`、`outcomes`、`tuning_ids` 和 `order`。时间范围为 `[from, to)`；首次观测范围用二分定位，开始/结束查询筛选该实体全部候选，不用首次观测时间错误裁剪长交互。

查询快照保留固定事件版本及查询创建时的覆盖/写入状态，返回 `as_of_sequence`、`cursor`、`next_cursor`、`has_more`、`total_matches`、`created_at`、`expires_in_seconds`。`history_page(cursor)` 延续此快照，`close_query(cursor)` 主动释放。分页的 `has_more` 表达还有匹配结果，观测缺口仍由覆盖信息表达。时间相同时按首次接收序号排序。

`HistoryError.code` 区分 `invalid_query`、`invalid_cursor`、`cursor_expired`、`session_changed`、`session_closed`、`query_limit`、`query_budget`。查询拒绝不将记录器置为失败。记录本身的条数/估算内存或持久化预算耗尽仍暂停记录。

日志重放仍按序列检查混合运行、冲突重复和递增修订；序列推进改为线性检查，去重仅保留记录摘要，避免对大日志反复扫描序号或持有全部历史修订。`replay(..., include_observations=False)` 可仅返回最新事件集合。跨运行合并、磁盘查询索引、增量修订订阅和时间区间重叠筛选未在此次实现。

## 验证边界

首轮场景证据见[验证记录](validation/2026-09-14-first-round.md)。动态名称 token、未列入目录的状态、其他 DLC/MOD 组合及长时高负载不在本次结论范围内。原生通知只能代表实际发送并被观测到的事件；样本缺失不等于没有变化。
