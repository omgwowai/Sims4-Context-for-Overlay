# 摄像机视锥查询

ContextOverlay 0.11.0 / API、SDK 2.3.0 新增 `context.camera_view`。调用方在游戏模拟线程按需调用 `get_camera_view`，取得当前区域中位于近似视锥内的实体摘要。查询本身不写历史、不创建分页快照、不启动轮询。

接入前通过 `client.get_api_info()["capabilities"]` 检查 `context.camera_view`；SDK 会自动拒绝不支持此能力的提供者。完整示例见 [camera_view.py](../sdk/examples/camera_view.py)，复测步骤和已有证据见[视锥查询验收](camera-view-validation.md)。

## 调用与参数

```python
view = client.get_camera_view(
    kinds=("sim", "object"),
    vertical_fov=None,
    aspect_ratio=None,
    far=None,
    expected_session_id=None,
)
entities = view["results"]
complete = view["coverage"]["complete"]
```

| 参数 | 约定 |
| --- | --- |
| kinds | 非空且无重复的 list/tuple，选择 `sim`、`object`；默认两者。Sim 包括游戏按 `is_sim` 分类的非人类角色 |
| vertical_fov | 垂直全视场角，单位度，有限数且 `0 < value < 180`；None 使用近似默认值 45° |
| aspect_ratio | 画面宽／高，有限正数；None 使用近似默认值 16/9。数值组合必须能表示为有效视锥 |
| far | None 默认不额外限制最远距离；否则为有限正数。沿相机前向的深度上限，单位游戏世界坐标，不是到相机的欧氏距离 |
| expected_session_id | 可选会话约束，沿用现有 API 的读档／旅行语义 |

近端平面固定在相机位置（`near=0`）。结果按相机到实体坐标点的三维距离排序，再按 kind 和数值 ID 排序；ID 始终以十进制字符串输出。无 `limit` 或分页参数，返回已成功判定的全部命中实体。

## 相机与几何精度

读取 EA `camera` 模块最近一次保存的位置、目标点和区域 ID，由目标点减相机位置推导朝向。普通镜头假定世界 Y 轴向上、无滚转；接近竖直时使用确定性的 Z 轴参考并报告 `orientation_basis`。不声称获得了渲染矩阵、实时 FOV 或实际 viewport。

响应回传实际 `vertical_fov / horizontal_fov / aspect_ratio / near / far` 和参数来源。默认角度、宽高比是近似值；调用方传入参数也不能使结果自动成为渲染器精确可见集合。所有结果均标记 `query.approximate=True`、`occlusion_checked=False`，墙壁、家具及楼层遮挡不参与判断。

优先从 EA `get_fooptrint_polygon_bounds` 的局部占地边界及物体缩放，取得以实体世界坐标点为中心的近似球半径；其次使用可用的寻路半径；都不可用则使用坐标点。通用物体没有自己的 routing context 时，不把默认代理半径当成该物体尺寸。`bounds.method / radius / source / fallback_reasons` 记录实际依据。

**占地与寻路半径均不是完整渲染包围盒**：高物体、动画中的 Sim、视觉模型偏移仍可能漏选；球与各平面的保守检测也可能在角落误选。`coverage.complete` 不承诺视觉精确。第一版支持范围是普通生活模式，包括暂停、旋转、缩放、跟随；建筑、购买、第一人称及 Tab 镜头不在首版保证范围。

`camera.freshness` 报告 `observed / unknown`、最近观测到客户端同步的 UTC 时间和墙钟秒数。监听 `camera.update` 仅记录常数大小的时效信息，不扫描实体。读档恢复、区域退出或不匹配的状态会清除／拒绝沿用旧时间；无法确认时效时仍可使用当前区域的有效 EA 状态，并标记 `unknown`（可能来自存档恢复）。不以超过某个时间阈值自动拒绝静止镜头，且 `render_frame_synchronized=False`。

## 范围与返回内容

`scope.kind=zone_instantiated`：枚举当前区域已加载对象，包含地块外、各楼层的世界实体；排除隐藏对象、没有活动实例的 Sim、对象及其父链中的库存内容。墙体、地面和纯客户端布景不保证是可枚举的独立 GameObject。

```text
kind = camera_view
api_version / module_version / schema_version
session_id / request_id / recorded_at / provenance
scope / query / camera
read_started / read_finished / execution_ms
results[]
  entity                  kind / id / key / name，物件含 definition_id
  identity_status
  spatial                 position / level / routing_surface（沿用字段状态包装）
  distance / depth         到坐标点的三维距离、沿相机前向的有符号深度
  containment              inside / intersects，针对使用的代理球或点
  bounds                   method / radius / source / render_bounds / fallback_reasons?
  same_lot                 是否在当前地块，带字段状态
  context_scope            active_lot_instantiated
count / matched_count / matched_count_exact / truncated
coverage / status
```

球部分进入视锥即纳入，边界包含。球可能跨越相机所在平面，此时实体坐标点的 depth 可以为负。楼层与房间不参与筛选；不额外调用房间原生查询。

地块外实体会出现在摘要中，但本次没有扩大 `get_context` 或历史采集范围。继续按 ID 查询详情时，其实时字段仍可能为 `out_of_scope`。第二次查询发生在稍后的时刻，同一 session 不能证明实体状态或视锥成员仍未变化。

## 完整性和错误

扫描预算为 10,000 个原始管理器条目（包含隐藏、库存及随后排除的对象），不是返回条数上限。先计入预算再筛选，不使用 EA 预先跳过隐藏对象的枚举器。正常完成时返回全部命中；异常或预算耗尽时返回已确认结果并明确标记缺口。

- `coverage.complete`：枚举已结束，且没有候选因必需数据缺失而无法判断。
- `coverage.enumeration_complete / scanned_count / candidate_count / unresolved_count / reasons`：枚举与判定覆盖情况。
- `coverage.selection_counts / bounds_fallback_reasons`：所有已做几何判定候选使用的近似方式；尺寸退回坐标点属于约定内近似，不单独构成枚举缺口。
- `matched_count_exact=False`：已知数量只是已确认命中数，不代表全部区域。
- `truncated=False`：接口不按返回数量截断；仍必须检查 coverage，不能据此推断扫描完整。
- `status=partial`：存在扫描缺口、摘要可选空间字段不可用或实体引用读取抛异常；可与 `coverage.complete=True` 同时出现。

`identity_status` 表示实体引用是否读取成功，名称解析状态仍在 `entity.name` 内。物件名称可能是带 `status/text/reason` 的结构，Sim 名称通常为字符串；`unmapped`、`unresolved_tokens`、`no_display_name` 等名称状态不会单独令包变为 partial。与 Context 一样，`status=complete` 不保证名称已经完整解析。

相机不可用不会伪装成正常空结果：

| 错误码 | 含义 |
| --- | --- |
| camera_unavailable | 相机状态缺失、非有限坐标或位置与目标点无法构成方向 |
| camera_zone_mismatch | 相机状态属于另一区域，不能套用到当前对象 |
| invalid_request | 参数名称、类型、范围或数值组合无效 |
| not_ready / session_closed / session_changed / wrong_thread / collector_disabled | 沿用现有 API 的生命周期、线程和功能开关规则 |
| capability_unavailable（SDK） | 提供者未声明 `context.camera_view` |

能力发现仍可在游戏外导入并读取，API 调用只能从模拟线程进行。示例见 [camera_view.py](../sdk/examples/camera_view.py)。开发驱动支持 `api_camera_view`，调用参数置于请求 `params`。

接口的离线回归、实机复测步骤、2026-09-24 样本与未验证范围集中在[视锥查询验收](camera-view-validation.md)。性能数字只用于说明对应场景，不构成上限承诺。
