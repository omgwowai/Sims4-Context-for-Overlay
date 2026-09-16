# Autonomy 决策采集（0.7.1）

2026-09-16。0.7.0 首局实机初始化失败，见[失败记录](validation/2026-09-16-autonomy-first-live.md)。**0.7.1 已修复并安装；新一局在游戏仍打开且暂停时，已读到 1,162 条决策及关联交互，全部具体行为选择阶段均取得原生 getter 返回值。** 候选五项限制、赢家保留和概率合计检查通过，见[暂停实机快照](validation/2026-09-16-autonomy-paused-live.md)；立即执行、独立目标层及完整会话诊断仍未验收。下文描述当前实现和预期边界；[确认方案](autonomy-capture-plan.md)、[原实现验证](validation/2026-09-16-autonomy-implementation.md)和[修复验证](validation/2026-09-16-autonomy-initialization-fix.md)记录设计、离线证据与安装状态。

## 记录范围

Autonomy 是事件记录模块的新增采集源，默认开启，沿用当前地块内已实例化实体的范围。普通交互以完整 `InteractionQueue.append` 返回成功为保留门槛：仅收到 `on_added_to_queue` 通知不够。成功入队后未开始就取消，决策仍保留；开始、取消、失败和结束沿用交互记录。

立即交互绕过队列，以原生 `GeneratorElementBase._run` 实际进入对应交互的 `_run_gen` 为等价门槛。只有开始通知、尚未执行的情况不保留决策；进入执行后返回 false 或抛出异常仍保留，并分别保存执行返回值或异常类型。

主行为与聊天等真实子行为选择均记录。子行为、已确认的技术动作及姿态来源归为 `internal`，查询时设置 `include_internal=True`。仅有延续／父子交互关系不生成新决策，也不凭“这个 Sim 的最近决策”补连。

## 三部分数据

| 部分 | 已记录 | 解释边界 |
| --- | --- | --- |
| 筛选 | GSI 行为评估行数、评分行数、阶段／原因模板的淘汰计数、对象检查状态计数；子行为提供者失败尝试数 | 只覆盖游戏本次生成的 GSI；未枚举或在 GSI 前失败的检查不补造。对象状态包括通过和失败，不能把全部状态数当作淘汰数。最多保留 128 种原因／状态，其余只保留数量。 |
| 评分 | 所保留候选的原始评分、最终选择权重、路径时间、多任务比例，以及可唯一关联的 `InteractionScore` 字段 | 可含需求贡献、关系／Buff 倍率、时间距离、机会成本和注意力明细。缺失项列入 `missing`；不重跑评分或相加成“原因占比”。 |
| 选择 | 实际池大小、总权重、原始索引、按权重排名、选中项、原概率、遗漏概率；按权重／均匀／确定性三种方式区分 | 概率以该层完整实际池为分母，保留明细不会重新归一化。多层的概率是条件概率，不能跨层比较权重。 |

每层默认最多 **5** 项：若赢家在前五，保留前五；若不在，保留最高四项和赢家。赢家仍保留原始排名及引擎顺序，并列权重以原顺序稳定排序。一轮中可能包含“子行为提供者 → 组别 → 具体行为”，或“行为类型 → 具体目标”，各层独立限制。

子行为提供者保留游戏 GSI 原评分说明；具体 mixer 保留调用中按 AOP 身份关联的基础权重，以及在同次评分调用内唯一匹配的 GSI 文本。文本中的内容分／组别按原文保存，尚未转为结构化分项；个别服务倍率未在 GSI 单独提供，明确列为缺失。主行为评分明细保留游戏自身的 GSI 过期警告，不用采集器另算总分替换游戏分数。

多任务门槛记录实际 getter 返回值、阈值、是否适用和 selector 返回结果。游戏组件把捕获原函数的转发方法挂到具体实例所属类型，因此在每次选择前检查实际 Sim 类型并按需接入；基础 Sim 类不必具有该方法。已接入或继承同一包装的方法不重复包装，也不额外调用 getter。每层 `multitasking.roll_source` 区分 `native_getter`、`native_gsi` 与 `unavailable`；`hook` 表示此次类型入口是否接入。仍不获取行为抽签的底层随机数。

若实际选择池不能观测，只保留有证据的评分输入，并把 `pool_count`／概率标为缺失。不能把这种情况解释为完整覆盖。

## 生命周期与关联

选择时暂存普通 JSON 数据，并绑定具体交互的弱引用与本次采集的唯一决策序号。缓存命中时使用原选择记录：`selection_time` 和 `commit_time` 分开；`cache_origin=true` 表示实际观测到缓存校验入口，false 只表示没有这项证据。一次请求对象可以产生多个独立选择序号。

正式事件使用 `event_type=game_event`、`category=autonomy.decision`：

| 字段 | 用法 |
| --- | --- |
| `payload.decision_id` | 与正式事件 ID 相同；不使用请求对象地址作为持久身份 |
| `payload.interaction_event_id` | 选中并提交的交互事件 ID |
| 交互的 `facts.decision_event_id` | 回指决策；入队回调已产生的交互会追加修订，后续修订保持关联 |
| `payload.context_source`／`is_script_request` | 原始来源；脚本调用算法也可能产生决策，不能一律说成自发意图 |
| `payload.retention_gate` | `queue_success` 或 `immediate_entered`，不等于完成／成功 |
| `payload.stages[]` | 每层独立身份、父层、选择时间、候选、概率和覆盖状态 |
| `payload.execution_result` | 可取得的提交返回／立即执行结果；后续交互结果仍看关联交互 |

决策的实体索引只包含执行者。落选者和落选对象留在候选证据中，不成为实际事件参与者。决策和交互用专用字段互相引用，不把“做出决策”伪装成交互产生的玩法效果。

## 配置与使用

以下字段可加入用户数据目录 `ContextOverlay/config.json`，省略时使用默认值。修改后通过 `co.restart` 开始独立运行，或下次进入游戏时生效。

```json
{
  "autonomy_enabled": true,
  "autonomy_top_n": 5,
  "autonomy_pending_capacity": 256,
  "autonomy_pending_memory_mb": 8,
  "autonomy_pending_ttl_seconds": 600
}
```

8 MiB 的估算预算均分给“等待提交的决策”和“上游选择／缓存评分证据”两个缓冲，各自最多 256 条。计费采用 JSON UTF-8 字节数的四倍加容器余量，是保守估算，不是 Python 堆大小保证。两者均使用弱引用、FIFO、600 秒现实时间过期；对象失效、范围退出、容量不足、过期和会话结束都有丢弃计数。临时数据不保存到独立日志。此预算不包括游戏自身的 GSI 数据生成／归档及当前正在执行的调用栈。

`co.status` 或 API `get_status()` 的 `autonomy` 包含入口状态、已提交／未选中计数、两个缓冲的数量和估算峰值、丢弃原因、最近采集错误和选择序列化用时。序列化用时不含 GSI 生成成本；实际总开销和单局数据量需要实机测量。采集错误隔离于游戏调用，不改变原异常、返回值或随机调用次数。停止时恢复自己启用的 GSI 开关；若其他消费者通过开关入口接管，则保留对方状态。

0.7.1 的 `coverage.multitasking_roll` 初始为 `pending_sim_type`，首次实际选择接入后为 `native_sim_type_method`；某次入口失败则标为 `partial`，并增加 `counts.multitasking_hook_unavailable`。此失败不会关闭其余 Autonomy 采集，后续选择仍会尝试接入新出现的方法。`partial` 保留本局出现过的缺口，不因后续成功而清除。

公共 API／SDK 保持 **1.1.0**，schema 保持 **1**，新增能力标识 `events.autonomy_decision`：

```python
from context_overlay import api

info = api.get_api_info()
if "events.autonomy_decision" in info["capabilities"]:
    packet = api.query_history(
        kind="sim", identifier="active", include_internal=True,
        event_types=["game_event"], fields=["autonomy.decision"],
        representation="both")
```

游戏内历史默认隐藏内部层；开启“含内部步骤”可看聊天等子行为决策。决策概览显示所选行为、门槛与层数，详情按层展示候选、原概率、评分组成和缺失项。API 可读结果的 `rendered.history[].decision_details` 提供相同详情。用 `co.history sim active 100 true` 可导出含内部层的近期历史。

旧运行日志没有新增决策证据，不能据此补出候选或评分。尚未确认的覆盖范围和实机检查清单见验证报告。
