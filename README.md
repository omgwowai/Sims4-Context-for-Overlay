# Sims4 Context for Overlay

给《模拟人生 4》的 Overlay 提供当前状态和事件历史。你可以读取 Sim 最近在做什么、查之前的事件，再把自己生成的旁白、总结或其他 JSON 内容写回同一份历史。

当前源码为 **0.10.2**，API / SDK 为 **2.2.0**，数据 schema 为 **2**。接入方式是游戏内 Python MOD；支持[同源四层事件查询](docs/event-views.md)，正常结束运行时也会[自动生成分层文件与人物阅读版](docs/run-output.md)，并附[事件去向对账与规则比较](docs/event-quality.md)。模型调用和 Overlay 界面由消费者负责。

已发布的团队试用包仍为 **0.9.0 / API 2.1.0**，不包含新的分层接口；当前源码构建和安装方法见[开发说明](docs/development.md)。

## 三个模块分别做什么

| 模块 | 核心功能 |
| --- | --- |
| 事件记录 | 记录交互阶段、已接入的离散状态变化和可观察到的自主决策；也接受 Overlay 写回自己的事件，统一查询 |
| Context 采集 | 按需读取 Sim / 物件的当前状态，选择需要的字段，附带最近历史；也能查询附近实体 |
| 数据语义化 | 用游戏文本资源和规则把状态、事件解释成中文，保留原始字段和来源，方便人和模型理解；这一层不调用大模型 |

例如，事件记录告诉你“刚才发生了什么”，Context 提供“现在是什么状态”，语义化让这些数据更容易读。游戏内的“查看状态与历史”窗口也使用这三项能力。

## 先跑起来

1. 从[团队飞书文档](https://omgwowai.feishu.cn/wiki/AAvPw03vJiR1NSkdDtmcxP04ng6)的附件获取 `ContextOverlay-0.9.0-Windows.zip`，完整解压，退出游戏，双击 `Install.cmd`。安装不用额外装 Python。
2. 在游戏设置里开启自定义内容和脚本模组，进入一个能操控 Sim 的地块。
3. 控制台输入 `co.api_test`，应看到 `passed: true`；再输入 `co.api_inspect`，看看写进去的记录。
4. 开始接入自己的 MOD：[从读一份 Context 到写回一条事件](docs/quickstart.md)。

团队可以直接从这个仓库取 [SDK 客户端](sdk/context_overlay_client.py)、[接入示例](sdk/README.md)和文档。接自己的 Overlay 只需要安装本体并复制 SDK；要修改本体，再按[开发说明](docs/development.md)构建。GitHub 的源码 ZIP 不能直接装进游戏。

安装细节、旅行测试和回退方式在[安装与使用](docs/install.md)。Windows 试用包也附带同版 SDK 和文档，可直接离线查看。

## 现在能用什么

| 你想做的事 | 入口 |
| --- | --- |
| 读 Sim / 物件的当前状态，加上最近几条历史 | `get_context` |
| 筛选某个人、某个来源或整个会话的历史 | `query_history`，SDK 可用 `history` 管理分页 |
| 写入自己的 JSON 事件，重试时避免重复 | `append_event` |
| 接着上次的位置读取新增或更新的事件 | `read_event_changes`，SDK 可用 `changes` 管理分页 |
| 查询原始修订、最新事件、组织层或短版回顾 | `query_event_view`，使用状态轮询、分页和同源解释 |
| 找附近的 Sim / 物件 | `get_nearby_entities` |
| 确认提供方是否安装、是否加载好 | `get_api_info` / `get_status` |

默认历史混合游戏和外部事件。只需要游戏记录时传 `origins=["game"]`；只看自己的记录时传 `producers=["team.my_overlay"]`，名称换成自己的。外部事件的内容由你自己定，没有必须照抄的旁白或总结格式。

普通旅行会保留历史、去重键和增量位置；读档、回主菜单、重启游戏或 `co.restart` 开始新会话。当前状态只读当前地块里的实体，历史也只包含我们实际观察到的内容。

## 接入时先记住这几件事

- API 在游戏线程调用。返回的普通字典可以交给后台；模型结果回来后，再回到游戏线程写事件。
- 外部事件只追加。想更新或撤回一段内容，用新事件表达；同一次提交重试时沿用同一个去重键和内容。
- 默认阅读可请求 `recap`，再按需回查组织项和原始修订。旧 `get_context/query_history` 仍返回事件历史，不自动替换成短版回顾。
- 历史有数量和内存上限；老事件可能从内存中淘汰。当前 API 不跨游戏重启读取磁盘旧历史。

读写、去重、外部记录窗口和一次跨地块历史读取已经实机跑通；新地块持续采集、复杂玩法和第三方 MOD 组合还需要大家边接边试。[已经测过什么](docs/validation.md)里有具体范围。

## 文档怎么找

| 目的 | 文档 |
| --- | --- |
| 第一次接入、出错处理、怎么反馈 | [快速接入](docs/quickstart.md) |
| SDK 文件放哪里、有哪些例子 | [SDK 说明](sdk/README.md) |
| 完整参数、返回值、错误码 | [API 参考](docs/public-api-v2.md) |
| 四层查询、证据回查与手动验收 | [分层接口验收](docs/event-views-validation.md) |
| 安装、游戏窗口、自检、回退 | [安装与使用](docs/install.md) |
| 理解采集范围、事件事实、Autonomy 与文本来源 | [架构与采集语义](docs/architecture.md) |
| 构建、离线报告、调试与分发 | [开发与调试](docs/development.md) |
| 已有验证结论及尚未覆盖的场景 | [验证摘要与已知限制](docs/validation.md) |
| 后续产品方向 | [未来方向](docs/future.md) |
