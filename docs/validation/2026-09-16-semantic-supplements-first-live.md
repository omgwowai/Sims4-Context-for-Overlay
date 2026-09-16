# 2026-09-16 语义化补充首次实机核对

用户安装并运行游戏后，对会话 `ee40ccd808274335a1abb012e5affeac` 的已有日志和 Context 导出进行核对。本轮仅分析数据、更新验证文档，没有修改运行时代码、安装包或原始游戏数据。机器可读结果见[分析数据](2026-09-16-semantic-supplements-first-live.json)；实现与自动化测试基线见[离线验证](2026-09-16-semantic-supplements.md)。

**三个模块在本局正常产出数据，新增资源描述已进入实机导出。剩余主要问题是运行时参数获取和绑定，而非找不到中文词条。** 这不是完整玩法覆盖、界面视觉或性能验收。

## 构建与完整性

- MOD 0.6.0，API 1.1.0，游戏构建 1.126.73.1030，游戏内 Python 3.7.0。
- 安装包 SHA-256：`54836702f2068abf80e230ec655dc6586c97a0668b378db254ad404ab9aac058`。会话与导出的源码、字符串和来源目录指纹均与本次构建一致。
- 北京时间 11:21:33 至 11:29:07，约 7 分 35 秒；游戏时间从第 1 周第 2 日 07:01:31 到第 3 日 07:00:50，约 24 游戏小时。
- 三个模块开关均启用，开发测试驱动关闭。
- 日志 13,234 行，包含 12,834 条事件修订与 400 条观测；按 event_id 取最新修订后为 6,713 个事件。严格重放和现有完整性审计通过，错误为空。
- 正常以 `zone_teardown` 结束。结束前写入队列为 0，导出为 `written`，FIFO 淘汰为 0；记录器和持久化错误均为空。
- 本次 runtime.log 没有采集、导出或窗口初始化错误；游戏用户目录没有本次新增的 lastException、lastUIException 或 lastCrash 文件。
- session_end 的状态取自写入自身之前：accepted/durable sequence 为 13,233，最后一行序号为 13,234；这不表示缺少记录。

## 三个模块的本局表现

| 模块 | 本局证据 | 结论与边界 |
| --- | --- | --- |
| 事件记录 | 2,184 个交互、2,595 个状态变化、1,934 个生活／机制事件 | 数据完整写入；76 项入口报告 installed，不能将安装状态等同于全部玩法已验证 |
| Context 采集 | 1 份完整导出，50 条关联历史；身份、位置、时间、交互、需求、Buff、关系共 7 类字段可用 | Sim 的物件状态为正常的 not_applicable；本局无独立物件 Context 或附近查询样本 |
| 数据语义化 | 导出含 20 项去重说明，18 项完整解析、2 项缺参数，未截断 | 全部证据路径有效；关系文本代入双方姓名，基础情绪说明也已在历史中取得 |

生活／机制事件覆盖本局实际发生的 13 类，包括 1,263 条直接数值效果、284 条 Buff 再次应用、115 条广播效果、81 条情绪变化、79 条反应、44 条特征添加、42 条库存转移、9 条制作产物、7 条目标完成、5 条付款、3 条关系知识变化，以及阶段完成和情感变化各 1 条。内部特征和目标不自动等同于玩家看到的性格或抱负变化。

另有 3,158 次 TimeSince 类计时统计量通知被既定规则抑制，不再产生对应数值变化事件。该指标是通知计数，不是性能测量。

Context 在第 3 日 07:00:50 读取 Nyssa Landry，取得 57 个 Buff（4 个标记为可见）、3 个关系对象。读取开始和结束的游戏 tick 相同。日志约 39.95 MiB，导出 302,801 字节；结束时历史内存估算约 109.85 MiB，这是记录器估算而非进程内存实测。

## 描述实际效果

以下统计使用原始运行时文本，范围为 journal 最新事件修订及本局 Context；重复资源、事件元数据和导出可能重复计数，不是事件数或全游戏覆盖率。

| 描述状态 | 出现次数 | 含义 |
| --- | ---: | --- |
| 完整解析 | 631 | 按资源类型、ID、tuning 名、hash 和实际文本去重为 109 项 |
| 模板存在、参数缺失 | 92 | 均为 Buff 描述，涉及 16 个资源 |
| 缺少强度，未选取情绪描述版本 | 28 | 保留 description_variant_not_selected |
| 未取得本地化 key | 3,391 | 当前入口没有 key；不能证明继承、默认或客户端文案不存在 |

可核对的例子：

- “寒酸的装潢”取得说明：“这所房子的美学设计仅仅是乏味与单调而已。”
- 隐藏资源 `buff_Environment_Hidden_Negative_Snob` 没有取得显示名称，但有官方说明：“一个优美的环境能使你的模拟市民心情变好。”这是原字段文本，不能根据名称中的 Negative 把它改写为负面效果。
- “伤心了”的关系说明完整解析为：“Nyssa觉得Lila对不起自己，待在Lila身边会让Nyssa回想起伤痛。”双方 Sim 参数在有证据时可以正常解析。
- 快乐、不舒服、紧张、悲伤、生气等情绪的基础说明已在 mood.changed 事件中读到，记录了强度和 client_overrides_evaluated=false。实际安装环境暴露了这些字段，不再只是离线目录中的候选文本。

本局 runtime.log 没有 Inspector 打开记录，故没有证据证明用户实际检查过新详情布局。这里只确认导出内容和窗口初始化成功。

## 未完整解析的 313 处文本

| 类型 | 次数 | 实际原因 |
| --- | ---: | --- |
| 聊天交互名称 | 215 | 模板“和{0.String}闲聊”收到 SIM 类型的第 0 参数，而非 String |
| Buff 说明 | 92 | 原生模板需要人物名字／性别，但描述字段 tokens 为空 |
| 物件名称 | 6 | 已是有效 OBJECT token，但目录名称 key 为 0，也没有可用自定义名称 |

313 处文本共含 498 个未解析表达式，本局均为 parameter_evidence，没有触发未支持语法。不能直接与上一局的 60 处比较：新构建开始实际采集描述，玩法和资源分布也不同。

Buff 缺口已有明确代码依据：EA 的 buff_name 是可调用的 TunableLocalizedStringFactory，而 buff_description 是普通 TunableLocalizedString。当前适配器只向可调用字段传入参与者，因此同一个 Buff 的名称证据包含 Nyssa 的 Sim token，描述却只含 hash 和空 tokens。原生模板并非不存在，也不是这些 Buff 都不在界面显示。EA 的 BuffUpdate 消息另行携带 buff_id 和 sim_id；不能将服务器静态文本对象视为客户端最终组装好的文本。后续应完善带来源说明的持有者参数绑定，不静默改写本局原始证据。

聊天也有独立 UI 名称路径：EA 社交交互在社交组变化时可能使用多人覆盖名称或队列 tooltip，再由 UI manager 更新名称。当前采集主要调用通用 get_name，本局证明返回值仍可能与模板要求的类型不匹配。需要沿实际 UI 名称路径验证采集，不能把发起者姓名强行替换成聊天对象。

其余 130 处名称为 no_verified_name_accessor，主要涉及广播器和内部目标；另有 28 处情绪说明未选择版本。离线重解释把其中 130 处名称重新归为无 key，但没有新增成功解析的文本；这是状态重分类，不算覆盖提升。

## 后续优先级与边界

1. 完善 Buff 描述的持有者参数绑定，覆盖本局明确的 92 处缺口，并保留原始文本证据。
2. 验证聊天交互的最终 UI 名称来源，再补采 String／成员列表参数。
3. 补充有可靠强度证据的情绪上下文；无强度时继续保留未选取版本。
4. 区分临时／无目录名称物件与采集失败，保留 ID，不凭空命名。

本局没有命中新增的 Money、TimeShort、DayOfWeekShort/Long 模板，也没有命中 T/DAE 年龄选择器。因此数值／日期格式继续只有自动化测试依据；年龄语法仍未支持。没有游戏帧率、Hook 耗时、界面截图或操作断言，不能把日志无错扩展为性能和完整视觉验收。

核心复现命令：

```powershell
$py = "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe"
$run = "$env:USERPROFILE/Documents/Electronic Arts/The Sims 4/ContextOverlay/runs/ee40ccd808274335a1abb012e5affeac"
& $py -X utf8 scripts/validate_run.py $run --output .validation/semantic-supplements-live-integrity.json
& $py -X utf8 scripts/audit_localization.py $run --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json --catalog .local/resource-semantics/resource_catalog.json --output .validation/semantic-supplements-live-localization.json
```
