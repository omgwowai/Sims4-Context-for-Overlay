# Sims4-Context-for-Overlay

为 The Sims 4 的 LLM Overlay 提供可组合的事件记录、当前 Context 采集与数据语义化能力。设计师可以选择数据来源、实体、字段和表示方式，结合自己的 Prompt 生成内容；原始事实、可读表达与叙事结果保持可区分、可追溯。

## 当前阶段

已完成项目初始化，以及三个参考项目的关键文档、采集源码和历史样本调研；已建立 Context 分类、查询与输出 MVP 设计，并实现一个带证据的离线历史查询/中文描述/JSON 导出原型。游戏内 Context MOD、鼠标菜单和实时状态输出尚未实现或部署。

**已确认总体方向：同一项目内的三个功能模块——事件记录、Context 采集器、数据语义化，最终以三者全部启用为目标。** 模块解耦用于调整、独立调试和阶段验收，可以整合为一个 MOD 交付。具体内部契约、打包方式和实现顺序随后细化。主入口见[三模块设计共识](docs/modular-context-provider.md)。

| 模块 | 输入与职责 | 输出与可选组合 |
| --- | --- | --- |
| 事件记录 | 从游戏已有事件/回调与必要观测中记录发生过的事实 | 历史记录、按 Sim/Object 关联查询；可作为 Context 的历史数据源 |
| Context 采集器 | 按需读取当前游戏状态，按设计师配置组合可用来源 | 结构化 Context；可选接入事件记录和语义化 |
| 数据语义化 | 使用规则解释约定格式的原始数据；支持运行时和离线输入 | 规范化含义与自然语言；可直接转换快照或历史记录 |

三个模块的数据结构和对接接口由本项目统一定义、同步迭代；允许共享数据类型、工具与规则资源。模块可单独开关，也可使用样例独立调试；不要求分别发布和安装。三个模块全开时，设计师仍可按请求筛选数据源和表示方式，不必每次输出全部历史或全部译文。

语义化保留可离线运行的转换核心。项目可以按阶段增加验证工具或小插件，例如事件查看、Context 导出预览、原始值与译文对照、游戏内查询入口。它们调用核心模块的接口，用于验证或验收具体能力，是否保留为正式工具随后决定。设计师配置、Prompt、模型调用和 Overlay 呈现属于下游消费/展示层；长期记忆及影响 Autonomy 留作后续扩展。

游戏本体已有 `EventManagerService/TestEvent` 等分发机制；事件记录模块补充所需的观测范围、统一持久化与查询，并非假定游戏没有任何事件通知。

三个新模块尚未实现；已有历史预览原型不是完整运行时服务。

## 已有研究：覆盖与完整性

首个研究问题：**通过游戏本体和 MOD，能否做到 Context 的“不重不漏”？**

当前结论：**不能承诺整个游戏的一切上下文绝对不重不漏；可以在固定版本、实体范围、字段/事件集合与采集时间窗口内，把事实唯一性和覆盖程度做成可验收目标。**

| 问题 | 首轮判断 |
| --- | --- |
| 定义能否不重？ | 可以检验 canonical ID 唯一；但不同源码身份可能对应同一运行时事实，需要别名与实体映射 |
| 事实能否不重？ | 可以设计规范事件和幂等消费；同一次交互的多个来源、参与者视角分别保留引用 |
| 状态能否不漏？ | 必须声明实体、字段、时点与精度，结合初始快照、变更监听和对账；原生、场外、未支持 MOD 的边界要单独列出 |
| 历史能否不漏？ | 只能验收采集生效后的指定事件集合；快照不能完整恢复中间变化、采集前历史或未持久化事件 |
| LLM 输入能否完整？ | 按具体玩法保留必要事实并允许回查；摘要、印象与推断和原始事实分开 |

本轮核验的具体依据：

- Atlas 的 **271,134 个静态身份没有重复**，但其中有 239,390 个函数参数与临时数据，不能当成同等数量的独立运行时事实。
- Experience 的三个历史数据集共 **8,951 条事件，重复 event_id 为 0，样本内悬空 parent 共 5 条**。这验证了部分数据性质，没有证明全量事件或语义完整性。
- 现有采集包含候选截取、广播冷却、字段采样和落盘前过滤；Observer 则明确声明了未实例化 Sim、场外决策与 Native 内部计算的边界。

完整论证、现有缺口与建议验收方式见 [Context 覆盖研究](docs/context-coverage-study.md)。

## Context 分类

当前从技术入口选择采集方案，入口是 [Context 的技术获取方式与接口分类](docs/context-acquisition-interfaces.md)：运行时查询、事件与变化订阅、业务 Hook、GSI 调试归档、tuning/资源、持久化状态、Python 执行跟踪、MOD 协作数据、Native/客户端补充观测。每类列出具体 API、可取得的数据和适用边界，并区分游戏内接口与文件/HTTP/SSE 等对外传输。

[运行时内容分类](docs/runtime-context-taxonomy.md) 作为另一条检索维度，按游戏含义分为场景、角色、状态、关系、活动、情境、资源、知识、事件、记忆、行动条件及 Overlay 状态等 12 类。同一类内容可能组合多种技术获取方式。

两份分类文档均为 v0.1 研究目录；既有接口草案与离线样例保留供后续细化参考。当前不冻结具体接口或 Prompt，游戏事实、角色已知信息、派生记忆及 Overlay 已生成内容分别表达。

## 已有研究：状态、经历回顾与生成事件

[从采集数据到状态、经历回顾与 LLM 事件](docs/state-history-and-generated-events.md) 进一步说明：Experience 主要通过 T03 Hook / T04 GSI 捕获运行事实，再用 T08 保存历史与派生印象。游戏内已有状态、MOD 捕获的事件、派生记忆和 Overlay 自有内容需要分别看待。

这些资料可以用于离线整理已覆盖时间段的经历，也可以组合实时状态查询，为 LLM 生成后续事件提供 Context。现有 Experience 还不能保证恢复任意历史时刻的完整 Sim/Object 状态；需要为所选字段补初始快照、实际状态变化、实体生命周期、统一时间及采集缺口记录。在线生成采用“提案 → 当前条件复核 → 执行 → 实际结果回录”的闭环。

## 事件事实与后续记忆扩展

同一次已确认的游戏交互拥有一个共享事件 ID，各采集源的观测、各个 Sim 的经历视角和后来形成的记忆分别关联它。EventView 表达亲历/感知/被告知及可知范围，Memory 表达保留的摘要或解释；模型推断与游戏实测状态分开。

普通 Object 使用关联事件时间线；Sim 对物件的主观印象属于该 Sim 的记忆。事件记录重新纳入总体规划，长期记忆和认知层仍属后续扩展。既有取舍与源码依据保留在[架构建议](docs/event-memory-architecture.md)，具体方案需与三模块的职责和可独立调试要求对齐后再实现。

## 已有离线原型

[离线原型](tools/preview_context.py)可以按 Sim 或物件实例返回最近最多 5 条交互记录，保留取消/未知结果、合并明确关联的参与视角，并输出 UTF-8 JSON。已生成 [Sim 样例](docs/examples/context-sim-preview.json)和[物件样例](docs/examples/context-object-preview.json)，此前 13 项边界检查通过。样例中的实时状态明确为不可用，不代表游戏内查询已经完成。

原型及运行命令保留在[历史查询 MVP 文档](docs/context-query-and-output-mvp.md)。它可作为将来规则翻译与历史兼容的样例，本轮没有改写采集代码。

## 参考项目

| 项目 | 本地位置 | 本项目关注的内容 |
| --- | --- | --- |
| sims4-python | `C:\sources\sims4-python` | 游戏源码与资源研究、运行时观测、可访问边界 |
| Sims4-Experience-Mod | `C:\sources\Sims4-Experience-Mod` | 经历事件采集、归因、物件印象与语义记忆 |
| Sims4-Context-Atlas | `C:\sources\Sims4-Context-Atlas` | Context 定义、读写关系、玩法与源码索引 |

本轮以本地资料为依据，没有拉取远端更新。版本、来源差异和具体证据位置见[参考资料基线](docs/reference-baseline.md)。历史样本结果与当前采集器实现分开描述。

## 本仓库的职责

- 定义面向游戏与 LLM 的 Context：实体、状态、事件、规则、参与者视角及派生记忆。
- 开发和管理有关采集 MOD、适配器、身份规则及版本兼容信息。
- 维护覆盖登记、证据索引和可复现的质量检查，让已采到的内容和未知边界都可以核验。
- 将可追溯的上下文提供给记忆、行为和叙事功能，记录模型推断与游戏事实之间的关系。

## 文档与工具

| 路径 | 内容 |
| --- | --- |
| [docs/modular-context-provider.md](docs/modular-context-provider.md) | 已确认的三模块设计共识：全开目标、内部接口、独立调试、阶段验证工具和设计师出口 |
| [Context for Overlay（飞书）](https://omgwowai.feishu.cn/wiki/AAvPw03vJiR1NSkdDtmcxP04ng6) | 已发布到《2. Project Overlay》目录的策划预告：三模块简介、上下文使用方式及结合现有设想的玩法示例；本地预告稿已删除 |
| [docs/semantic-catalog-and-context-api.md](docs/semantic-catalog-and-context-api.md) | 语义目录/规则翻译、字段目录/Profile 和展示闭环的阶段研究，供后续接口细化参考 |
| [docs/overlay-experience-flow.html](docs/overlay-experience-flow.html) / [配套说明](docs/overlay-experience-flow.md) | 游戏本体、Experience 与 Overlay 的现在 / 未来流程图；可离线查看，附牛排共同记忆示例、SVG 和 Mermaid |
| [docs/event-memory-architecture.md](docs/event-memory-architecture.md) | 事件记录与未来记忆层的参考：原始观测、共享事件、实体视角、迁移与验收 |
| [docs/context-query-and-output-mvp.md](docs/context-query-and-output-mvp.md) | 保留的历史查询方案与离线原型说明；新模块的接入契约尚未冻结 |
| [tools/preview_context.py](tools/preview_context.py) | 单一记录目录内的离线交互历史查询、中文描述与 JSON 文件输出 |
| [tests/test_preview_context.py](tests/test_preview_context.py) | 原型的视角去重、身份精度、实例隔离、结果/时间语义及文件读写边界检查 |
| [docs/examples/context-sim-preview.json](docs/examples/context-sim-preview.json) / [context-object-preview.json](docs/examples/context-object-preview.json) | 从真实历史样本导出的可回查 ContextPacket；不包含实时状态 |
| [docs/state-history-and-generated-events.md](docs/state-history-and-generated-events.md) | Experience 的技术归属、状态重建与经历回顾的边界、在线 LLM 事件生成建议 |
| [docs/context-acquisition-interfaces.md](docs/context-acquisition-interfaces.md) | 按获取方式划分的 9 类技术入口、具体 API、采集与传输边界及选型建议 |
| [docs/runtime-context-taxonomy.md](docs/runtime-context-taxonomy.md) | 面向 LLM Overlay 的 12 类运行时 Context、来源、边界与使用示例 |
| [docs/context-coverage-study.md](docs/context-coverage-study.md) | 首轮研究、四层完整性定义、采集建议与验收方案 |
| [docs/reference-baseline.md](docs/reference-baseline.md) | 本地版本、证据位置、文档与源码差异、复现步骤 |
| [docs/research/2026-09-11-reference-audit.json](docs/research/2026-09-11-reference-audit.json) | 静态身份与历史事件审计结果、输入摘要 |
| [tools/audit_reference_data.py](tools/audit_reference_data.py) | 只读、离线、仅标准库的参考数据检查工具 |

四个项目同处 `C:\sources` 时，在本仓库根目录运行：

```powershell
python tools/audit_reference_data.py --output docs/research/2026-09-11-reference-audit.json
```

工具不需要游戏环境，不执行参考仓库代码。它检查已提供数据的结构与引用，不测量全游戏漏采率。

## 下一步

三模块职责与项目组织方式已确认。下一步细化项目内部数据契约、首批采集范围和实现顺序，并为各阶段选择必要的验证工具或小插件。以三模块全部启用的输出链路作为主要集成验收路径，独立开关和离线样例用于调试与定位问题；具体打包方式尚未确定。本轮记录设计共识，尚未开始新模块的实现。

## 文档维护

- README 持续维护项目定位、当前结论和下一步工作。
- 详细研究记录放入 `docs/`，注明来源版本、证据位置、已知缺口与待验证假设。
- 区分“文档声明”“源码核验”“样本验证”“游戏实测”，不把计划能力写成已经实现。
- 每次调整上下文定义、采集范围或实现方案时，同步修改有关文档，保留重要结论的变更记录。

## 迭代记录

| 日期 | 变更 |
| --- | --- |
| 2026-09-11 | 初始化 Git 仓库；记录项目定位、参考资料与首个研究问题。 |
| 2026-09-11 | 完成首轮源码与历史样本核验；补充覆盖结论、证据基线、审计工具及下一阶段验收建议。 |
| 2026-09-11 | 新增面向 LLM Overlay 的运行时 Context 分类 v0.1，补充角色知识等源码入口和数据使用示例。 |
| 2026-09-11 | 新增按获取方式和接口划分的技术分类 v0.1，将其作为采集方案选型入口。 |
| 2026-09-11 | 补充 Experience 经历与状态重建的区别、游戏日回顾要求及 LLM 事件生成闭环，核对历史样本的实际时间范围。 |
| 2026-09-11 | 确定“当前状态 + 历史 → 同一份 ContextPacket”的 MVP；核验点击/悬浮及本地化入口，新增离线查询/文本/文件输出原型、两份真实样例与 13 项检查。 |
| 2026-09-11 | 提出在本项目建立独立事件核心的架构建议，以共享事件关联实体视角与记忆；调整 MVP 的采集来源和实施顺序，保留 Experience 历史格式兼容路径。 |
| 2026-09-11 | 新增游戏本体 / Experience / Overlay 分层图解与牛排共同记忆示例，明确解释层初版不回写 Autonomy，提供离线页面、SVG 和 Mermaid 文档。 |
| 2026-09-11 | 当前目标调整为规则语义化和面向设计师的 Context 服务两条主线；暂缓经历采集/重构，明确字段目录、Profile、只读快照及生成到 Overlay 的返回通道。 |
| 2026-09-11 | 项目命名调整为 Sims4-Context-for-Overlay；总体方向升级为三个可独立组合的模块，事件记录重新纳入规划，先评审边界再细化与实现。 |
| 2026-09-11 | 确认三模块职责及项目组织方式：统一定义内部接口，可整合为一个 MOD，最终以三个模块全部启用为目标；解耦服务于调整与调试，允许增加阶段验证工具或小插件。 |
| 2026-09-11 | 准备面向策划的《Context for Overlay》本地预告草稿，介绍计划能力、使用方式与玩法实验；当前会话没有可用浏览器，飞书目录资料核对与文档创建待完成。 |
| 2026-09-11 | 经飞书 CLI 读取 Project Overlay 及 authorship、微恐体验方案，补充预告中的三模块简介与参照样例，删除推进章节；已创建飞书文档并回读核对，按要求删除本地预告稿和上传临时文件。 |
