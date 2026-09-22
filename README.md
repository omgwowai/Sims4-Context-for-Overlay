# Sims4 Context for Overlay

ContextOverlay 是一个给《模拟人生 4》游戏内 Overlay 使用的 Python MOD：它记录可观察到的游戏事件，读取 Sim / 物件当前状态，接收下游 Overlay 写回的外部事件，并把结果整理成可查询的 JSON。模型调用和界面由下游 MOD 自己负责。

当前源码为 **ContextOverlay 0.10.10**，公共 API / SDK 为 **2.2.0**，数据 schema 为 **2**。正常结束运行时可自动生成分层文件和质量对账文件。

## 从哪里开始

- 使用项目或接入自己的 Overlay：先看[文档入口](docs/index.md)。
- 不确定 Context、历史 Events 或四层视图怎么选：看[读取指南与能力矩阵](docs/reading-guide.md)。
- 想用真实游戏结果理解四层：看 [Nova 的一局游戏](docs/event-layers-example.md)。
- 第一次接入：按[快速接入](docs/quickstart.md)完成“读状态 → 写事件 → 读回来”。
- 只使用 SDK：看 [SDK 说明](sdk/README.md) 和 sdk/examples/。
- 修改本体：看[开发与调试](docs/development.md)，构建后再按[安装与使用](docs/install.md)部署。

## 主要能力

| 能力 | 入口 |
| --- | --- |
| 读取当前 Sim / 物件状态和有限历史 | get_context |
| 查询当前会话历史、筛选来源并分页 | query_history，SDK 使用 history |
| 写入外部 JSON 事件并安全重试 | append_event |
| 读取新增或更新的事件 | read_event_changes，SDK 使用 changes |
| 查询原始修订、最新事件、组织层和短版回顾 | query_event_view 及相关状态/分页/解释方法 |
| 查询附近的 Sim / 物件 | get_nearby_entities |
| 检查版本、能力和运行状态 | get_api_info / get_status |

API 在游戏模拟线程调用；返回的普通字典可以交给后台模型或网络逻辑，结果回来后必须由下游 MOD 回到游戏线程，并重新确认 session、目标身份和请求是否仍然有效。历史是当前会话中实际观察到的内容，受到 FIFO、写盘和查询预算限制；没有记录不等于游戏中什么都没有发生。

## 从源码构建和安装

项目使用游戏兼容的 **CPython 3.7** 构建 .ts4script。在仓库根目录执行：

    & "C:\path\to\python37.exe" -B -X utf8 scripts/build.py --game "D:\Games\The Sims 4"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -UserData "D:\Documents\Electronic Arts\The Sims 4" -NonInteractive

如果源码或规则资源有变化，先重新构建；安装器会核对 dist/build-manifest.json 与当前 src/。游戏运行时不要安装，安装器不会自动关闭游戏。构建只更新 dist/ 中的本地产物，不会自动生成 ZIP；分发包仅在明确要求时制作。

## 项目目录

| 目录 | 内容 |
| --- | --- |
| src/ | 游戏 MOD 和可复用的事件处理核心 |
| sdk/ | 下游 MOD 使用的客户端、示例和合成数据 |
| scripts/ | 构建、安装、离线报告、资源审计和游戏验收工具 |
| tests/ | 不依赖游戏进程的回归测试 |
| docs/ | 使用、API、架构、验收和开发文档 |
| dist/、tmp/、.local/ | 忽略的构建、临时和本机验证/资源文件 |

已验证范围和明确限制见[验证摘要](docs/validation.md)。
