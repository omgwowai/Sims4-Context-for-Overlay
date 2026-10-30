# 活动地块枚举：本地归档扩展

`context_overlay.api.get_scene_entities` 适配发送方的 `scene_archive/2` 接口。
这是一项本地扩展：基础 API/SDK 仍为 2.6.0，MOD 仍为 0.14.1；必须核对
`get_api_info().local_extensions.get_scene_entities == "scene_archive/2"`
和实际脚本包哈希，不能仅凭版本号判断已经具备能力。

```python
from context_overlay import api

status = api.get_status()
packet = api.get_scene_entities(expected_session_id=status['session_id'])
```

调用须在模拟线程进行。可传 `kinds=("sim", "object")` 或其中一种。
为了兼容发送方调用，仍接收 `identifier="active"` 或合法 Sim ID，但仅验证格式，
不解析主角、不要求该人物存在，也不根据其位置过滤结果。

## 范围与完整性

查询遍历当前 zone 对象管理器的惰性 `values()`，经过既有 world eligibility
检查，再调用 `GameObject.is_on_active_lot()`。不设置距离、楼层、房间或 64 条结果限制。
隐藏、库存及其子物件、未实例化、其他 zone、当前地块外实体均不在结果范围。
这里的“完整”只指该范围；不是所有 SimInfo、整个世界、建筑几何或像素可见物体。

相对于交接代码，扫描预算现在也计入被隐藏的管理器条目，避免过滤生成器在预算外
隐式遍历。预算为 10,000 个条目；扫描上限及异常都会报告，不以空列表表示成功。

| 字段 | 含义 |
| --- | --- |
| `scope` | 实际查询范围：`active_lot_instantiated`、zone/lot ID、off_lot excluded、全部楼层与房间 |
| `provider_scope` / `provider_scope_finished` | 底层 Overlay 的 zone 范围及查询结束时读回；不冒充查询范围 |
| `read_started` / `read_finished` | 游戏时钟 ticks，字符串保留精度 |
| `read_*_unix_ms` / `read_elapsed_ms` | 墙钟窗口及单调时钟耗时，不是视频 PTS |
| `results[].entity` | `kind`、十进制字符串 ID、key、可用的名称/definition ID |
| `results[].read_*` | 该实体详情的独立读取窗口 |
| `coverage.complete` | 成员枚举已结束且无未解决的成员判定或 ID 错误 |
| `coverage.data_complete` | 返回实体的名称及空间字段均成功读取 |
| `matched_count_exact` | 与成员枚举完整性一致；空间字段失败不丢弃已知 ID |
| `truncated` | 扫描未完成、枚举中断或查询范围改变 |
| `coverage.reasons/errors` | 分类计数与前 100 条诊断，含可取得的实体 ID；另计 `errors_omitted_count` |
| `status` | 成员和字段均完整时为 complete，否则 partial |

名称读取失败仍返回原始实例 ID；位置或房间不可读仍返回该实体及字段错误。
未知的地块归属不能当作“在地块外”。结果按 kind、数值 ID 排序并去重。
原有附近查询、相机查询、Context、事件和本地化行为不变。

本轮只完成离线适配与 Python 3.7 验证；安装后的原生枚举数量、性能、房间判据和
完整案例仍需新一轮实机验证。离线假服务测试不能作为游戏内通过证据。
