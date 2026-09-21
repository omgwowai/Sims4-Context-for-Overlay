# ContextOverlay Python SDK

SDK 版本 **2.2.0**，适配 API 2.x / schema 2。当前源码对应 ContextOverlay **0.10.3**，支持游戏内四层事件查询及结束时的分层文件；分层接口要求提供者声明 `event_views.query`。

把 [context_overlay_client.py](context_overlay_client.py) 放进自己 MOD 的包里，再把示例中的 `my_overlay_mod` 换成你的包名。SDK 不单独放进 Mods 文件夹，也不会帮你调模型或创建界面。[快速接入](../docs/quickstart.md)有完整的读写过程。

| 示例 | 用途 |
| --- | --- |
| [quickstart.py](examples/quickstart.py) | 先读当前 Sim、写一条事件、再查回来 |
| [overlay_events.py](examples/overlay_events.py) | 自由 JSON 写入、来源筛选、无实体查询、增量分页与重试 |
| [consumer.py](examples/consumer.py) | 接入、错误处理与跨窗口回调的历史句柄 |
| [event_history.py](examples/event_history.py) | 事件筛选与动作效果分组 |
| [event_views.py](examples/event_views.py) | 同源四层查询、后台状态和逐回调分页 |
| [nearby_entities.py](examples/nearby_entities.py) | 附近实体选择后读取 Context |
| [offline_preview.py](examples/offline_preview.py) | 普通 Python 读取合成 Context |

除 `offline_preview.py` 外，示例都由你的 MOD 在游戏线程调用，不会自动运行。`offline_preview.py` 可直接用 `python -X utf8 sdk/examples/offline_preview.py` 运行；[context-packet.json](examples/context-packet.json) 和 [nearby-packet.json](examples/nearby-packet.json) 是合成数据。

需要准确参数时查 [API 参考](../docs/public-api-v2.md)。游戏本体的安装、升级和自检看[安装说明](../docs/install.md)。

新接口的范围、预算和回查见[游戏内分层事件查询](../docs/event-views.md)，可执行的终端请求和 MOD 验收判据见[分层接口验收](../docs/event-views-validation.md)。
