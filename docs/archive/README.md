# 历史研究与原型归档

归档日期：2026-09-14。当前开发以[总体设计](../modular-context-provider.md)、[开发基线](../reference-baseline.md)和[首轮实现与验收](../implementation-and-validation.md)为准。

## 1. 使用方式

本目录保留 2026-09 阶段研究及整理前的文档快照。原文中的“当前”“下一步”“已核验”和接口名称属于原日期、原版本及原场景，不能自动成为新实现的要求或验证结果。

归档保留研究正文，补充状态说明并修正移动后的相对链接和失效锚点。审计 JSON、预览 JSON 保留原文件内容。旧工具和测试已删除，归档不提供当前可运行的复现入口。

## 2. 文档与数据

| 历史资料 | 保留价值 | 当前对应入口 |
| --- | --- | --- |
| [参考基线](2026-09-research/reference-baseline.md) | 旧源码版本、Atlas/Experience 证据索引与审计方法 | [开发环境与参考基线](../reference-baseline.md) |
| [覆盖研究](2026-09-research/context-coverage-study.md) | 静态身份、旧事件样本及覆盖边界论证 | [首轮实现与验收](../implementation-and-validation.md) |
| [事件与记忆架构](2026-09-research/event-memory-architecture.md) | 旧模型问题、事实/视角/记忆分层与迁移研究 | [事件记录模块](../event-recorder.md) |
| [语义与 Context 接口研究](2026-09-research/semantic-catalog-and-context-api.md) | 旧两条主线、Profile、规则与下游返回通道的草案 | [Context 采集](../context-collector.md)、[数据语义化](../semanticizer.md) |
| [查询与输出原型](2026-09-research/context-query-and-output-mvp.md) | 旧格式最近交互查询、展示与文件出口的验证记录 | [首轮实现与验收](../implementation-and-validation.md) |
| [参考数据审计 JSON](2026-09-research/research/2026-09-11-reference-audit.json) | 2026-09-11 聚合计数、输入摘要与限制 | 仅作历史证据 |
| [Sim 预览](2026-09-research/examples/context-sim-preview.json)、[物件预览](2026-09-research/examples/context-object-preview.json) | 旧原型的静态输出与事实表达示例 | 不作为新 schema 或测试通过结果 |

## 3. 归档与当前路线的区别

旧 Experience 的导入、接口兼容或代码移植只有在具体需求成立时评估，不是首轮依赖。历史样本有其范围和缺口，不能代表新模块的事件覆盖或性能。

已提炼到当前设计的内容包括：有类型的实体身份、一次发生与多次观测的区别、当前状态与历史时间分离、原始事实与派生表达分离、显式缺失/截断、持久化确认和可追溯验收。

长期记忆、状态重建、LLM 事件执行及 Overlay 玩法另见[后续扩展资料](../vision/README.md)。
