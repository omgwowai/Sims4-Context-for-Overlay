# 0.5.0 公共 API 与 SDK：离线验证

日期：2026-09-15。公共 API **1.0.0**，SDK **1.0.0**，数据 schema **1**。实现与方案见[公共 API v1 文档](../public-api-v1.md)，机器可读信息见[验证数据](2026-09-15-public-api.json)。

本轮完成公共接口、SDK、示例、文档和构建；未安装、启动游戏、修改存档或更新飞书。0.5.0 包含上一轮 0.4.0 的语义解析改进，其实机验收仍待用户安排。已安装版本仍为 0.3.2。

## 1. 实现范围

`context_overlay.api` 对外公开版本／能力、运行状态、当前 Context、历史过滤分页与幂等关闭。Facade 只调用现有 Collector／Recorder，不要求下游访问运行时全局对象，也不经过控制台或文件。

Runtime 记录初始化线程和 api_ready 状态。公共调用在读游戏对象或查询表之前检查线程与会话；初始化未完成、关闭和旧会话不能被当成新运行。输出复制为可 JSON 序列化的数据，APIError 的 code/message/details 形成错误契约。

SDK 为可复制到下游命名空间的单文件客户端，兼容 API 1.x、schema 1，不固定准确 MOD 版本。Client 无初始化副作用，调用时处理缺少提供方／版本不符；HistoryQuery 管理分页和释放，允许放在 UI 状态中或使用 with。包内附有合成数据和不依赖游戏的预览示例。

## 2. 已执行验证

CPython 3.7.9 下 **104 项测试通过**，其中公共 API / SDK 契约检查 14 项。测试使用真实 Collector 和 Recorder，EA 状态读取由替身提供，未在游戏中运行。

| 关注点 | 已验证行为 |
| --- | --- |
| 导入与加载时机 | API 导入、SDK 构造和 get_api_info 不导入游戏服务或初始化运行 |
| 线程 | 后台线程的数据／状态调用返回 wrong_thread，适配器读取和查询表未被访问 |
| 运行状态 | 加载等待、starting、启动失败、关闭与就绪分别处理 |
| 参数和目标 | 大整数 ID 保持精度，拒绝 float/bool/非法字段等；active 解析后固定目标 |
| 复制与写入 | 修改返回的状态／事件不会影响源数据；API 查询不会新增日志记录或导出文件 |
| 不可用状态 | 记录器／语义化关闭、采集关闭、范围外目标、无活动 Sim 和记录失败保持明确状态 |
| 历史查询 | 时间与类型过滤、固定修订、重复读取游标、逐页中文引用 |
| 查询生命周期 | 过期、运行切换、预算拒绝、幂等关闭及渲染失败释放新查询 |
| SDK 生命周期 | with 内异常仍关闭，页面字典的修改不破坏内部游标，客户端重新获取替换后的 Runtime |
| SDK 兼容 | 缺少依赖、旧提供方没有 API、API 主版本不符及兼容小版本协商 |

SDK 及示例已用 Python 3.7 编译检查，合成 ContextPacket 的离线预览可运行。现有语义化、窗口、历史、存储和安装器检查一并通过。

## 3. 构建与产物

- `dist/ContextOverlay.ts4script`：0.5.0，build_info 包含 `public_api_version=1.0.0`。字节码魔数与本地游戏 `1.126.73.1030` 一致。
- MOD 包 SHA-256：`ea433ad11922cf5de89e50e19f287aa09143f28d2a57769f4b337b0d163560ef`。
- `dist/ContextOverlay-0.5.0-Windows.zip`：含提供方、安装入口、SDK、示例和当前文档的本地候选包。
- `dist/ContextOverlay-SDK-1.0.0.zip`：单独给下游开发者的源码包，包含清单与文件摘要，不包含 EA 游戏资源或运行时采集核心。
- `.validation/public-api-tests.txt`：完整测试输出；SDK 源码和 ZIP 摘要在验证 JSON 中。

构建／打包不会自动安装，也不会上传外部平台。SDK 源码不直接作为 MOD 安装；应放入下游自己的 Python 包。仅安装 SDK 不能替代提供方。

## 4. 复现

在仓库根目录运行：

```powershell
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 -m unittest discover -s tests -v
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 scripts/build.py --strings C:/sources/sims4-python/data/strings/CHS_CN.json
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 sdk/examples/offline_preview.py
python scripts/package_sdk.py
python scripts/package_trial.py
```

## 5. 尚未验证与首个集成场景

离线检查不证明实际游戏的 MOD 导入顺序、线程身份或第三方 UI 完全兼容。建议下一次以一个下游测试 MOD 验证：

1. 在加载存档前构造 Client 并读取能力；确认实际查询返回 not_ready。
2. 进入地块，在交互／alarm 回调请求一个 Sim 的当前状态与 15 条历史，核对 partial 和字段状态。
3. 打开筛选历史，翻页、关闭；同时打开本 MOD 原生窗口，确认共享查询预算和清理行为。
4. 旅行／读档后继续使用旧游标，确认拒绝；原 Client 再发新查询应取得新 session_id。
5. 后台只处理普通数据，故意调用一次运行接口应返回 wrong_thread；展示前检查请求与运行时效。

网络连接、模型调用、UI 展示和后台结果回到游戏线程的调度由消费 MOD 实现。此版本没有 HTTP、跨线程自动排队、事件推送或无限历史能力。
