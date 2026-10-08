# Sims4 Context for Overlay

ContextOverlay 是一个给《模拟人生 4》游戏内 Overlay 使用的 Python MOD：它记录可观察到的游戏事件，读取 Sim / 物件当前状态，接收下游 Overlay 写回的外部事件，并把结果整理成可查询的 JSON。模型调用和界面由下游 MOD 自己负责。

当前发布版本为 **ContextOverlay 0.14.1 Preview / API、SDK 2.6.0 / schema 2**，开发和实测基线为 Windows、游戏 `1.126.73.1030`、简体中文。[下载 Windows 安装包或 SDK](https://github.com/omgwowai/Sims4-Context-for-Overlay/releases/tag/v0.14.1)，安装步骤见[安装与使用](docs/install.md)。

## 从哪里开始

| 目标 | 阅读入口 |
| --- | --- |
| 接入自己的 Overlay | [快速接入](docs/quickstart.md) → [SDK 示例](sdk/README.md) |
| 选择 Context、历史或分层视图 | [读取指南](docs/reading-guide.md) → [Nova 的四层实例](docs/event-layers-example.md) |
| 读取气泡和迁移旧接口 | [气泡事件与时间窗口](docs/balloons.md) |
| 查看一局的输出、判断是否完整 | [写盘与自动分层文件](docs/run-output.md) |
| 了解当前验证结果和限制 | [验证摘要](docs/validation.md) |
| 修改本体、构建和调试 | [开发与调试](docs/development.md) |

全部文档按用途列在[文档入口](docs/index.md)。

## 主要能力

| 能力 | 入口 |
| --- | --- |
| 读取当前 Sim / 物件状态和有限历史 | get_context |
| 按游戏时间窗口查询气泡发送事件 | `get_context` 的 `balloons`；历史和落盘 Events 的 `balloon.sent` 筛选 |
| 查询当前会话历史、筛选来源并分页 | query_history，SDK 使用 history |
| 写入外部 JSON 事件并安全重试 | append_event |
| 读取新增或更新的事件 | read_event_changes，SDK 使用 changes |
| 查询原始修订、最新事件、组织层和短版回顾 | query_event_view 及相关状态/分页/解释方法 |
| 查询附近的 Sim / 物件 | get_nearby_entities |
| 查询当前相机近似视锥内的全部实体摘要 | [get_camera_view](docs/camera-view.md) |
| 检查版本、能力和运行状态 | get_api_info / get_status |

Context、Inspector 和原生事件采集覆盖当前已加载区域内的有效世界实例，包含门外、人行道和公共空间；隐藏、库存及未加载区域的实体不在当前状态范围内。气泡作为事件保存，Context 默认查询最近 5 个游戏分钟；发送记录不证明屏幕显示，也不用于推断人物想法。

API 在游戏模拟线程调用，只有 `get_api_info()` 可独立于该线程读取。返回的普通字典可以交给后台模型或网络逻辑；结果回来后，由下游 MOD 回到游戏线程并重新确认 session、目标和时效。内存历史受 FIFO 限制，落盘 Events 可核对持久化截点；正常结束后自动生成分层文件与质量对账。没有记录不等于游戏中没有发生。

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

0.14.1 发布前通过 441 项离线回归；最新 0.14.0 实机会话的 104 条气泡、4,943 条记录和 27 个导出内容文件核对通过。最新一局未点击 Inspector，不能作为窗口显示验收。证据版本、来源和其他限制见[验证摘要](docs/validation.md)。
