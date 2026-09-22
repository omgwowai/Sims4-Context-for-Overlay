# 接上自己的 Overlay

先跑通“读状态 → 写一条自己的记录 → 再读回来”，之后再接模型和界面。这一版给游戏内 Python MOD 用，不需要另外启动服务，也没有 HTTP 接口。

## 把 SDK 放进你的 MOD

先按[安装说明](install.md)装好 ContextOverlay。然后把 `sdk/context_overlay_client.py` 复制到自己的包里，例如：

```text
my_overlay_mod/
  __init__.py
  vendor/
    __init__.py
    context_overlay_client.py
  overlay.py
```

导入路径里的 `my_overlay_mod` 换成你的包名。各级包目录都要有 `__init__.py`，脚本按游戏的 Python 3.7 编译。只复制 SDK 文件；ContextOverlay 本体由玩家单独安装。

```python
from my_overlay_mod.vendor.context_overlay_client import Client, ContextOverlayError

client = Client()
```

创建 `Client` 不会马上访问游戏，放在模块顶层没问题。实际读取和写入放到你已有的交互、UI 或游戏 alarm 回调里。除了 `get_api_info()`，下面的 API 都在游戏线程调用；Python 自己创建的线程或 `threading.Timer` 不能代替游戏回调。

## 先读，再写，再读回来

可以直接参考 [quickstart.py](../sdk/examples/quickstart.py)，复制进自己的 MOD 后，从游戏线程调用 `round_trip()`。它会：

1. 确认 ContextOverlay 已加载、记录器可用。
2. 读取当前 Sim 的身份、时间、位置和最近 5 条游戏事件。
3. 写一条关联到这个 Sim 的外部事件，再按生产者读回来。

它只写 ContextOverlay 的事件日志，不改变 Sim 的行为。示例不会自动运行，每次手动调用会增加一条新记录。

实际接模型时，可以分成两个游戏线程回调：

```python
def capture_for_model():
    # 返回值是普通字典，可以交给你的后台模型任务。
    return client.get_context(
        "sim", "active", fields=["identity", "time", "location", "needs"],
        history_limit=10, origins=["game"])


def store_model_result(context, payload, submission_id):
    # 模型任务结束后，由你的 MOD 调回游戏线程执行。
    return client.append_event(
        "team.my_overlay", payload,
        entities=[context["target"]["key"]],
        idempotency_key=submission_id,
        expected_session_id=context["session_id"])
```

这里的 `payload` 可以是你的自由 JSON，比如 `{"text": "今天的回顾", "inputs": ["事件ID"]}`；字段名只是例子。选一个固定的 `producer` 名称，方便筛选。生产者名和去重键用不含空格的可打印 ASCII 字符，最多 128 个字符。一次 payload 最多 64 KiB，实体关联最多 32 个。

`submission_id` 由你的 Overlay 为每次结果生成，并保留到重试结束。同一个键加相同内容会返回原事件；内容变了就应换新键。`persistence="accepted"` 表示已接收、可读取，后台稍后写盘；`written` 才表示已确认落盘。详细的队列状态在 `get_status()["recorder"]["persistence"]`。

上面的 Context 显式选择游戏来源，是为了方便先接模型。API 本身默认混合全部来源。如果你会监听事件触发模型，记得安排好自己的触发条件，避免写入一条结果后又无休止地触发下一轮。

## 按需查历史

只看某个 Sim 的外部记录：

```python
with client.history("sim", sim_id, origins=["external"], page_size=15) as query:
    first_page = query.page["history"]["events"]
```

查自己的所有记录，包括没有关联实体的记录：

```python
with client.history(None, None, producers=["team.my_overlay"], page_size=15) as query:
    first_page = query.page["history"]["events"]
    # 需要更多时检查 query.has_more，再调用 query.next_page()。
```

`history()` 默认只查当前 Sim；明确传 `(None, None)` 才是全会话。窗口也只展示关联到所查看实体的记录，空窗口不等于写入失败。没有实体关联时，通过全会话 API 查询。

每页数据固定在查询开始的时刻，刷新要重新查询。读完关闭句柄，`with` 会自动处理；跨 UI 回调时，可以参考 [consumer.py](../sdk/examples/consumer.py) 的打开、翻页和关闭方式。

## 持续读取新事件

用 [overlay_events.py](../sdk/examples/overlay_events.py) 的 `IncrementalReader`。从你自己的游戏 alarm 或 UI 回调调用 `step(handle)`，每次最多处理一页：

```python
reader = IncrementalReader(session_id, origins=["game"], start="retained")
reader.step(handle_events)
```

`start="retained"` 先读保留的历史；`"now"` 从当前位置开始，只关注之后的变化。只有整轮分页处理成功才更新 checkpoint。`handle_events` 用 `(event_id, revision)` 识别重复内容，因为失败重试会重放已经处理过的部分。

同一个事件可能有多个修订，比如交互先开始、后结束。一次拉取只给你这段范围里的最新修订，不保证交付每个中间步骤。`event_id` 和 checkpoint 都原样保存和传回，不要拆开解析。

普通旅行会保留 session、checkpoint 和去重键。加载时等 `get_status()["ready"]` 恢复；恢复后比较 session，相同就续读，不同则为新会话创建 reader。历史页在 120 秒现实时间后过期，暂停和加载也计时；过期时调用示例的 `retry_batch()`，保留最后已提交的 checkpoint 重新读。

## 出错时怎么处理

SDK 抛出的 `ContextOverlayError` 有 `code` 和 `details`，`to_dict()` 可以直接记日志：

| 情况 | 先怎么处理 |
| --- | --- |
| `dependency_missing` / `incompatible_api` | 检查 MOD 是否安装、脚本是否启用，SDK 和提供方是否配套 |
| `not_ready` / `session_closed` | 等地块加载好，再确认 session；不要紧循环重试 |
| `session_changed` | 已经换会话了，丢弃旧请求引用，重新读 Context |
| `wrong_thread` | 把调用移回游戏线程 |
| `rate_limited` / `write_busy` | 查看 `details.retry_after_seconds` 和状态，稍后用同键、同内容重试 |
| `cursor_expired` | 重新查历史；增量 reader 用 `retry_batch()` 保留进度 |
| `history_gap` | 一部分旧事件已淘汰，明确选择 `reset("retained")` 重读保留范围或 `reset("now")` 从现在继续 |
| `idempotency_conflict` | 同一个提交键用了不同内容，检查自己的重试逻辑 |
| `recorder_disabled` / `recorder_failed` | 看 `co.status` 和 `runtime.log`；这不是“没有新事件” |

模型结果是否还适用，要由你的 Overlay 判断：核对原请求、Sim 和当前地点。旅行会保留 session，所以 session 相同不代表旧地点的建议仍然有用。

当前状态和历史里的 `partial`、缺失字段、未知结果也请保留。没有读到需求值不等于需求为 0，没有历史记录不等于什么都没发生。完整参数和错误码在 [API 参考](public-api-v2.md)。

## 帮我们试什么、怎么反馈

这轮先看三件事：能否顺利装上、自己的 MOD 能否读写、旅行或重试时会不会丢记录或重复。记录筛选和经历总结后面再做，现在先按人物、来源、时间和页数控制输入量。

有问题直接说“刚才做了什么、预期是什么、实际是什么”。方便的话附上游戏和 MOD 版本、错误码，以及相关的 `runtime.log` 片段。自检问题再带上 `ContextOverlay/api-self-test.json`；需要核对某条记录时给 session / event ID。日志里可能有角色名和你写入的内容，先挑相关部分即可，不用发整个存档。

构建目录的 `build-manifest.json` 记录源码、规则资源、版本和包哈希；游戏用户目录下的 `ContextOverlay/install-receipt.json` 记录实际装了哪个脚本。当前已测范围见[验证摘要](validation.md)。
