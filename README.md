# Sims4-Context-for-Overlay

为 The Sims 4 的 Overlay 提供可选择、可追溯的当前状态、历史事件与可读表达。

## 当前目标与状态

同一项目开发三个核心模块，以一个 MOD 交付、统一版本，默认三个模块全部启用；模块可单独关闭与调试。具体打包文件、内部 schema 和首批采集项在实现时逐步核定。

| 模块 | 职责 | 设计 |
| --- | --- | --- |
| 事件记录 | 观察并保存发生过的事实，按 Sim/Object 查询关联历史 | [事件记录模块](docs/event-recorder.md) |
| Context 采集器 | 按目标、字段和范围读取当前状态，按需加入历史 | [Context 采集模块](docs/context-collector.md) |
| 数据语义化 | 将约定格式的数据转换为规范含义和可读文本，保留原始证据 | [数据语义化模块](docs/semanticizer.md) |

当前处于实现准备阶段，三个核心模块尚未实现或游戏实测。旧离线工具、测试及临时目录已清除；旧输出不作为新实现的验收结果。下一步按[首轮实现与验收](docs/implementation-and-validation.md)验证一条贯通三个模块的流程。

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
3. [事件记录](docs/event-recorder.md)、[Context 采集](docs/context-collector.md)、[数据语义化](docs/semanticizer.md)：各模块设计及待定事项。
4. [技术获取方式与接口](docs/context-acquisition-interfaces.md)：按查询、通知、Hook、资源等入口选择方案。
5. [Context 内容分类](docs/runtime-context-taxonomy.md)：候选数据目录，不代表全部纳入首版或已支持。
6. [首轮实现与验收](docs/implementation-and-validation.md)：首个完整场景、开发顺序和验证要求。

## 历史资料与后续扩展

- [归档索引](docs/archive/README.md)：旧 Experience/Atlas 调研、原型方案、审计结果和静态输出。
- [后续扩展与产品愿景](docs/vision/README.md)：历史状态重建、记忆、事件生成和 Overlay 示例。
- [项目变更记录](docs/CHANGELOG.md)：阶段演变与本次整理记录。
- [Context for Overlay 策划预告（飞书）](https://omgwowai.feishu.cn/wiki/AAvPw03vJiR1NSkdDtmcxP04ng6)：此前发布的玩法说明；当前工程范围以本仓库开发文档为准。

## 文档维护

每项能力区分设计建议、已定位源码、已实现和已游戏验证。当前文档只描述当前开发方向；旧阶段方案放入归档，后续玩法放入愿景资料。调整字段、采集范围和接口时同步更新对应模块与验收要求，保留来源、版本、限制和未完成项。
