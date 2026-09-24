# 摄像机视锥查询

ContextOverlay 0.11.0 / API、SDK 2.3.0 新增 `context.camera_view`。调用方在游戏模拟线程按需调用 `get_camera_view`，取得当前区域中位于近似视锥内的实体摘要。查询本身不写历史、不创建分页快照、不启动轮询。

```python
view = client.get_camera_view(
    kinds=("sim", "object"),
    vertical_fov=None,
    aspect_ratio=None,
    far=None,
    expected_session_id=None,
)
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

扫描预算为 10,000 个管理器条目（包含随后排除的对象），不是返回条数上限。正常完成时返回全部命中；异常或预算耗尽时返回已确认结果并明确标记缺口。

- `coverage.complete`：枚举已结束，且没有候选因必需数据缺失而无法判断。
- `coverage.enumeration_complete / scanned_count / candidate_count / unresolved_count / reasons`：枚举与判定覆盖情况。
- `coverage.selection_counts / bounds_fallback_reasons`：所有已做几何判定候选使用的近似方式；尺寸退回坐标点属于约定内近似，不单独构成枚举缺口。
- `matched_count_exact=False`：已知数量只是已确认命中数，不代表全部区域。
- `truncated=False`：接口不按返回数量截断；仍必须检查 coverage，不能据此推断扫描完整。
- `status=partial`：存在扫描缺口或摘要可选字段／名称不可用；可与 `coverage.complete=True` 同时出现。

相机不可用不会伪装成正常空结果：

| 错误码 | 含义 |
| --- | --- |
| camera_unavailable | 相机状态缺失、非有限坐标或位置与目标点无法构成方向 |
| camera_zone_mismatch | 相机状态属于另一区域，不能套用到当前对象 |
| invalid_request | 参数名称、类型、范围或数值组合无效 |
| not_ready / session_closed / session_changed / wrong_thread / collector_disabled | 沿用现有 API 的生命周期、线程和功能开关规则 |
| capability_unavailable（SDK） | 提供者未声明 `context.camera_view` |

能力发现仍可在游戏外导入并读取，API 调用只能从模拟线程进行。示例见 [camera_view.py](../sdk/examples/camera_view.py)。开发驱动支持 `api_camera_view`，调用参数置于请求 `params`。

## 验证

离线回归：`python -B -X utf8 -m unittest discover -s tests -p test_camera_view.py -v`。覆盖各视锥平面、相交半径、镜头方向、全量结果、区域与库存过滤、时效重置、覆盖缺口、线程／会话守卫、SDK 兼容性和不写历史。

实机验证应通过正常客户端镜头操作或 EA 发往客户端的相机指令触发，再从脚本侧回读；不能直接给 `camera.update` 填入测试坐标冒充真实客户端同步。实际验证结果另行记录。

### 2026-09-24 本机验证

游戏 `1.126.73.1030`，已安装 MOD `0.11.0`，session `cda62f7fc0264cb28fccb6210b08e105`。运行时 `source_sha256` 与本次构建 manifest 一致；查询前后记录器接收序号均为 395，没有查询引起的历史写入。

| 同一暂停场景 | 命中数 | 完整调用耗时（单次样本） |
| --- | --- | --- |
| 默认 45°、16/9 | 78，其中地块外 50 | 16.7 ms |
| 垂直 FOV 120° | 192 | 29.0 ms |
| 仅 Sim | 1 | 1.8 ms |
| far=1 | 2 | 8.1 ms |

每次枚举 479 个对象且 `coverage.complete=True`：338 个使用占地代理球、135 个使用寻路代理球、6 个使用坐标点。宽视角包含默认集合、距离上限缩小集合、类型筛选及大于 64 条的全量返回均经实测。耗时包括该次 API 返回数据的组装和复制，仅为此场景的样本，不是性能上限承诺。

通过原生 `FocusCamera` 发往客户端的旋转、拉远和跟随指令验证：客户端重新同步相机位置／目标点，查询分别得到 60、83、82 个命中，均完整；跟随步骤实测 `follow_mode=True`。客户端可以约束或调整请求的镜头位置，接口采用实际同步值。正常速度 `1` 下的查询也成功，随后已回读确认恢复暂停 `0`，镜头位置和目标点恢复为测试前值。没有保存存档。

时效限制也得到实测：只发跟随请求、没有新的相机同步时，`follow_mode` 仍保留上一条同步的值，`age_seconds` 持续增长。因此不能用“请求已发送”或 `freshness.status=observed` 证明客户端此刻的跟随开关状态；测试收尾的跟随开关回读未立即匹配原值，保留为未确认状态。默认 FOV 的画面边缘精度、移动中跟随的持续表现仍未做视觉校准，不以这些抽测宣称渲染器精确可见性。

本机证据位于忽略目录 `tmp/camera-view-20260924/`，包括 `report.json`、`checks.json`、各场景返回包、截图及实际发送到开发桥的脚本。为完成进档临时校准的 4K 菜单规则也保存在该目录，安装的 s4dev 规则已恢复原文件。
