# 气泡采集

源码 0.12.0 / API、SDK 2.4.0 新增 `context.balloons` 和 `events.balloons`；0.12.1 修复原生请求不支持弱引用导致的延迟来源关联失败。2026-10-08 的 0.12.1 实机会话已核对 31 次发送，其中 27 条有明确来源；0.12.2 修正感受气泡空随机延迟的误报。详见[验证摘要](validation.md)。

## 采集依据

Hook `balloon.balloon_request.BalloonRequest.distribute`，只有原函数正常返回 `True`、气泡所属 Sim 是当前已加载区域内未隐藏的有效世界实例时才采集。0.13.0 起包含人行道和公共空间等活动地块外区域；运行状态的缓存范围为 `zone_instantiated_current_zone_visit`。返回 `False`、抛出异常、未加载／隐藏实体、加载来源均不生成气泡事件。观测不改写原调用参数、返回值或游戏异常，不调用图标工厂，不开启 GSI 或改变气泡时长。

这记录的是模拟端成功提交给 Distributor 的请求，不是客户端渲染回执。类型包括思考、说话、困扰、感受、婴儿感受；数值到类型名使用运行中游戏的 `BALLOON_TYPE_LOOKUP`，未知类型保留原数值。不推断屏幕可见性、人物内心或谈话文本，也不由请求时长推导“现在仍在显示”。绕过这条 Python 入口的客户端效果／第三方气泡不在覆盖承诺内。

## Context：按事件时间窗口读取（0.14.0 / API 2.6.0）

气泡只存入事件系统。Context 不再维护独立的最近 20 条缓存，而是查询 Recorder 的事件索引；默认取本次区域访问中，目标人物最近 **5 个游戏分钟**发送的气泡。只匹配该人物的 `subject` 角色，不混入他为其他人物触发的气泡。没有客户端显示／消失回执，因此不提供“当前仍显示”的推测状态。

```python
context = client.get_context("sim", "active", fields=["identity", "balloons"],
    include_history=False, balloon_window={"past_sim_minutes": 10, "limit": 50})
balloons = context["snapshot"]["balloons"]
if balloons["status"] == "available":
    value = balloons["value"]
    for event in value["events"]:
        print(event["first_observed_time"], event["payload"]["balloon_type"], event["event_id"])
```

`balloon_window` 可用 `past_sim_minutes`（正数，最多一游戏周），或同时提供 `from_ticks/to_ticks`（游戏整数 tick，范围 `[from, to)`），两种方式互斥。相对窗口按运行中游戏的时间换算，包含查询时当前 tick 已发送的事件；暂停不会让游戏时间窗口自然过期，快进按游戏时间推进。`limit` 为单次返回 1–500 条，默认 50，**不是保留条数**。默认 Sim 字段仍包含 `balloons`，`include_history=False` 只关闭普通近期历史块，`origins/producers` 也只筛普通历史块。

`value.format=balloon_event_window_v1`，`events` 是完整事件的独立副本，按首次发送时间倒序，保留原 `event_id/revision/payload/cause/roles/zone_visit` 和持久化状态；没有额外的 Context 气泡身份。返回实际 `window`、`as_of_time/as_of_sequence`、`total_matches`、`has_more`、`retention_gap`、`complete` 与 `coverage`。`has_more` 表示还有匹配事件未返回，`retention_gap` 保守表示本会话事件内存曾有 FIFO 淘汰，不证明此人物或窗口一定缺失。任一为 true，Context 外层为 `partial`；`complete` 只表示保留事件查询覆盖，不是所有画面气泡的召回率。

Context 读取后立即释放内部查询，不给调用者遗留游标，不读磁盘、不产生事件。返回 `history_query` 和 `durable_query` 两组固定人物、时间和访问的参数，可分别分页查询保留历史和落盘事件：

```python
# 在游戏线程的后续回调中逐页处理，大批结果不要一次复制到 UI 内存。
query = client.history(expected_session_id=context["session_id"], **value["history_query"])
consume(query.page["history"]["events"])
# 后续回调中 query.next_page()；完成、取消或关闭窗口时 query.close()。

# 有 retention_gap 或需要核对完整落盘窗口时，使用异步 Events 查询。
request = client.query_event_view(expected_session_id=context["session_id"], **value["durable_query"])
# 后续回调轮询状态，ready 后读取和翻页，最后 close_event_view。
```

落盘查询不会应用历史 FIFO，但只覆盖创建请求时已经持久化的截点；最新已接收、尚未落盘的事件可能暂未包含。页面 `coverage.recording` 和序号会说明截点，不把磁盘延迟伪装成完整实时结果。直接查询其他访问的气泡时，可使用 `query_history` 或 `query_event_view("events")` 并指定／省略 `zone_visit`；Context 固定当前访问。

Recorder 停用时字段为 `disabled`，失败为 `error`；Hook 未安装为 `unsupported`，目标范围外为 `out_of_scope`，Object 为 `not_applicable`。空结果只说明所查保留事件没有匹配项。请求创建到延迟发送之间的来源缓存继续保留；它用于记录 cause，与已移除的最近气泡缓存不同。

**接入迁移：**使用能力 `context.balloon_window` 或 `get_api_info().balloons.context_format` 识别新形状；原 `value.recent` 改为 `value.events`，原 `game_time` 改读 `first_observed_time`，原缓存截断标志拆分为 `has_more/retention_gap`。`get_status().balloons.context_storage=canonical_events`，不再提供最近缓存计数。API 2.6.0 的其他事件和整理层结构保持既有形状。

0.14.1 起离线翻译工具仍能展示 0.12／0.13 旧 Context 导出的 `recent/game_time`，标注为“旧版气泡缓存快照”，并保留当时的 `truncated` 提示。只重新生成说明，不改写旧原始字段，也不把最近条数缓存解释成完整时间窗口；新采集和新 API 不恢复旧缓存。

旧版 0.13.0 实机样本的 123 次 Context 淘汰是当时最近 20 条窗口的更新；全部 320 条气泡仍在 Records，事件 FIFO 淘汰为 0。正常退出是持续采集的结束边界，最终接收／持久化／结束序号一致且 `capture_complete=true` 才能确认完整关闭。旧日志和导出保持原样。

## Records / Events

每次成功发送生成独立的 `game_event`，`category/field="balloon.sent"`，`tier="main"`，`evidence_type="distributor_request"`。同一个请求被多次真实发送时保留每次发送，不按图标或时间去重。事件参与者是气泡所属 Sim；图标引用的人物／物件只保存在 payload，不因此视为参与者或知情者。

主要 payload 字段：

| 字段 | 含义 |
| --- | --- |
| `balloon_type` | 游戏协议数值及已映射类型名 |
| `icon / overlay` | 资源 type/group/instance，十进制字符串避免大整数精度丢失 |
| `icon_object` | 图标对象 ID、manager ID，以及能明确解析的实体引用 |
| `icon_info / category_icon` | 已构建图标 protobuf 的有界字段快照 |
| `duration_seconds / priority` | 请求时长、优先级 |
| `delay_seconds / delay_randomization_seconds` | 原请求调度参数；不是发送后额外等待时间或实际随机延迟 |
| `view_offset / relationship_track` | 可选偏移、关系轨道资源引用，不重新构建轨道协议 |
| `delivery / client_visibility / icon_semantics` | 分别为 `distribute_returned_true / unverified / unmapped` |
| `association` | 有明确来源时为 `recorded_source`，否则 `unavailable` |
| `unavailable_fields` | 可选字段读取失败的清单，其他已取得字段仍保留 |

图标 proto 最多递归 5 层、每层 64 个字段、每个重复字段 32 项；长字符串／字节截断会标记。缺少资源含义映射时保留原 ID，不把图标命名成“饿了”“想恋爱”等心理描述。

按事件字段查询：

```python
with client.history("sim", "active", event_types=["game_event"], fields=["balloon.sent"]) as query:
    sends = query.page["history"]["events"]
```

`read_event_changes`、Records、Events 与普通事件共用持久化、分页和证据机制。`get_status().balloons`、`co.status` 和会话边界记录公开采集覆盖；气泡 Hook 安装失败不会假称已采集。

## Organized / Recap

气泡分类为 `balloon_signal`，在 organized 的 `facts` 中逐次保留。能够从创建请求时的 resolver/source 或发送时的同步调用帧取得确切交互原因时，记录 `cause`；延迟发送按请求的对象身份关联，不按“当时正在做什么”猜原因，也不依赖对象相等性或只凭 `id` 匹配。

来源缓存最多 2048 条、1 MiB 估算元数据、300 秒实际时间；优先弱引用，EA 原生 slotted 请求不支持弱引用时暂存强引用，重复发送不延长期限。元数据预算不代表所引用 EA 对象图的总内存上限，因此同时设置条数和时间上限。每秒实际时间轮询、下一次登记／发送及地块关闭会清理；Recorder 停用不阻止定时清理。`coverage` 报告强引用数、累计 fallback、过期和淘汰数。来源过期／淘汰后仍保留成功发送事实，但可能无法关联活动；暂停游戏超过期限后也可能失去来源。

创建于加载调用帧内的请求在缓存有效期内延迟发送时继续排除，避免把加载效果当作新经历；无法识别或已淘汰的创建来源不作保证。一般请求没有明确 cause 时独立保留。0.12.0 旧会话缺失的原因无法可靠恢复，不追补猜测。

整理层沿既有交互事件 ID 与同一次地块访问规则关联活动。关联不到时独立保留；不会与聊天话题、Buff 或 Autonomy 评分混成一条心理活动。紧凑活动摘要将关联气泡放在 `balloons` 中，recap 增加独立 `balloons` 分区，显示发送时间、所属人物和可选活动引用。每条可回查 unit、event 和原始修订；自动文件导出和质量对账包括气泡计数。

0.13.1 的规则核对了游戏 `1.126.73.1030` 的 12 个调校身份及一个加入跳舞的代理类型。运动比赛、被动听音乐和跳舞等动画步骤只有在原始 Autonomy 记录提供唯一 provider 实例、人物／目标／访问及执行区间均符合已有规则时才归并；相同位置或时间重叠不足以关联。纯待机、缺少 provider 的动态动画继续独立显示。

气泡 organized 单元增加 `association_state`，已验证来源时附带 `source_interaction={event_id, action}`；紧凑摘要保留相同字段。recap 保留 `association_state`，并提供 `source_action` 文本和 `source_event_id`，原始来源证据仍在该气泡的 `cause` 中。

| `association_state` | 含义与展示 |
| --- | --- |
| `activity_linked` | 来源已记录并关联活动，`activity` 指向本快照的活动引用 |
| `source_recorded_activity_unresolved` | 来源交互存在且身份／访问匹配，但没有可靠活动归属；显示来源动作，不再统称“原因未关联” |
| `source_unavailable` | cause 指向的来源事件缺失，或人物／访问不匹配，不输出猜测的来源动作 |
| `source_unrecorded` | 没有交互来源记录，例如原生定时感受气泡 |

来源缓存的淘汰只影响临时的“请求 → 来源”关联；已经写入 Records 的 cause 不会被删除。0.13.0 样本有 181 次来源容量淘汰，但 316 条非感受气泡仍全部保留来源；不能把该计数当作气泡丢失数。0.13.1 未提高缓存预算或改动此生命周期。

## 验证与边界

`tests/test_balloons.py` 覆盖发送成功门、范围外／加载排除、64 位图标 ID、字段部分失败、事件窗口边界／分页／人物角色、Recorder 不可用与 FIFO 缺口、原生 slots 延迟来源与过期、Hook 返回值／异常保持、整理与 recap 原始证据回查，以及感受气泡合法的空随机延迟。`delay_seconds` 和 `delay_randomization_seconds` 可以为 `null`，这表示未设置；无效非空值仍报告字段读取失败。原生气泡 Hook 的字节码符号审计通过，当前版本检查见[验证摘要](validation.md)。

0.12.1 实机会话已观测 15 次思考、12 次说话和 4 次感受请求；27 条有创建来源，22 条能进一步关联已有活动，5 条的来源动作尚未分类而独立保留。4 条感受气泡通过 EA 的定时回调直接构造请求，未携带交互原因；关系 track 和图标证据仍保存。0.12.2 修复这 4 条中空随机延迟被误判为错误的问题，既有日志保持原样。困扰／婴儿感受、旅行和本次空值修复仍需后续实机样本。图标只保留资源身份或图标引用对象，未映射图案含义；不提供屏幕显示回执、谈话原文、想法推断或总体召回率。
