# Context 采集模块

版本：v0.4。日期：2026-09-14。0.1.0 完成限定场景实机验证；0.2.0 新增可组合的历史筛选/分页，仅完成离线验证。接口见[运行说明](runtime-usage.md)，结果分别见[首轮记录](validation/2026-09-14-first-round.md)与[优化记录](validation/2026-09-14-history-optimization.md)。

0.3.0 增加直接消费 Collector 的游戏内窗口；0.3.2 将状态分类和字段列表改为横向文字行，长详情使用正文，见[布局验证记录](validation/2026-09-14-inspector-layout.md)。当前源码 0.6.0 试用版提供[公共 API v1 与 SDK](public-api-v1.md)，下游通过版本化入口读取 Context，无需获取 Collector 实例；本机已安装 0.6.0 并取得首局事件样本。API／SDK 1.1.0 新增[附近实体查询](nearby-entities.md)，尚未实机复测。原有字段范围不变，连续数值的定时历史采样已删除。

0.6.0 另增加常见生活事件、明确来源的数值效果、历史身份、动作／效果分组和 FIFO；当前 Context 字段与线程约束不变。完整范围见[覆盖说明](event-coverage-0.6.0.md)。

## 1. 职责

按请求确定目标、字段和范围，读取当前游戏状态，按需组合事件记录模块提供的历史，形成可导出且可追溯的 Context。总体边界见[三模块设计](modular-context-provider.md)。

Context 采集器负责能力目录、目标解析、读取时机、查询组合及结果状态。事件记录负责历史事实；语义化负责规范化表达与文本；慢速外部消费者通过输出边界接入。

## 2. 目标与字段能力目录

设计师按稳定的项目字段名选择数据；字段名映射到经过核验的 EA 读取入口，不暴露任意内部对象或可执行表达式。

每个支持字段登记名称、类型、单位、适用实体、读取来源、枚举范围、更新/缓存策略、数量限制、可能副作用、缺失含义和验证状态。候选内容见[内容分类](runtime-context-taxonomy.md)，接口选择见[技术目录](context-acquisition-interfaces.md)。

第一轮读取指定 Sim 的身份、名称、地块、游戏时间、当前交互及其关联物件身份，并纳入需求、Buff、关系和物件常用状态。每个具体支持字段登记源码依据并单独验收，不全面枚举隐藏统计量和内部标记。

目标范围为当前地块内已实例化的 Sim 和物件；场外目标返回明确的范围状态。关系读取以范围内的 Sim 为参与者，读取已有关系，不因查询创建关系。需求及关系数值只在 Context 请求时读取当前状态，不定时采样或写入数值历史；已有事件回调提供的状态变化逐次记录。Context 查询本身不产生历史事件。

目标绑定在请求开始时解析并固定；返回包记录实际实体，不能用后来改变的“当前选中 Sim”解释旧结果。SimInfo、已实例化 Sim、地块物件和库存物件的查找范围分别说明。

## 3. Profile 与请求

Profile 表达目标绑定、必需/可选字段、是否包含历史及历史范围、表示方式与读取限制。允许仅原始数据、仅可读表示或二者组合；首版 schema v1 的具体 JSON 字段见[接口登记](runtime-interfaces.md)。

必需字段必须得到可解释的结果。确认“当前没有交互”可以是有效的空结果；读取失败不满足该字段。玩法若要求非空，应另设触发条件。字段组展开为有界具体字段，不表示遍历全部 tracker。

Sim 可能同时运行多个交互，应明确运行/排队、主要/辅助交互和数量截断。若只选一个关联物件，需要明确采用哪个交互的目标。

首版提供当前状态查询、按需组合历史及导出；通过固定字段目录验证请求，物件状态 Profile 校验资源 ID/名称。游戏命令见[运行说明](runtime-usage.md)，本版没有 HTTP 端点或字段浏览 UI。

## 4. 快照、历史与可用性

| 情况 | 结果应表达 |
| --- | --- |
| 成功读取当前值 | 值、单位、来源、读取时点/区间及范围 |
| 确认没有目标状态或没有匹配记录 | 已成功查询的空结果，附适用范围 |
| 未实例化或已卸载 | 目标状态与可读取的字段范围，不伪装成不存在 |
| 字段不适用、未支持或读取失败 | 分别提供原因与受影响字段 |
| 事件记录停用或查询失败 | 历史能力不可用；当前状态可继续返回 |
| 历史覆盖不足或数量截断 | 覆盖区间、缺口、限制与尚有更多数据的情况 |
| 语义规则缺失 | 原始值仍可追溯，表达标为未映射或不完整 |

历史从事件记录接口获取，不从当前快照猜造。例如交互从列表消失，不能单独证明完成；当前 Buff/关系不能填充为昨天事件发生时的状态。

快照不是默认的全世界原子快照。读取跨多个 tick 时注明区间；当前事实和历史事实分别保留自己的时间与来源。未验证的跨 session/存档连续性不做自动合并。

## 5. EA 源码候选入口

| 数据 | 主要源码 |
| --- | --- |
| 服务、SimInfo 与实例 | [services](../../sims4-python/ea-source/EA/simulation/services/__init__.py)、[sim_info.py](../../sims4-python/ea-source/EA/simulation/sims/sim_info.py) |
| 统计量与 Buff | [base_statistic_tracker.py](../../sims4-python/ea-source/EA/simulation/statistics/base_statistic_tracker.py)、[buff_component.py](../../sims4-python/ea-source/EA/simulation/objects/components/buff_component.py) |
| 关系与游戏已有知识 | [relationship_tracker.py](../../sims4-python/ea-source/EA/simulation/relationships/relationship_tracker.py)、[sim_knowledge.py](../../sims4-python/ea-source/EA/simulation/relationships/sim_knowledge.py) |
| 物件与状态 | [script_object.py](../../sims4-python/ea-source/EA/simulation/objects/script_object.py)、[state.py](../../sims4-python/ea-source/EA/simulation/objects/components/state.py) |
| 游戏时间 | [date_and_time.py](../../sims4-python/ea-source/EA/simulation/date_and_time.py) |

找到入口只算源码定位。每个 adapter 还需确认目标版本、默认值、惰性初始化和副作用；读取中避免创建本来不存在的状态。运行验证以[开发基线](reference-baseline.md)中的本地游戏为准。

## 6. 数据包与输出

第一版数据包至少表达请求与 schema 版本、固定目标、来源/运行范围、当前快照、选取的历史、可选语义表示、证据引用、可用性、覆盖与截断。实体 ID 的序列化保持精确。

JSON 文件导出可作为首个调试出口，消费者只读取完整数据包。区分排队、写入成功和失败；写出成功不等于消费者已经使用。读取游戏对象留在允许的线程和生命周期，后台任务只处理普通数据。

外部模型调用、请求过期后的展示策略和 Overlay UI 属于后续消费集成，不影响本模块独立查询与验证。

## 7. 验收与扩展

验证目标绑定、同型号不同实例、空结果与错误、多个同时运行交互、字段限制、时间范围，以及关闭事件记录/语义化后的行为。完整场景见[首轮实现与验收](implementation-and-validation.md)。

首批字段、实例目标、schema v1、数量预算、游戏命令与输出目录已落实到[接口登记](runtime-interfaces.md)与[运行说明](runtime-usage.md)。后续按实际缺口扩充目录，并用更大规模场景制定性能预算。旧原型字段和输出样例保存在[归档](archive/README.md)，不作为新实现的成功证据。
