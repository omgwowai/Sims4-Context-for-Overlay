# 0.6.0 事件采集：试用版覆盖与数据契约

日期：2026-09-15。状态：已实现，完成源码核验与离线检查；首局实机暴露的计时噪声、资源适配、角色表达与来源缺口已做一轮修正，新增行为待实机复测。MOD 继续使用 0.6.0；API 与 SDK 保持 1.0.0，schema 保持 1。

[实施约定](event-expansion-plan.md)记录本轮范围，[原始调研](event-coverage-audit.md)保存实施前基线。本页说明当前实现，不能把支持一个通用入口理解为完整覆盖所有玩法或资料片。源码参考为 sims4-python `12718ed96470fc2edffbc7875d10cf537b1f0e57`，本机游戏字节码为 `1.126.73.1030`；证据见[验证报告](validation/2026-09-15-event-expansion.md)。

## 已实现的入口

所有新增领域已完成源码核验与离线测试。首局实机已观察到直接数值效果、Buff／情绪、知识、特征、技能等级、制作、付款、库存插入／移除、广播与反应等记录；不代表其全部路径验证通过。职业、人生节点、Sentiment、库存拆堆／隐藏移动等仍无本局样本。详细数量、证据及历史噪声、类型／命名、角色和动作关联问题见[首次实机分析](validation/2026-09-15-event-expansion-first-live.md)。

安装后通过 `api.get_status()["event_coverage"]` 检查具体入口的 `installed/unavailable`、原因及动态子类数量；`installed` 仅表示订阅或 Hook 安装成功，不表示已出现或验证过该类事件。修正后的构建在 session_start／session_end 保存入口状态和 `event_diagnostics`（适配器回调数、计时通知屏蔽数）；旧首局日志没有这些字段，不能从缺少事件判定入口不可用。计数不是帧耗时，也不等同于逻辑事件数量。

本轮修正和逐入口依据见[事件质量修正验证](validation/2026-09-15-event-quality-fixes.md)。`statistic.direct` 在发布前排除 tuning 名中独立 `TimeSince…` 段的计时统计，不进入内存历史和 JSONL，只汇总通知屏蔽次数；不影响 Context。已确认的进食及动作捕捉游戏内部 active/passive 步骤按 ID 与 tuning 名共同匹配移入内部层。未知统计量、Buff 和具体社交动作继续保留。

| 领域 | 当前事件与实现 | 边界 |
| --- | --- | --- |
| 具体行为、社交 | `interaction`：排队、开始、退出、进场时已有交互；具体 mixer 默认主要层；AllSims 参与者、目标、角色、续接 ID、退出类型、`outcome_result` | 原生动画、姿态／携带取消等明确技术来源在内部层；其他未知用途先显示。自然退出与玩法成功分支分别表达，不合成整次聚会或做饭容器 |
| Buff、情绪、物件状态 | BuffComponent 增减、handle／原因／mood 详情；`buff.refreshed` 表示仍存在的 Buff handle 或详情发生变化；`mood.changed`；原有八类常用物件状态 | Buff 嵌套添加／移除不重复计数；加载恢复排除。Buff 用途未建立完备分类表，未知保留并标待解释；不按隐藏标记删除 |
| 直接数值效果 | `statistic.direct`：已加载 Loot 实现的同步执行范围内，读取 `_notify_change` 真实旧值与已 clamp 的 `_value` | 包括该路径中的需求／关系／其他统计量。没有明确操作、无实际变化、加载或操作抛异常不生成。没有全局数值流，没有持续活动边界快照，不把自然衰减或请求 delta 当净效果 |
| 技能、特征 | `skill.level`：Skill.set_value／_update_value 的实际离散等级变化；`trait.added/removed` 原生通知 | 等级变化包括下降；不保存经验值曲线。原生等级通知作为适配缺失时的后备，明确可能为初始化，不补造旧等级 |
| 关系 | 原有 `relationship.bits`；`relationship.spouse` 配偶状态；`relationship.knowledge` 已知特征／职业／偏好等实际变化；`relationship.sentiment` 成员变化 | 配偶双向通知去重，离婚后再婚为新事实；knowledge／sentiment 保留方向。Sentiment 不记录强度曲线。未知关系标记保留原始资源 |
| 职业 | `career.changed/promoted`、`career.work_started/work_completed`；补 Hook 记录 `career.demoted/retirement` | 通用职业加入／退出／解雇等保留原始 origin；不推断场外工作过程。工作通知的 money_made 是游戏结算值，真实到账另外由资金修改入口核验 |
| 人生、家庭 | `life.pregnancy/age/death/household` 实际状态变化；`life.offspring_created/adopted` 原生出生／收养；`life.milestone` 解锁 | 捕获调用前本地证据，死亡／入库存后仍保留身份。怀孕清除不推断流产。milestone 保留 NEW_SIM／LOD_UP 等上下文，不把补授时刻当原人生事件时刻 |
| 制作、收藏、成长 | `crafting.completed` 的产物、recipe、品质、masterwork；`collection.acquired`；`progress.unlocked/item_unlocked`；`aspiration.goal_completed/stage_completed` | 原生通知没有具体目标时通过同步调用上下文补身份，仍缺失则显式 `missing_fields`。无 Sim 的制作通知不虚构制作者；不把原生通知宣称为全部收藏／配方路径 |
| 库存 | `inventory.transfer`：插入、移除、拆堆、移入隐藏库存，记录容器、数量和产物 | 比较实际变化并验证返回；加载不生成，拆堆不当销毁。没有完整追踪建造模式家庭库存、所有销毁／出售／替换路径 |
| 反应、广播 | `reaction.started` 来自实际开始的 REACTION 交互；`broadcast.effect` 来自通过外层测试后执行的效果回调 | 同一广播对象／效果／受影响实体持续执行更新同一事实的修订和次数，移除后再次施加为新事实。每个已观察回调更新次数；回调正常返回不自动证明效果成功，不推断目睹或理解 |
| 事件金额 | `payment.completed`：FamilyFunds.add／try_remove_amount 的真实前后资金与 actual_amount，保留 reason、请求金额、参与者及可用来源 | 限定显式本地 Sim 或本地 Loot 操作，未归因家庭变动不写流水。与动作的关联只在有 resolver.interaction 时建立；不宣称全部交易已覆盖 |

Hook 包装保留游戏原返回值与异常；采集异常单独报告并暂停采集。生成器实现不套用同步 Hook。Loot 与广播仅遍历安装时已经加载的具体子类，运行中才加载的实现需以后补适配；这项限制在能力状态中可见。

## 事实、原因与查询

保留三个顶层类型：`interaction`、`state_change`、`game_event`。新增生活事实使用 `game_event`，`category` 与 `field` 同值，可继续使用现有 `fields` 筛选器。

这是用于说明结构的合成片段，不是实机记录：

```json
{
  "event_id": "run:fact:example",
  "revision": 1,
  "event_type": "game_event",
  "category": "statistic.direct",
  "field": "statistic.direct",
  "tier": "main",
  "entities": ["sim:1", "sim:2"],
  "participants": [
    {"kind": "sim", "id": "1", "key": "sim:1", "name": "示例甲"},
    {"kind": "sim", "id": "2", "key": "sim:2", "name": "示例乙"}
  ],
  "roles": [],
  "payload": {
    "statistic": {"id": "16650", "tuning_name": "fixture_relationship_track"},
    "before": 95,
    "after": 100,
    "scope_evidence": "local_at_call"
  },
  "cause": {
    "event_id": "run:interaction:1:30",
    "basis": "resolver.interaction",
    "operation_id": "example-operation"
  },
  "source": "ExampleLoot._apply_to_subject_and_target",
  "evidence_type": "scoped_actual_change",
  "first_observed_time": {"ticks": "1234"},
  "last_observed_time": {"ticks": "1234"}
}
```

`cause` 可能为 null，或只有 `operation_id/operation` 而没有交互 ID。只有同步调用、原生角色及 resolver 证据建立关联；没有相近时间配对。`roles` 按可核验入口填写，不要求每个原生通知都有完整角色。当前 Context 的范围检查独立于历史引用：已离开实例集合的实体，只要本运行仍保留相关事件，就可以按已知 ID 查询历史。

首局修正后，数值／Buff／广播区分 subject 与 initiator；库存区分 item、source_container、destination_container、split_product。中文主语取效果角色，索引仍保留全部相关实体。制作优先读取流程的 `_current_crafting_interaction`，付款补通用 Payment 与 CraftingProcess 上下文，库存补 InventoryTransfer、CarryElementHelper 和 CarrySystemInventoryTarget 的同步入口。携带回调只证明携带者时，保留 cause.actor 与 `carry_system_target._sim` 依据，不编造 event_id。未覆盖的异步或直接搬移路径仍可能缺少操作者／交互。

`mood.changed` 增加 old_intensity、new_intensity、change_kind；旧强度只有在同一 BuffComponent 更新调用内核实时填写。目标完成通知保留 aspiration_type，WHIM_SET／NOTIFICATION 和已识别的 utility 抱负资源放入内部层。`broadcast.effect.payload.broadcaster` 已修正为 `{instance_id, resource}`，它是广播服务实例，不再产生错误的 `object:<id>` 引用；实际广播源通过角色及实体引用记录。

```python
from context_overlay import api

# 全部独立事实，兼容旧调用；字段过滤使用新增 category。
packet = api.query_history("sim", str(sim_id), page_size=30,
    event_types=["game_event"], fields=["skill.level", "life.age"])
api.close_history(packet["history"]["cursor"], expected_session_id=packet["session_id"])

# 动作一条、关联效果进入 effects。SDK Client.history 同样支持此参数。
packet = api.query_history("sim", str(sim_id), page_size=15, group_effects=True)
api.close_history(packet["history"]["cursor"], expected_session_id=packet["session_id"])
```

`group_effects=False` 保持 API 默认语义。开启后只折叠**本次筛选结果里同时存在的动作和效果**；动作已被 FIFO 淘汰、被时间／类型筛选排除、尚未到达时，效果单独显示。效果仍可按类型／字段独立查询。`total_matches` 表示分组后的行数；查询引用和内存预算按底层全部事实计量，分页冻结成员、修订、效果和持久化状态。原生窗口的概览和未筛选主要历史默认分组，具体类型筛选显示独立事实。

## 200000 条 FIFO

容量按本次运行的逻辑事件计算，包含主要层、内部层与独立效果；修订不额外占一个事件名额。事件表之外有一个按**首次接收**排序的 FIFO 队列。

1. 新事件到来且数量满时，选取最早进入队列的事件。
2. 先检查字节预算并向写入队列提交新修订及 `evicted_event_ids`。提交失败保留旧事件，按原错误规则暂停。
3. 成功接收后移除旧事件及其全部实体／时间索引，清理不再使用的历史身份、关系缓存；加入新事件。后续修订不刷新 FIFO 年龄，迟到的旧交互回调不会让已淘汰交互复活。
4. 状态中的 `evicted_events`、`history_index.retention_policy`、`last_evicted_time` 明示保留范围；数量满不再是错误。

既有查询快照可以继续引用已淘汰事件的冻结版本，直到关闭或过期，受独立的快照预算约束。新查询只看当前保留事件。磁盘 JSONL 仍为追加证据日志，重放会应用 `evicted_event_ids`；FIFO 不截断旧文件。每运行的日志和导出仍共用 2 GiB 上限，磁盘预留 1 GiB，多运行文件需自行归档。

**数量上限不是内存承诺。** 正式默认仍为 1536 MiB 的记录估算预算。此次重型合成样本约 133515 条即触发保护；为验证完整 FIFO，测试进程临时使用 3072 MiB 预算，写入 210000 条后保持 200000 条，计量约 2.25 GiB，进程私有内存约 2.59 GiB。默认配置未被调高；具体事件构成、修订和附加缓存会改变开销。详见[测量条件与结果](validation/2026-09-15-event-expansion.md)。

数量 FIFO 正常运作；内存、写入队列、输出空间或采集失败仍显式暂停。查询过大只拒绝该查询，不能通过增加 page_size 解决快照总预算，应缩短时间或类型范围。

## 本轮不包含

持续数值采样及恢复开关、持续活动边界数值、Situation 生命周期／参与关系／目标／评分、完整家庭账本、场外后台追踪、任意历史时点状态重建、跨运行合并、资料片专属系统全集。原有行为或 Buff 可能留下这些玩法的旁证，不能据此宣称专用覆盖。

下一步先在实际使用中验证各新增入口、与其他 MOD 共存、事件量和 UI 分组，并按暴露的具体发送路径补充适配和语义用途分类。试用手册见[窗口检查](inspector-manual-test.md)。
