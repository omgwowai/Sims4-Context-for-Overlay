# 架构与采集语义

## 三个模块

| 模块 | 状态与职责 |
| --- | --- |
| 事件记录 | 观察事实、身份与修订，维护关联历史、索引、持久化和覆盖状态 |
| Context 采集 | 按请求固定目标，读取当前字段，按需组合历史 |
| 数据语义化 | 从普通数据和已核验资源生成中文，保留原事实、来源及缺失原因 |

三个模块由同一个 MOD 提供，可独立停用。当前状态不依赖历史成功；关闭语义化仍返回原始数据。采集阶段的游戏名称解析及窗口文字展示不完全由 `semanticizer_enabled` 控制。外部模型和玩法执行属于消费者。

公共 API 统一参数校验并固定 Context 目标，控制台与开发驱动的 Context 导出共用此入口；Inspector 使用已选定的目标。Collector 组合当前状态、历史与文本，Runtime 负责文件导出与命令输出。最近历史仍按最后更新排序，分页查询默认按首次观测排序。技能、职业及人生状态等方法事件共用同一份前后快照读取规则，特殊来源继续补充各自的角色、因果与上下文。

## 范围与事实纪律

当前状态只查询活动地块内已实例化的 Sim／物件，默认字段为身份、位置、时间、交互、需求、Buff、关系和常用物件状态；具体字段选择见 API。查询不创建关系、不产生生活历史，也不持续扫描场外实体。

同一请求开始时固定目标，记录读取起止时间；不能将后来的当前选中 Sim 用于解释旧包。多个交互可以同时运行，Sim ID、物件实例 ID 与定义 ID 分开，ID／ticks 以字符串保存。查询空结果、范围外、未支持、读取失败和模块停用分别表示。

历史只依据实际通知和已经核验的 Hook，不从最终状态推回过去。不同通知的合并必须有交互／操作身份；同一个人物在相近时间做同名动作仍可能是不同发生。Hook 保留游戏返回值、异常和随机调用次数，采集错误独立报告。原生枚举或方法存在不等于所有路径已经覆盖。

事件允许没有 Sim 主体。`entities` 用于相关实体索引，效果归属由 `roles` 表达；不能将所有被索引实体都说成受影响者。`cause` 只有同步调用、resolver 或明确交互证据时建立，不以时间邻近推定因果。

## 事件覆盖

事件类型为 `interaction`、`state_change`、`game_event`；生活事件的 `category` 与 `field` 同值，可用于历史筛选。没有记录不证明没有发生。

| 领域 | 当前事件与实现 | 边界 |
| --- | --- | --- |
| 具体行为、社交 | `interaction`：排队、开始、退出、进场时已有交互；具体 mixer 默认主要层；AllSims 参与者、目标、角色、续接 ID、退出类型、`outcome_result` | 原生动画、姿态／携带取消等明确技术来源在内部层；其他未知用途先显示。自然退出与玩法成功分支分别表达，不合成整次聚会或做饭容器 |
| Buff、情绪、物件状态 | BuffComponent 增减、handle／原因／mood 详情；`buff.refreshed` 表示仍存在的 Buff handle 或详情发生变化；`mood.changed`；原有八类常用物件状态 | Buff 嵌套添加／移除不重复计数；加载恢复排除。Buff 用途未建立完备分类表，未知保留并标待解释；不按隐藏标记删除 |
| 直接数值效果 | `statistic.direct`：已加载 Loot 实现的同步执行范围内，读取 `_notify_change` 真实旧值与已 clamp 的 `_value` | 包括该路径中的需求／关系／其他统计量。没有明确操作、无实际变化、加载或操作抛异常不生成。没有全局数值流，没有持续活动边界快照，不把自然衰减或请求 delta 当净效果 |
| 技能、特征 | `skill.level`：Skill.set_value／_update_value 的实际离散等级变化；`trait.added/removed` 原生通知 | 等级变化包括下降；不保存经验值曲线。原生等级通知作为适配缺失时的后备，明确可能为初始化，不补造旧等级 |
| 关系 | 原有 `relationship.bits`；`relationship.spouse` 配偶状态；`relationship.knowledge` 已知特征／职业／偏好等实际变化；`relationship.sentiment` 成员变化 | 配偶双向通知去重，离婚后再婚为新事实；knowledge／sentiment 保留方向。Sentiment 不记录强度曲线。未知关系标记保留原始资源 |
| 职业 | `career.changed/promoted`、`career.work_started/work_completed`；补 Hook 记录 `career.demoted/retirement` | 通用职业加入／退出／解雇等保留原始 origin；不推断场外工作过程。工作通知的 money_made 是游戏结算值，真实到账另外由资金修改入口核验 |
| 人生、家庭 | `life.pregnancy/age/death/household` 实际状态变化；`life.offspring_created/adopted` 原生出生／收养；`life.milestone` 解锁 | 捕获调用前本地证据，死亡／入库存后仍保留身份。怀孕清除不推断流产。milestone 保留 NEW_SIM／LOD_UP 等上下文，不把补授时刻当原人生事件时刻 |
| 制作、收藏、成长 | `crafting.completed` 的产物、recipe、品质、masterwork；`collection.acquired`；`progress.unlocked/item_unlocked`；`aspiration.goal_completed/stage_completed` | 原生通知没有具体目标时通过同步调用上下文补身份，仍缺失则显式 `missing_fields`。无 Sim 的制作通知不虚构制作者；不把原生通知宣称为全部收藏／配方路径 |
| 库存 | `inventory.transfer`：插入、移除、拆堆、移入隐藏库存，记录容器、数量和产物 | 比较实际变化并验证返回；加载不生成，拆堆不当销毁。没有完整追踪建造模式家庭库存、所有销毁／出售／替换路径 |
| 反应、广播 | `reaction.started` 来自实际开始的 REACTION 交互；`broadcast.effect` 来自通过外层测试后执行的效果回调 | 同一广播对象／效果／受影响实体持续执行更新同一事实的修订和次数，移除后再次施加为新事实。每个已观察回调更新次数；回调正常返回不自动证明效果成功，不推断目睹或理解 |
| 事件金额 | `payment.completed`：FamilyFunds.add／try_remove_amount 的真实前后资金与 actual_amount，保留 reason、请求金额、参与者及可用来源 | 限定显式本地 Sim 或本地 Loot 操作，未归因家庭变动不写流水。与动作的关联只在有 resolver.interaction 时建立；不宣称全部交易已覆盖 |

Hook 包装保留游戏原返回值与异常；采集异常单独报告并暂停采集。生成器实现不套用同步 Hook。Loot 与广播仅遍历安装时已经加载的具体子类，运行中才加载的实现需以后补适配；这项限制在能力状态中可见。


## 生命周期、保留与分页

排队、运行、自然结束、取消、失败和未知分别记录；交互消失或取消请求本身不能证明成功完成。缺少开始／结束证据时保持缺失。游戏发生时间、观察时间与现实记录时间各有含义。

主要层与内部层分开。TimeSince 计时统计不进入事件历史；有明确用途依据的技术交互分为内部层，未知用途保留并标记。没有持续需求／关系采样，也不在活动开始和结束时制造数值差值。

每运行最多保留 200,000 个逻辑事件，按首次接收 FIFO 淘汰，后续修订不刷新年龄。先确认新修订被日志队列接收，再移除旧事件及索引。已有查询继续持有固定修订直到关闭或过期；数量上限不保证任意事件构成都能满足内存预算。

`group_effects=True` 仅将同一筛选结果里同时存在且有明确 `cause.event_id` 的效果置于动作的 `effects`；动作未入选或已淘汰时，效果独立显示。API 默认不分组，原生窗口的主要历史默认分组。分页的 `total_matches` 是分组后行数，预算仍计算全部事实。

日志是追加 JSONL，FIFO 不裁剪磁盘；离线读取的 `retained` 模式应用淘汰记录，完整离线报告则保留日志中所有事件的最终修订。已接收和已写入序号分开，fsync 后才确认持久化，完整导出经原子替换发布。内存／队列／磁盘过载暂停记录并报错；查询预算不足只拒绝查询。默认预算和命令见开发说明。

运行之间不自动合并；读档、旅行和重启建立新 session。保留的历史可按已知实体 ID 查询，即使该实体已离开地块；这不扩大当前 Context 的读取范围。

## Autonomy 决策

### 记录范围

Autonomy 是事件记录模块的新增采集源，默认开启，沿用当前地块内已实例化实体的范围。普通交互以完整 `InteractionQueue.append` 返回成功为保留门槛：仅收到 `on_added_to_queue` 通知不够。成功入队后未开始就取消，决策仍保留；开始、取消、失败和结束沿用交互记录。

立即交互绕过队列，以原生 `GeneratorElementBase._run` 实际进入对应交互的 `_run_gen` 为等价门槛。只有开始通知、尚未执行的情况不保留决策；进入执行后返回 false 或抛出异常仍保留，并分别保存执行返回值或异常类型。

主行为与聊天等真实子行为选择均记录。子行为、已确认的技术动作及姿态来源归为 `internal`，查询时设置 `include_internal=True`。仅有延续／父子交互关系不生成新决策，也不凭“这个 Sim 的最近决策”补连。

### 三部分数据

| 部分 | 已记录 | 解释边界 |
| --- | --- | --- |
| 筛选 | GSI 行为评估行数、评分行数、阶段／原因模板的淘汰计数、对象检查状态计数；子行为提供者失败尝试数 | 只覆盖游戏本次生成的 GSI；未枚举或在 GSI 前失败的检查不补造。对象状态包括通过和失败，不能把全部状态数当作淘汰数。最多保留 128 种原因／状态，其余只保留数量。 |
| 评分 | 所保留候选的原始评分、最终选择权重、路径时间、多任务比例，以及可唯一关联的 `InteractionScore` 字段 | 可含需求贡献、关系／Buff 倍率、时间距离、机会成本和注意力明细。缺失项列入 `missing`；不重跑评分或相加成“原因占比”。 |
| 选择 | 实际池大小、总权重、原始索引、按权重排名、选中项、原概率、遗漏概率；按权重／均匀／确定性三种方式区分 | 概率以该层完整实际池为分母，保留明细不会重新归一化。多层的概率是条件概率，不能跨层比较权重。 |

每层默认最多 **5** 项：若赢家在前五，保留前五；若不在，保留最高四项和赢家。赢家仍保留原始排名及引擎顺序，并列权重以原顺序稳定排序。一轮中可能包含“子行为提供者 → 组别 → 具体行为”，或“行为类型 → 具体目标”，各层独立限制。

子行为提供者保留游戏 GSI 原评分说明；具体 mixer 保留调用中按 AOP 身份关联的基础权重，以及在同次评分调用内唯一匹配的 GSI 文本。文本中的内容分／组别按原文保存，尚未转为结构化分项；个别服务倍率未在 GSI 单独提供，明确列为缺失。主行为评分明细保留游戏自身的 GSI 过期警告，不用采集器另算总分替换游戏分数。

多任务门槛记录实际 getter 返回值、阈值、是否适用和 selector 返回结果。游戏组件把捕获原函数的转发方法挂到具体实例所属类型，因此在每次选择前检查实际 Sim 类型并按需接入；基础 Sim 类不必具有该方法。已接入或继承同一包装的方法不重复包装，也不额外调用 getter。每层 `multitasking.roll_source` 区分 `native_getter`、`native_gsi` 与 `unavailable`；`hook` 表示此次类型入口是否接入。仍不获取行为抽签的底层随机数。

若实际选择池不能观测，只保留有证据的评分输入，并把 `pool_count`／概率标为缺失。不能把这种情况解释为完整覆盖。

### 生命周期与关联

选择时暂存普通 JSON 数据，并绑定具体交互的弱引用与本次采集的唯一决策序号。缓存命中时使用原选择记录：`selection_time` 和 `commit_time` 分开；`cache_origin=true` 表示实际观测到缓存校验入口，false 只表示没有这项证据。一次请求对象可以产生多个独立选择序号。

正式事件使用 `event_type=game_event`、`category=autonomy.decision`：

| 字段 | 用法 |
| --- | --- |
| `payload.decision_id` | 与正式事件 ID 相同；不使用请求对象地址作为持久身份 |
| `payload.interaction_event_id` | 选中并提交的交互事件 ID |
| 交互的 `facts.decision_event_id` | 回指决策；入队回调已产生的交互会追加修订，后续修订保持关联 |
| `payload.context_source`／`is_script_request` | 原始来源；脚本调用算法也可能产生决策，不能一律说成自发意图 |
| `payload.retention_gate` | `queue_success` 或 `immediate_entered`，不等于完成／成功 |
| `payload.stages[]` | 每层独立身份、父层、选择时间、候选、概率和覆盖状态 |
| `payload.execution_result` | 可取得的提交返回／立即执行结果；后续交互结果仍看关联交互 |

决策的实体索引只包含执行者。落选者和落选对象留在候选证据中，不成为实际事件参与者。决策和交互用专用字段互相引用，不把“做出决策”伪装成交互产生的玩法效果。


只统计实际观测到的筛除原因，不保存全部被测试淘汰候选的逐条明细。N 只限制写出的候选，不能改变游戏选择池或多调用评分器／随机函数。决定采集错误不得改变原游戏异常、返回值或随机序列。

暂存分为“等待提交的决策”和“上游选择／缓存评分”两组，各最多 256 条，共用 8 MiB 估算预算，600 秒现实时间过期。弱引用、失效、离开范围、FIFO、过期及会话结束均有丢弃计数；预算不含游戏自身 GSI 生成成本。关闭时恢复本模块启用的 GSI 状态，其他消费者接管开关后保留其状态。

`get_status().autonomy` 公开入口、提交数、缓冲峰值、丢弃原因及错误；选择序列化时间不是总性能开销。具体 Sim 类型的多任务 getter 在选择前按需接入，`pending_sim_type/native_sim_type_method/partial` 及 `roll_source` 表示实际覆盖；局部失败不关闭其余采集，也不抹去已经出现的缺口。

## 文本与资源

### 文本依据

资源名称与描述来自本机游戏资源，不按英文 tuning 名猜译，不使用 LLM。`scripts/build_resource_catalog.py` 直接读取安装目录中的官方 STBL 与 combined tuning，使用 `sims4-python/tools/dbpf.py` 和 `extract_tuning.py` 的只读解码器；输出中记录游戏版本、解码器／字段映射摘要和配置来源。

先按 `Resource.cfg`／`ResourceClient.cfg`／`ResourceSimulation.cfg` 的 `Priority` 选择**同一完整 TGI（type、group、instance）** 的资源。优先级相同且内容不同则保留冲突。之后才合并已选 STBL：不同有效 TGI 使用同一字符串键且文本不一致时，不以路径顺序选赢家。被整个资源覆盖的旧键不会从旧包补回来。

这是“本机已安装官方资源”的目录，不证明运行时启用了所有资料片，也不自动识别其他 MOD 的同键文本覆盖。运行时名称字段仍来自实际已加载的对象／tuning。遇到第三方 MOD 提供的新 hash，可能出现 `string_key_missing`；同键覆盖尚不能自动确认，`string_source.third_party_overrides` 明确为 `not_verified`。

### 三个文本角色

| 角色 | 运行时／静态来源例子 | 边界 |
| --- | --- | --- |
| `name` | 交互 `get_name`、Buff `buff_name`、技能 `stat_name`、物件名称组件 | 已记录的运行时名称证据优先 |
| `description` | `buff_description`、`bit_description`、`skill_description`、`trait_description`、`recipe_description`、`display_description` | 各自保留 hash 与参数，不把名称参数挪给描述 |
| `tooltip` | 配方 `unavailable_tooltip`、显示 mixin 的 `display_tooltip` | 读取提示文本不代表提示条件已经成立；运行时 source 带 `condition_evaluated=false` |

静态目录覆盖 13 类：Buff、关系标记、统计量／技能、交互、物件状态、特征、配方、情绪、抱负／里程碑、抱负路线、职业、职业路线、职业等级。资源条目数不等于有自然语言名称的数量，更不等于运行时翻译成功率。

字段不存在于显式 XML 时返回 `no_explicit_link`，**不宣称游戏没有这个文本**；它可能来自默认值、继承、客户端字段或运行时方法。多候选字段及情绪强度数组保留 `attribute/index`，不替历史记录选择一个变体。目录不展开任意引用或推测条件。

运行时资源字典可附加 `description`、`tooltip`；不支持该字段的类型可以不提供，明确没有 key 的详情为 `not_present`，读取失败为 `unmapped/detail_read_failed`。物件 Context 的 `snapshot.identity.value.description` 使用真实物件 token 获取描述，实体索引和附近列表继续返回简洁引用。

情绪资源若在服务器实例暴露 `descriptions`，按已观测的强度读取基础说明；缺少强度或索引无效时返回 `description_variant_not_selected`。该字段可能仅供客户端使用，运行时没有属性则不提供。年龄和特征覆盖不在此入口猜测，来源标记 `client_overrides_evaluated=false`；离线目录仍保留基础描述的全部候选。

Buff 的普通描述文本若没有 tokens、且缺口全部是第 0 参数的 Sim 姓名或简单 M/F 性别表达式，按读取时明确传入的唯一持有者生成文本参数。已有参数、可调用描述工厂的输出、其他参数位置和未验证格式均不改写。`source.token_binding` 保存 `basis=buff_owner_at_read`、`owner_id`、`token_index` 和 `unbound_localization`；有效参数仍在 `localization` 中。可读详情使用 `basis=observed_buff_owner_context`，窗口标注“按持有者解析”。离线目录不会自动从旧名称挪用参数。

交互的通用名称无法完整解析时，尝试从该 Sim 的 `UIManager._find_interaction` 读取同 ID 的 `display_name`；若有弱引用，还须指向同一交互实例。只采用完整解析的 UI 文本，不修改 UI，也不借用分组里其他交互的名称。来源为 `runtime_ui_queue`，失败的原名称证据保留在 `source.fallback_from`。UI 记录不可用或仍不能解析时维持原结果。

每个文本对象保留 `text`、`status`、`hash`、`template`、`localization` 和 `source`。`localization.tokens` 是当时复制的证据；`string_source.sources` 是当前字典来源 ID，可在相同构建的 `string_sources.json` 中查询包名、TGI、资源摘要与配置优先级。构建与查询的 `provenance.string_sources_sha256` 标识这份目录。

### 模板支持与缺口

在原有姓名、普通数值、嵌套字符串、物件名称／描述、简单 M/F 分支之上，新增 `ObjectCatalogName`、`ObjectCatalogDescription`、Sim token 的 `ObjectName` 别名及小写 m/f 分支。目录名称／描述不会被自定义名称替换。修复空自定义代词槽 `|||||` 的处理；真实自定义代词或中性分支仍保留未解析。

日期 token 的原始字段和 SIM_LIST 中无独立 type 的子项能正确保留。整数 `Money` 现按“1234 模拟币”表达，`TimeShort` 使用 24 小时 `HH:MM`，`DayOfWeekShort/Long` 支持 EA 游戏日期中的“周一／星期一”。星期要求 month/year 均为零、date 为 1–7（星期日为 7），依据 `DateAndTime.populate_localization_token`。返回 `format_profile=context_overlay_zh_CN_v1`，表示项目的确定性中文格式，不承诺与客户端逐字符一致。

小数金额不猜取整方式；日期自定义格式 hash、真实日历日期、`DateShort/TimeLong`、列表、`T/DAE` 年龄条件、姓名前缀和复杂代词仍明确未解析。现有逆向资料尚未给出全部客户端规则，不能把 Sim 参数强制改成 String 来填充聊天对象。

`unresolved` 增加 `category`，区分 `parameter_evidence`（缺参数、参数不完整、类型不匹配、缺字段或无效值）、`grammar_support`（未支持的表达式／格式）、`budget` 和 `resource_or_format`。类型不匹配另附参数位置、实际类型及期望类型；`status=unresolved_tokens` 保持兼容。审计分别统计未解析文本出现次数与表达式类别，同一文本可能包含多个表达式。离线重解释保留 `no_verified_name_accessor`，不再把没有已验证读取入口改记为无名称 key。

解析继续受深度、节点、token 数和字符预算限制。模板不支持、缺字段和超限都保留具体原因，不能标记为成功。无参数静态文本也保存 template，便于消费者核对。


### 离线重解释

工具侧 `scripts/offline.py` 使用 `typed_resource_semantics_v2` 目录，资源目录重解释不进入游戏包。匹配资源类型、ID 与 tuning 名，冲突不任意选择，动态 tokens 只能来自当时证据，不能从今天的游戏对象或另一文本字段补造。

原始事实保留，重解释视图放入 `semantic_view`，可读结果放入 `rendered`。静态目录缺少显式字段不证明游戏没有文本：继承、默认值及客户端覆盖不一定在目录中。资源说明最多返回 128 项并去重，超过预算明确截断；详情不改变资源名称。

## 不在当前范围

场外完整生活、Situation 专用生命周期与目标评分、完整家庭账本、连续数值曲线、跨运行合并、任意历史时点状态重建、资料片系统全集，以及模型生成／执行玩法。有关交互和 Buff 可能留下旁证，但不能据此宣称专门覆盖。
