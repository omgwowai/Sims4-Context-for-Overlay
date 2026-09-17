# Sims4 Context for Overlay

为《模拟人生 4》提供当前状态、已记录事件和可读文本，通过一个 `ContextOverlay.ts4script` 交付；下游 MOD 可直接使用公共 API 或 Python SDK。

当前源码版本 **0.7.1**，公共 API／SDK **1.1.0**，数据 schema **1**。安装结果以游戏用户目录中的 `ContextOverlay/install-receipt.json` 为准；源码版本号不能替代构建哈希核验。

## 从这里开始

| 目的 | 文档 |
| --- | --- |
| 安装、游戏窗口、升级与回退 | [安装与使用](docs/install.md) |
| 接入自己的 MOD，查询状态、附近实体和分页历史 | [公共 API 与 SDK](docs/public-api-v1.md)、[SDK 入门](sdk/README.md) |
| 理解采集范围、事件事实、Autonomy 与文本来源 | [架构与采集语义](docs/architecture.md) |
| 构建、离线报告、调试与分发 | [开发与调试](docs/development.md) |
| 已有验证结论及尚未覆盖的场景 | [验证摘要与已知限制](docs/validation.md) |
| 后续产品方向 | [未来方向](docs/future.md) |

## 能力与边界

- Context 按请求读取活动地块内已实例化 Sim／物件的身份、位置、交互、需求、Buff、关系和常用物件状态。
- 事件记录保存实际观察到的交互、离散变化、生活事件和已提交的 Autonomy 决策，支持按实体筛选、固定修订分页和有界 FIFO 历史。
- 语义化使用游戏资源与确定性规则生成中文，保留原始证据、缺失原因和静态参考边界。离线工具直接读 JSON／JSONL，默认输出 Markdown。
- 不持续采样需求或关系数值，不追踪场外完整生活，不从最终状态补造历史，也不执行 LLM 生成的玩法。

游戏内查询必须在模拟线程执行；返回值是独立的普通 JSON 数据，可交给下游后台处理。源码与合成样例不等于实机验收；历史验证范围见验证摘要。

## 开发约定

日常修改不生成 ZIP 分发包。完成相应检查后按 [AGENTS.md](AGENTS.md) 本地安装；游戏运行时不替换脚本包。旧研究、逐次验证报告和版本演变通过 Git 历史回查，当前文档只维护有效行为与限制。
