# Sims4-Context-for-Overlay

为 The Sims 4 的 Overlay 提供可选择、可追溯的当前状态、历史事件与可读表达。

**内部试用 0.3.2：** 从[飞书成果与试用说明](https://omgwowai.feishu.cn/wiki/AAvPw03vJiR1NSkdDtmcxP04ng6)下载 Windows 安装包，完整解压后双击 `Install.cmd`，无需 Python。详见[安装说明](docs/install.md)；其他 MOD 的文件与运行时调用方式见[开发接入说明](docs/mod-integration.md)。源码 ZIP 不包含已编译脚本包。

## 当前目标与状态

同一项目开发三个核心模块，以一个 MOD 交付、统一版本，默认三个模块全部启用；模块可单独关闭与调试。首版交付 `ContextOverlay.ts4script`，采用 schema v1；字段与入口见[接口登记](docs/runtime-interfaces.md)。

| 模块 | 职责 | 设计 |
| --- | --- | --- |
| 事件记录 | 观察并保存发生过的事实，按 Sim/Object 查询关联历史 | [事件记录模块](docs/event-recorder.md) |
| Context 采集器 | 按目标、字段和范围读取当前状态，按需加入历史 | [Context 采集模块](docs/context-collector.md) |
| 数据语义化 | 将约定格式的数据转换为规范含义和可读文本，保留原始证据 | [数据语义化模块](docs/semanticizer.md) |

首轮开发验证已完成：三个模块在本地游戏中贯通，独立开关和离线语义转换均有验证证据。范围限定为当前地块内已实例化的 Sim 和物件，以做饭与吃饭为主要场景，采集行为、触发来源及需求/Buff/关系/物件常用状态，导出 JSON 和逐条中文解释。具体要求见[首轮实现与验收](docs/implementation-and-validation.md)，完成状态见[实际验证记录](docs/validation/2026-09-14-first-round.md)。旧原型不作为本次新实现的验收依据。

当前源码为 **0.5.0**，新增供下游 MOD 使用的 **公共 API 1.0.0 和 Python SDK 1.0.0**：直接读取 Context、按实体／时间／类型分页查询历史、管理游标、检查版本与可用性。下游无需访问 `_runtime`，无需控制台或文件中转。已完成离线契约验证，尚未安装或实机验收。见[公共 API 文档](docs/public-api-v1.md)、[SDK 入门](sdk/README.md)和[验证记录](docs/validation/2026-09-15-public-api.md)。

本版包含 0.4.0 的名称解析改进：游戏资源字段、中文 STBL 与动态参数联合解析，保存 LocalizedString 证据，支持旧日志重解释；名称功能仍待用户实机测试。方法见[语义化模块](docs/semanticizer.md)，旧数据核对见[语义解析记录](docs/validation/2026-09-15-semantic-resolution.md)。

已安装及内部试用包仍为 **0.3.2**。用户已确认界面布局：首页按钮为“当前状态 → 历史事件 → 刷新 → 关闭”，状态和历史使用横向文字行，长详情使用正文。默认关闭连续需求变化历史，当前需求快照及 200,000 条事件上限保留。使用方式见[窗口说明](docs/inspector-manual-test.md)，实机范围见[布局验证记录](docs/validation/2026-09-14-inspector-layout.md)。此前的[容量测量](docs/validation/2026-09-14-history-optimization.md)对应 0.2.0，首轮实机结论对应 0.1.0。

设计师配置、Prompt、模型调用和 Overlay 展示属于下游消费侧。长期记忆、任意历史时刻状态重建、LLM 事件执行及 Autonomy 改造放在后续扩展范围。

## 开发依据

| 来源 | 本地位置 | 用法 |
| --- | --- | --- |
| 游戏本体源码参考 | `C:/sources/sims4-python/ea-source/EA` | 主要依据：追踪游戏接口、调用路径、字段、事件发送条件和生命周期 |
| 本地游戏运行环境 | `D:/Games/The Sims 4` | 实际加载与行为验证；2026-09-14 核对程序版本为 `1.126.73.1030` |
| 静态分析参考 | `C:/sources/Sims4-Context-Atlas` | 辅助发现候选入口与关系，回到对应游戏源码核验 |
| 可选实现经验 | `C:/sources/Sims4-Experience-Mod` | 仅在具体问题需要时参考；新实现不预设依赖旧 MOD、旧结构或旧格式导入 |

`sims4-python` 中的 EA 反编译源码与 `My Script Mods` 下的自研 MOD 分开引用。源码版本、反编译状态、游戏用户数据目录及验证限制见[开发与参考基线](docs/reference-baseline.md)。

## 当前开发文档

建议按以下顺序阅读：

1. [三模块总体设计](docs/modular-context-provider.md)：职责、依赖、组合运行与共同边界。
2. [开发与参考基线](docs/reference-baseline.md)：源码依据、实际运行环境、来源优先级与核验状态。
3. [事件记录](docs/event-recorder.md)、[Context 采集](docs/context-collector.md)、[数据语义化](docs/semanticizer.md)：各模块职责、接口和扩展边界。
4. [技术获取方式与接口](docs/context-acquisition-interfaces.md)：按查询、通知、Hook、资源等入口选择方案。
5. [Context 内容分类](docs/runtime-context-taxonomy.md)：候选数据目录，不代表全部纳入首版或已支持。
6. [首轮实现与验收](docs/implementation-and-validation.md)：首个完整场景、开发顺序和验证要求。
7. [运行与调试](docs/runtime-usage.md)、[接口与证据登记](docs/runtime-interfaces.md)：首版构建、命令、数据契约及源码入口。
8. [游戏内窗口与手动试验](docs/inspector-manual-test.md)：菜单入口、浏览方式、试验步骤和错误定位。

## 历史资料与后续扩展

- [归档索引](docs/archive/README.md)：旧 Experience/Atlas 调研、原型方案、审计结果和静态输出。
- [后续扩展与产品愿景](docs/vision/README.md)：历史状态重建、记忆、事件生成和 Overlay 示例。
- [项目变更记录](docs/CHANGELOG.md)：阶段演变与本次整理记录。
- [Context for Overlay 内部试用（飞书）](https://omgwowai.feishu.cn/wiki/AAvPw03vJiR1NSkdDtmcxP04ng6)：安装包、界面说明、数据与 MOD 接入方式；正文底稿见[内部试用介绍](docs/internal-preview.md)。

## 文档维护

每项能力区分设计建议、已定位源码、已实现和已游戏验证。当前文档只描述当前开发方向；旧阶段方案放入归档，后续玩法放入愿景资料。调整字段、采集范围和接口时同步更新对应模块与验收要求，保留来源、版本、限制和未完成项。
