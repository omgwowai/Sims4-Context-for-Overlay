# ContextOverlay Python SDK

将 [context_overlay_client.py](context_overlay_client.py) 放入自己的 MOD 包，并按游戏 Python 3.7 构建。安装方法、参数、分页生命周期、线程与错误契约统一见[公共 API 与 SDK](../docs/public-api-v1.md)；采集边界见[架构](../docs/architecture.md)。

| 示例 | 用途 |
| --- | --- |
| [consumer.py](examples/consumer.py) | 接入、错误处理与跨窗口回调的历史句柄 |
| [event_history.py](examples/event_history.py) | 事件筛选与动作效果分组 |
| [nearby_entities.py](examples/nearby_entities.py) | 附近实体选择后读取 Context |
| [offline_preview.py](examples/offline_preview.py) | 普通 Python 读取合成 Context |

[context-packet.json](examples/context-packet.json) 和 [nearby-packet.json](examples/nearby-packet.json) 是展示用合成数据。
