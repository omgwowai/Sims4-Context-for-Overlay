# ContextOverlay Python SDK 1.0.0

供下游 The Sims 4 脚本 MOD 调用 ContextOverlay **公共 API 1.x / 数据 schema 1**。最低提供方版本是 **ContextOverlay 0.5.0**。0.3.2 和 0.4.0 没有公共 API，不能直接配合这个 SDK 使用。

本版已完成离线契约测试和 Python 3.7 构建检查，尚未在游戏中进行跨 MOD 接入验收。安装的 0.3.2 和飞书附件没有自动升级。

## 放到自己的 MOD 中

将 `context_overlay_client.py` 复制到自己的命名空间，例如：

```text
my_overlay_mod/
  __init__.py
  vendor/
    __init__.py
    context_overlay_client.py
  context_consumer.py
```

两个 `__init__.py` 可为空。按自己的 Python 3.7 MOD 构建流程编译／打包。SDK 无第三方依赖，不是单独安装的 `.ts4script`，不需要 pip。**不要复制提供方的 `context_overlay/` 核心代码**，避免游戏里出现多个采集器；玩家另外安装一份 ContextOverlay MOD。

```python
from my_overlay_mod.vendor.context_overlay_client import Client, ContextOverlayError

client = Client()  # 可在导入阶段创建，不会加载游戏服务或启动采集。

def read_selected_sim(sim_id):
    # 从自己的游戏交互或 alarm 回调调用；该回调必须在游戏模拟线程。
    try:
        packet = client.get_context(
            "sim", str(sim_id), fields=["identity", "needs", "buffs", "interactions"],
            history_limit=15, representation="both")
    except ContextOverlayError as exc:
        return {"available": False, "error": exc.to_dict()}
    return {"available": True, "partial": packet["status"] == "partial", "packet": packet}
```

`available=True` 表示请求取得了数据，不代表每个字段都可用。检查 `packet.status`、`snapshot.<字段>.status`、`history.status` 与覆盖信息；空历史不能证明该实体没有发生过事件。

## 查询和释放历史

```python
with client.history("sim", str(sim_id), page_size=15,
                    event_types=["interaction"], from_ticks=day_start_ticks) as query:
    first = query.page               # 完整 history 数据包
    events = first["history"]["events"]
    if query.has_more:
        second = query.next_page()  # 到最后一页后再调用返回 None
# 离开 with 自动释放，无须等 120 秒过期。
```

游戏窗口分多次回调翻页时，把 `query = client.history(...)` 放在自己的窗口状态中，在“下一页”回调调用 `next_page()`，在关闭／刷新回调调用 `close()`。这些调用仍在游戏线程。关闭幂等；SDK 不在析构函数里自动调用游戏代码。发生错误时旧页和下一页游标保持不变；过期或切换运行后应关闭旧句柄并建立新查询。

直接调用 `client.query_history()` 时不生成管理句柄，消费方必须自行用返回的 `history.cursor` 和 `session_id` 调用 `client.close_history(...)`。接口签名、筛选与完整错误表见[公共 API v1 文档](../docs/public-api-v1.md)。

## 游戏线程与后台处理

1. 在游戏线程读取 Context，保留 `session_id`、`request_id`、目标 ID 和读取时间。
2. 将返回的普通字典交给自己的后台模型／网络逻辑，不传递游戏对象或查询句柄。
3. 通过自己 MOD 的游戏线程回调展示结果；再次检查 `client.get_status()` 的运行 ID。读档或旅行后丢弃旧结果；同一次运行也可能有多个过期请求，消费方自行用请求 ID／目标 ID 区分。

SDK 不自动排队跨线程请求，不调用模型，不发送网络请求，不创建窗口。它也不隐式重试失败请求。除 `get_api_info()` 外，有活动运行时所有公共调用都会检查游戏线程；后台调用返回 `wrong_thread`。

## 依赖与升级

`Client` 可以跨运行复用。每次调用重新取得公共提供方并核对 API 主版本及数据 schema，不保存 `_runtime`，不绑定准确 MOD 版本。`get_api_info()` 可在游戏未加载时检查依赖；`get_status()` 用于区分尚未就绪与各模块关闭／失败。

未安装返回 `dependency_missing`；已安装旧版但没有 API 返回 `incompatible_api`。公共 API 的 `APIError` 被统一转换为 `ContextOverlayError`，`code` 用于分支处理，`message` 用于诊断。不要捕获所有异常后返回“正常但没有数据”。

SDK 只承诺 API 1.x；新增可选字段可以被忽略，字段状态和枚举应有未知值回退。公共 API／数据 schema 的主版本变化需要消费 MOD 显式升级。SDK 不为 0.3.2 私有接口提供隐式回退。

## 游戏外开发

`examples/context-packet.json` 是明确标记的合成数据，可直接测试你的 Prompt 和展示层；`examples/offline_preview.py` 可以用普通 Python 运行，不依赖游戏。`Client(provider=测试对象)` 支持注入实现同一公共契约的替身；不要把离线替身的结果当作实机验收。

真实调用示例见 `examples/consumer.py`。完整方案、数据约定和错误行为见[公共 API v1 文档](../docs/public-api-v1.md)。
