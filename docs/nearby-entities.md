# 附近实体查询：API／SDK 1.1.0

日期：2026-09-15。MOD 保持 0.6.0，schema 保持 1。新增能力为 `context.nearby_entities`，原 Context 与历史接口保持兼容。实现已通过离线回归，原生房间返回值及跨 MOD 调用仍待实机复测；验证见[记录](validation/2026-09-15-nearby-entities.md)。

## 用途与最小调用

查询以指定 Sim 为中心的当前空间邻近实体，返回按距离排序的 Sim／物件身份和空间信息。用已有 `get_context` 按需读取选中实体的交互、需求、Buff 或物件状态，不默认展开所有邻居的 Context。游戏对象读取必须在模拟线程；返回值为独立 JSON 数据，可交给后台处理。

```python
from my_mod.vendor.context_overlay_client import Client

client = Client()
nearby = client.get_nearby_entities(
    "active", kinds=["sim", "object"], radius=8.0,
    same_level=True, limit=32)

# 在自己的 UI 中选择一个结果后，沿用其身份和 session。
row = nearby["results"][0] if nearby["results"] else None
if row is not None:
    target = row["entity"]
    fields = (["identity", "location", "interactions", "needs", "buffs"]
              if target["kind"] == "sim" else ["identity", "location", "object_states"])
    context = client.get_context(
        target["kind"], target["id"], fields=fields, include_history=False,
        expected_session_id=nearby["session_id"])
```

调用前检查能力；旧版提供方仍可处理原有 SDK 方法，调用新增方法则返回 `capability_unavailable`。只检查 MOD 版本号 0.6.0 不足以区分同版本的不同构建。

```python
supports_nearby = "context.nearby_entities" in client.get_api_info()["capabilities"]
```

直接调用 `context_overlay.api.get_nearby_entities` 与 SDK 的参数一致。[完整示例](../sdk/examples/nearby_entities.py)和[合成数据](../sdk/examples/nearby-packet.json)可供下游开发；合成数据不是游戏证据。

## 参数

```python
get_nearby_entities(identifier="active", *, kinds=("sim",), radius=None,
                    metric="horizontal", same_level=True, same_room=False,
                    include_self=False, limit=32, expected_session_id=None)
```

| 参数 | 约定 |
| --- | --- |
| identifier | 中心 Sim，支持 `active` 或正的 64 位 Sim ID；不接收游戏对象。active 只解析一次 |
| kinds | 非空、不重复的 list／tuple，可选 `sim`、`object`；默认只查 Sim。Sim 按游戏 `is_sim` 分类，可能包含宠物等非人类 Sim |
| radius | 0–1,000,000 的有限数值，单位 `game_world_units`；边界包含。None 只允许与 same_room=True 同用 |
| metric | `horizontal`（默认，x/z 平面）或 `euclidean`（三维直线距离），同时决定半径筛选和排序 |
| same_level | 默认 True，要求游戏 level 相等；不使用高度差或路由表面相等代替楼层 |
| same_room | 默认 False；True 时增加同房间约束。与半径共同指定时取交集 |
| include_self | 默认 False；True 时中心 Sim 也须满足 kinds 和空间筛选条件 |
| limit | 整数 1–64，默认 32；不是候选扫描上限，不创建分页游标 |
| expected_session_id | 可选会话约束；读档／旅行／重启后拒绝旧 session |

同房间、不限定半径：

```python
client.get_nearby_entities("active", same_room=True, radius=None)
```

跨楼层按三维距离查询：

```python
client.get_nearby_entities("active", radius=12, metric="euclidean", same_level=False)
```

`get_api_info().nearby` 公布类型、距离指标、单位、最大返回数、候选扫描预算、最大半径及房间筛选能力。

## 返回结构与完整性

```text
kind = "nearby_entities"
api_version / module_version / schema_version
session_id / request_id / recorded_at / provenance
target                  固定的中心 Sim 身份
scope                   active_lot_instantiated，场外排除
query                   实际执行参数、单位、distance_basis、排序规则
read_started / read_finished
origin                  中心的 position、level、routing_surface、room
results[]
  entity                kind / id / key / name；物件有 definition_id
  identity_status       身份名称读取失败时保留 ID，状态为 error
  distance              horizontal / vertical / euclidean
  spatial               position / level / routing_surface / room
  relative              same_lot / same_level / same_room / same_routing_surface
count / matched_count / matched_count_exact / truncated
coverage / status
```

空间字段与相对关系使用 `{status, value, source, reason?}`。ID 和时钟 ticks 为字符串，楼层和距离为数值。vertical 是非负高度差。距离未四舍五入后再筛选，不承诺等于米、可行走路程或到家具外轮廓的距离。

位置使用实体的世界坐标点。routing_surface 保留 primary_id、secondary_id 和 type；与 level 分开。room.value 为 `{zone_id, id}`；同房间比较 zone、游戏房间 ID 和 level。房间名称／用途不在此接口范围内。

排序优先使用所选 metric，距离相同按 kind、数值 ID 排序。ID 不转换成浮点数。在扫描完整时，先考察全部候选再保留最近 limit 个；不会直接截取管理器的前 limit 个实体。

| 标记 | 含义 |
| --- | --- |
| count | 实际返回的结果数 |
| matched_count | 已核实匹配的数量，包含因 limit 未返回的条目 |
| matched_count_exact | 查询是否完成所有相关候选的判定；False 时 matched_count 仅为已知数量 |
| truncated | 已知匹配数超过 limit，结果经过数量截断；不代表有可用游标 |
| coverage.complete | 枚举完整，且没有候选因为读数／必需筛选字段不可用而无法判定 |
| coverage.enumeration_complete | 是否完成候选枚举；扫描预算耗尽或枚举器异常时为 False |
| coverage.scanned_count / candidate_count | 遍历到的管理器对象数／通过类型与范围筛选并去重后的候选数 |
| coverage.unresolved_count / reasons | 无法判定的候选数量，以及有限的原因计数 |
| status | `complete` 或 `partial`；可选空间信息不可用也会令整个包为 partial |

`truncated=False` 不保证查询完整，还要检查 coverage。`coverage.complete=True` 表示当前限定范围内的查询完整，不表示场外、库存或隐藏实体也被查询。

可出现 `status=partial` 且 `coverage.complete=True`：例如已确认所有半径和楼层条件，但室外房间信息未知。可选房间信息不足不改变已经核实的几何邻近结果。

中心必需空间信息不可用返回错误，不输出“正常但没有邻居”。个别候选不可判定时跳过该候选，返回已确认结果并标记 coverage 缺口。覆盖不完整时，最近 N 个仅指已成功判定的候选。

## 房间、库存与采集范围

使用当前对象管理器的非隐藏实例，复用当前地块范围检查。Sim 必须有活动实例；场外 Sim、未实例化 Sim 排除。非 Sim 对象包括可枚举的家具、食物和装饰等，不保证存在玩家可点击的交互。墙体、地板和纯客户端视觉元素不保证作为独立 GameObject 枚举。

另外检查实体及父对象是否处于库存；背包、冰箱等容器里的内容不算摆放在附近。桌面插槽物件、携带物件若仍是范围内非隐藏世界实例，可以按其世界坐标进入结果。父链异常会形成缺口。

EA Python 中存在 `build_buy.get_room_id` 原生别名，并在房间物件筛选中使用它。正的整数返回作为游戏房间标识；None、0、负值及异常保留不可用状态，整数哨兵值放在 raw_id。其室外／特殊场景含义尚未实机验证，不能把两个无效结果相等解释为同房间。正房间 ID 也不代表具有人类语义的“厨房”等房间用途。

默认只为中心和最终返回的实体查询房间；启用 same_room 时，对已通过半径／楼层筛选的候选查询房间。中心房间无可靠结果则抛 `spatial_unavailable`，不会降级为仅按距离查询。

本接口不判断视线、寻路、可交互性、听见／目睹或角色知情程度。

## 生命周期、开销与错误

一次调用同步完成，不缓存游戏对象、不创建历史快照或定时采样。扫描最多 10,000 个管理器条目，临时保留最多 64 个结果对象，扫描去重键也受扫描上限约束。扫描超限在 coverage 中报告。本次范围并不保证原子世界快照，保留读取时间区间；性能尚未实机测量，不建议每帧调用。

查询与后续 `get_context` 是两次读取。session 一致只证明同一次运行，角色在期间仍可能移动；需要最新邻近关系时重新查询。单次查询不影响事件 FIFO、历史页预算、写盘队列，也不依赖事件记录器／中文解释启用。Collector 关闭时返回 `collector_disabled`。

沿用 `invalid_request/not_ready/wrong_thread/session_changed/session_closed/collector_disabled`；新增相关错误：

| code | 含义 |
| --- | --- |
| target_unavailable | 无当前操控 Sim，或指定中心没有可读取实例 |
| target_out_of_scope | 中心实例不在当前采集范围 |
| spatial_unavailable | 中心缺少必需位置／楼层／房间；details 中保留字段与证据 |
| capability_unavailable | SDK 检测旧提供方未声明 context.nearby_entities |

## 游戏内导出与复测

API 本身不写盘。为验证提供 `co.nearby`，显式把同一查询结果交给已有导出器：

```text
co.nearby
co.nearby active 8 all
co.nearby active room sim
co.nearby active 12 all False False 32 euclidean
```

位置参数依次为中心 Sim、半径或 room、类型、same_level、same_room、limit、metric。类型可以是 sim、object、all 或 sim,object；room 表示不限定半径且要求同房间。第一条默认查询 8 个世界单位内、同楼层的 Sim。

控制台返回 count、matched_count、truncated、coverage 和导出路径。`status=queued` 表示进入写盘队列；结合 `co.status` 的持久化状态确认写入。文件位于 `ContextOverlay/runs/<session_id>/context-<request_id>.json`，kind 为 nearby_entities，不写成历史事件。

建议复测同层不同房间、楼上楼下、室外、泳池，以及桌面物件与库存物品的区别；用两种 metric 和 room-only 查询对照。记录输出文件和游戏截图，不依靠计数本身判断空间结果正确。
