# 事件采集覆盖调研

日期：2026-09-15。状态：实施前调研快照。下文的“当前／已记录／未接入”均对应当时的基线，保留用于追溯设计，不代表最终实现状态。0.6.0 新实现、局部支持与缺口以[当前覆盖说明](event-coverage-0.6.0.md)为准；本轮已形成离线验证的试用包，尚未安装或实机验证。

结论：当前已有交互生命周期、Buff 增减、关系标记增减和常用物件状态变化。与 Experience 相比，缺少多参与者视角、效果来源、感知／反应、情境参与和自主决策观测；游戏本身还提供大量尚未接入的离散事实，包括技能升级、情绪切换、制作产物、职业结算和人生节点。补全应同时处理“已经采集但默认看不到”“缺少字段／关联”和“完全没有采集入口”，不能只增加事件订阅数量。

后续访谈已经完成：当前实施范围和取舍见[0.6.0 实施约定](event-expansion-plan.md)。本调研保留候选领域与源码发现，不表示这些候选全部纳入本轮。

## 1. 依据与核验范围

| 依据 | 本次使用的版本 | 用途 |
| --- | --- | --- |
| 当前 MOD | 基于提交 `51c47b96530cda4824a26b922998125097925e6d` 的工作区，包含已删除连续采样的未提交改动 | 以实际注册、Hook、转换和筛选代码判定“已记录” |
| sims4-python | `12718ed96470fc2edffbc7875d10cf537b1f0e57`，`ea-source/EA/simulation` | 主要依据；核对事件发送点、参数及业务状态变更方法 |
| Sims4-Experience-Mod | `9c25822f4fc9cbc68b88cd383d6c884dc489b0c1`，0.7.25 | 对照已实现的六类经历及其限制，不继承其架构或范围 |
| Sims4-Context-Atlas | site_version 25，分析源提交 `1003b2507e5e68dd59eb10135140187bb5ed7750` | 仅用于领域导航；与当前源码基线不同，结论回到 sims4-python 核对 |
| 本地游戏 | `D:/Games/The Sims 4`，已核对基线 `1.126.73.1030` | 本次没有启动游戏，没有新增实机验证结论 |

静态扫描从 `process_event`／`process_events_for_household` 调用入手，覆盖 113 个候选 Python 文件；在 `TestEvent` 的 158 个非 Invalid 静态名称中，138 个找到字面量发送点，共 226 处调用，另定位到 5 处动态发送点。逐项结果见[原生事件清单](native-event-inventory.md)。这些数量**不是玩法覆盖率**：枚举是 DynamicEnum，事件也可能由 tuning、原生代码或其他路径发送；许多枚举只是刷新通知，一种玩法也可能不使用 TestEvent。

反编译质量同样构成边界。[PROVENANCE.tsv](../../sims4-python/ea-source/EA/PROVENANCE.tsv) 将 `relationships/data/relationship_data.py` 标为 new-stub，本次 AST 解析失败；旧路径 `relationships/relationship_data.py` 为 old。部分标为 new-ok 的文件仍有可疑控制流，例如 funds 的资金限制分支。因此，下文“已找到入口”不等于“可直接照抄并保证当前游戏完整运行”；正式实现仍要核对字节码或游戏行为。

## 2. 当前到底记录了什么

本表描述工作区当前代码。历史安装包曾记录的连续差值不计入当前能力；旧日志中的旧事件也不会因为删代码而被清除。

| 内容 | 当前状态 | 已保留的信息及实际边界 |
| --- | --- | --- |
| 交互入队、开始、退出 | 已记录 | 交互运行 ID、tuning、执行者、范围内直接目标、触发来源、阶段、退出类型及时间；同一次交互修订同一事件，不将入队／开始／结束算成三次行为 |
| 采集开始前已存在的交互 | 已记录，起点不完整 | 实体进入观测范围时读取交互队列／运行态，标为首次观察；不补造其实际开始时间 |
| 主交互与内部步骤 | 已分层记录，分类需改进 | `is_super` 等条件决定层级；当前所有非 super 的 mixer 都进入 internal，包括讲笑话、侮辱等有玩法意义的社交动作 |
| 直接目标的关联历史 | 已记录，参与者不完整 | 同一共享事件可以由执行者和范围内直接目标查询；未收集完整 AllSims／听众／群体参与角色，不能等同于完整“谁被谁影响” |
| Buff 添加／移除 | 已记录，信息不完整 | 原生通知提供的 Buff 资源及存在状态前后值；不包含完整 reason、buff_source、情绪权重、刷新／叠加含义和因果链，覆盖还受原生发送条件限制 |
| 关系标记添加／移除 | 已记录，范围受限 | 双方、关系标记、增减；双方都必须在当前范围内。双向通知去重，移除后重新添加是新事件；不是友谊／浪漫数值变化历史 |
| 常用物件状态转换 | 已记录，白名单 | Broken、BrokenState、Dirty、DirtyState、Freshness、Quality、Servings、Consuming 八类状态及通知的前后资源值；不是全物件状态，也不是份数等全部底层数值 |
| 运行、范围进入／离开、错误 | 已保存观测元数据 | 说明记录何时有效、哪里有缺口；范围离开不自动解释为死亡、销毁或旅行 |
| 当前需求、当前友谊／浪漫关系值 | 仅 Context | 按请求读取当前值；不是事件历史，也不能还原任意过去时刻 |
| 需求／关系的事件级数值前后值 | 尚未记录 | 当前 `before`／`after` 结构能表达变化，但尚无对应数值采集适配器 |
| 连续数值定时采样／差值历史 | 已按决定删除 | 配置、调度、基线、差值生成、调试入口及专用展示均删除；不作为待恢复能力 |

实现依据：[Runtime.install / handle_event / poll](../src/context_overlay/game_runtime.py)、[EAAdapter.interaction](../src/context_overlay/ea_adapter.py)、[Recorder](../src/context_overlay/recorder.py)、[物件状态白名单](../src/context_overlay/profiles.py)。Runtime 订阅六个 TestEvent，并接入入队、退出、状态变化三个 Hook；每秒回调现在只做范围维护等工作，不做连续数值采样。

已保存的最近八次旧包运行日志中找到 26 条 `mixer_social`，全部为 internal，包含讲笑话、谈天气、侮辱、吼叫等。这是分类问题的样本证据，不是全部社交动作都已采到的证明。默认窗口最近五条主要事件会排除它们。调整分类应优先于盲目再加一层重复社交记录。

## 3. 与 Experience 相比缺少什么

Experience 的 `did / received / perceived / changed / joined / decided` 是组织视角，不是游戏原生事件全表；当前 MOD 的 `interaction / state_change` 是规范结构。不能按类型名称数量判断丰富程度。

| 对照项 | Experience 的实际接入 | 当前 MOD 的差距 | 借鉴时应保留的限制 |
| --- | --- | --- | --- |
| 做了什么、对谁做 | 归档交互，读取 `ParticipantType.AllSims`，为其他参与者生成 received | 已有 actor／直接 target；缺完整参与者和角色、接收侧视角，社交 mixer 默认隐藏 | 不必复制 received 事件；可以让同一事件关联多个实体及角色。Experience 参与者也有数量上限 |
| 同场环境 | 终态附近物件／Sim 的 co_present 摘要 | 尚无事件发生时的附近环境摘要 | Experience 默认约 8 米、垂直差 2 米、最多 8 项；附近存在不等于看见或参与 |
| 感知与反应 | Broadcaster effect 回调、reaction 触发 | 尚未接入 | 持续广播会重复触发，Experience 使用 120 秒现实时间去重；“效果作用／反应被触发”不能一概写成“亲眼目睹并理解” |
| 效果操作与来源 | Loot operation 回调、容器／交互来源、经由物件；更丰富的 Buff 原因与配对 | Buff／关系标记／物件状态只有局部结果，缺统一操作来源与关联；其他 Loot 效果未专门记录 | 配置中的 amount 不等于实际净变化；正常返回也不必然表示有效。Experience 有过滤、去重、仅 Sim 主体和时间邻近回退等限制 |
| 情境参与 | 加入／离开 Situation、job、容器关联 | 尚未记录聚会／约会等参与身份 | 一个 Sim 可以同时参加多个情境；不能仅保存一个“当前活动”就声称完整覆盖 |
| 自主选择过程 | 自主决策结果，GSI 数据存在时保留候选分数／概率，最多五项 | 当前只有交互触发来源，没有候选／决策记录 | 这是额外诊断能力；用户已选择“触发来源即可”，不自动纳入本轮必做，也不解释为完整心理动机 |

依据：[interaction_hook.py](../../Sims4-Experience-Mod/src/experience_recorder/hooks/interaction_hook.py)、[perception_hook.py](../../Sims4-Experience-Mod/src/experience_recorder/hooks/perception_hook.py)、[loot_hook.py](../../Sims4-Experience-Mod/src/experience_recorder/hooks/loot_hook.py)、[situation_hook.py](../../Sims4-Experience-Mod/src/experience_recorder/hooks/situation_hook.py)、[decision_hook.py](../../Sims4-Experience-Mod/src/experience_recorder/hooks/decision_hook.py)。

Experience 保存样本中确实出现了六类记录，包括社交 did、walkby joined、broadcaster perceived、StatisticChangeOp 和 AddTraitLootOp。但其派生卡片／“里程碑”不是对 EA 原生人生里程碑系统的完整接入；它的默认 Sim 跟踪范围也不同，不作为扩大本 MOD 场外采集范围的依据。

## 4. 从游戏源码发现的其他补足方向

以下均为**尚未专门采集**的领域；“部分”表示现有交互／Buff／标记可能留下旁证，不能据此认为已有完整领域事件。“通知”表示找到实际发送调用并检查参数；“方法”表示已定位状态变更方法，完整成功条件、调用覆盖和实机行为仍须在实现阶段核验。

### 4.1 行为结果、效果和社交

| 待补内容 | 当前情况 | EA 入口与能得到的事实 | 核验重点 |
| --- | --- | --- | --- |
| 交互玩法结果：成功／拒绝／失败分支 | 部分：有 pipeline 退出类型，无 outcome 分支 | [outcome.py](../../sims4-python/ea-source/EA/simulation/interactions/utils/outcome.py) 的 `decide`／`store_result_for_outcome`；[interaction.py](../../sims4-python/ea-source/EA/simulation/interactions/base/interaction.py) 的完成通知 | 自然结束不等于社交被接受；分支已选也不等于所有后续效果成功。需同时记录交互终态与业务结果 |
| 事件触发的需求／关系等数值变化 | 未记录；仅当前 Context | [base_statistic.py](../../sims4-python/ea-source/EA/simulation/statistics/base_statistic.py) 的显式 set_value 通知实际 old／new；[relationship_track.py](../../sims4-python/ea-source/EA/simulation/relationships/relationship_track.py) 的前／后关系通知 | 只在有业务事件依据的路径记录变化；不全局订阅所有数值更新。RelationshipChanged 的 delta 可能是 clamp 前目标差，不保证实际净变化 |
| 效果来源、作用对象及经由物件 | 部分：只有交互 trigger 和少量续接 ID | [loot_basic_op.py](../../sims4-python/ea-source/EA/simulation/interactions/utils/loot_basic_op.py)、[outcome.py](../../sims4-python/ea-source/EA/simulation/interactions/utils/outcome.py) 等操作／resolver 路径 | 区分尝试、配置、实际变更；用交互／操作身份关联效果，不能只按时间邻近归因 |
| 情绪切换 | 未记录独立事件 | [buff_component.py](../../sims4-python/ea-source/EA/simulation/objects/components/buff_component.py) 的 `_send_mood_changed_event` 发送 `old_mood / new_mood` | Buff 增减与情绪切换是不同事实；相同情绪强度变化另行定义，不恢复连续曲线 |
| Buff 原因、来源、刷新／叠加 | 部分 | 同文件 `add_buff` 有 reason、source、from_load 等参数；began 通知只覆盖其特定新增分支 | 已有 Buff 的刷新可能不触发 began；需要确认持续时间、重复应用和移除配对，而非重复写“获得” |
| 婚姻、离婚等配偶变更 | 部分：可能有 relbit 旁证 | [sim_info.py](../../sims4-python/ea-source/EA/simulation/sims/sim_info.py) `update_spouse_sim_id` 发送 `SpouseEvent`，含旧／新配偶 ID | 一次婚姻事实会影响双方；标记变化与配偶通知须关联，不能重复计数 |
| 对他人的知识获得／失去 | 未记录 | [sim_knowledge.py](../../sims4-python/ea-source/EA/simulation/relationships/sim_knowledge.py) 的已知特征、职业、偏好、秘密等方法；`KnowledgeChanged` 只有双方 ID | 通知无 sim_info、无“哪个字段变了”；需要带字段的变更证据，不能写成获得了所有知识 |
| Sentiment 获得、替换、移除／消退 | 部分：可能看到伴随 relbit | [sentiment_track_tracker.py](../../sims4-python/ea-source/EA/simulation/relationships/sentiment_track_tracker.py) `add_statistic`／`_remove_statistic_and_rel_bit`；[relationship_track.py](../../sims4-python/ea-source/EA/simulation/relationships/relationship_track.py) 收敛回调 | 添加可能被规则拒绝；移除可能是替换、年龄变化或自然消退。只记录这些离散节点，不记录强度曲线 |

### 4.2 能力、目标与人生经历

| 待补内容 | 当前情况 | EA 入口与能得到的事实 | 核验重点 |
| --- | --- | --- | --- |
| 技能获得／等级提升 | 未记录 | [skill.py](../../sims4-python/ea-source/EA/simulation/statistics/skill.py) `SkillLevelChange` 带 skill／new_level | 与 SkillValueChange 连续进度分开；初始化／from_load 路径要核对，首次出现不全是升级 |
| 特征获得／失去、奖励解锁 | 未记录；可能有相邻交互 | [trait_tracker.py](../../sims4-python/ea-source/EA/simulation/traits/trait_tracker.py) TraitAddEvent／TraitRemoveEvent；[unlock_tracker.py](../../sims4-python/ea-source/EA/simulation/sims/unlock_tracker.py) UnlockEvent；BucksPerkUnlocked | 区分可见性格、奖励与内部控制特征；从存档恢复不能算新获得 |
| 愿望／抱负目标与阶段完成 | 未记录 | [aspirations.py](../../sims4-python/ea-source/EA/simulation/aspirations/aspirations.py) AspirationGoalComplete／MilestoneCompleted；WhimCompleted 等见附表 | 部分通知仅有 Sim，没有目标 ID，需补上下文；愿望进度刷新不逐次进入历史 |
| 原生人生／成长里程碑 | 未记录 | [developmental_milestone_tracker.py](../../sims4-python/ea-source/EA/simulation/developmental_milestones/developmental_milestone_tracker.py) `unlock_milestone`，有状态、年龄、重复与补授逻辑 | `TestEvent.MilestoneCompleted` 此处主要是抱负阶段，不能代替人生里程碑；补授、回溯授予要单列 |
| 年龄增长 | 未记录 | [aging_mixin.py](../../sims4-python/ea-source/EA/simulation/sims/aging/aging_mixin.py) `advance_age` / AgedUp | ReadyToAge 只是就绪；AgedUp 通知无旧年龄，需从实际变更路径保留前后阶段 |
| 怀孕、分娩、收养 | 未记录 | [pregnancy_tracker.py](../../sims4-python/ea-source/EA/simulation/sims/pregnancy/pregnancy_tracker.py) start／complete、OffspringCreated 含 offspring_infos；[adoption_interactions.py](../../sims4-python/ea-source/EA/simulation/adoption/adoption_interactions.py) ChildAdopted | 怀孕开始不一定有 TestEvent；清除怀孕状态不能统一解释为流产；新生儿可能尚未实例化，需设计本地事件参与者引用 |
| 死亡、恢复存活状态 | 未记录；离开范围不能替代 | [death.py](../../sims4-python/ea-source/EA/simulation/interactions/utils/death.py) `_set_death_type` / SimDeathTypeSet；[ghost.py](../../sims4-python/ea-source/EA/simulation/sims/ghost.py) `remove_ghost_from_sim` | 死亡通知前可能已取消实例化／迁移家庭，当前过滤会漏掉；清除 death_type 的原因及完整复活流程仍须核验 |
| 生病、诊断、康复／移除病症 | 未记录独立事实；Buff 可能是旁证 | [sickness_service.py](../../sims4-python/ea-source/EA/simulation/sickness/sickness_service.py) make_sick／remove_sickness；[sickness.py](../../sims4-python/ea-source/EA/simulation/sickness/sickness.py) DiagnosisUpdated | 发现症状、明确诊断、治疗和康复不同；移除可能是替换病症，不能全部写成治愈 |
| 超自然身份和形态切换、等级节点 | 未记录 | [occult_tracker.py](../../sims4-python/ea-source/EA/simulation/sims/occult/occult_tracker.py) add／remove／实际 switch；[ranked_statistic.py](../../sims4-python/ea-source/EA/simulation/statistics/ranked_statistic.py) RankedStatisticChange | pending 变化不是已变身；等级通知只有 Sim，具体轨道和旧／新等级需补。名气／声誉等领域需继续核对 tuning，不能仅凭通用 rank 通知承诺全覆盖 |

### 4.3 工作、经济、物件与活动

| 待补内容 | 当前情况 | EA 入口与能得到的事实 | 核验重点 |
| --- | --- | --- | --- |
| 入职／离职、晋升、工作日开始／结算 | 未记录领域事件 | [career_base.py](../../sims4-python/ea-source/EA/simulation/careers/career_base.py) CareerEvent／CareerPromoted／WorkdayStart／WorkdayComplete；结算包含职业、工时、收入 | 区分事件 origin、辞职／解雇／退休等路径；场外工作过程仍不采，返回本地时可见的结算单独评估 |
| 大学入学、学期／毕业 | 未记录 | [degree_tracker.py](../../sims4-python/ea-source/EA/simulation/sims/university/degree_tracker.py) enroll／complete_term／graduate；入学通知带 enrolled_sim_id | 入学通知无 sim_info；本次未找到通用毕业 TestEvent，需方法级接入；连续成绩不作为历史采样 |
| 制作完成及最终产物 | 部分：做饭等交互和部分物件状态 | [crafting_component.py](../../sims4-python/ea-source/EA/simulation/objects/components/crafting_component.py) ItemCrafted 带产物、制作者、技能、品质、masterwork；recipe 可读取 | 建立“人—制作交互—最终产物”关联；同名 ItemCrafted 也用于植物发芽，不能全部译为某人制作成功 |
| 摄影、收藏获得 | 未记录专门事实 | [photography.py](../../sims4-python/ea-source/EA/simulation/crafting/photography.py) PhotoTaken；[collection_manager.py](../../sims4-python/ea-source/EA/simulation/objects/collection_manager.py) CollectedItem 带 collection_id／item_id／stack_count | 结果物件、照片目标和收藏进度分开；CollectionChanged 只是更宽泛更新 |
| 库存转移、赠送／交易中的物品流动 | 未记录 | [inventory.py](../../sims4-python/ea-source/EA/simulation/objects/components/inventory.py) 插入／移除及堆叠；[inventory_item.py](../../sims4-python/ea-source/EA/simulation/objects/components/inventory_item.py) OnInventoryChanged | 原生通知没有具体 item／方向；需要数量、旧／新容器和成功结果。加载、隐藏库存、堆叠合并不能当成购买／赠送 |
| 物件创建、替换、移除／销毁 | 部分：仅范围进入／离开 | [client_object_mixin.py](../../sims4-python/ea-source/EA/simulation/objects/client_object_mixin.py) ObjectAdd 在建造购买后修复路径；[game_object.py](../../sims4-python/ea-source/EA/simulation/objects/game_object.py) on_add／on_remove | ObjectAdd 不是通用创建；ObjectDestroyed 在 on_remove 发出，必须区分移除、库存、替换、真正销毁及卸载 |
| 收入、支出、退款、转账、账单／公用事业 | 未记录领域事实 | [funds.py](../../sims4-python/ea-source/EA/simulation/sims/funds.py)、[payment_cost.py](../../sims4-python/ea-source/EA/simulation/interactions/payment/payment_cost.py)、[utilities_manager.py](../../sims4-python/ea-source/EA/simulation/sims/household_utilities/utilities_manager.py) | SimoleonsEarned 只覆盖部分收入，不能涵盖所有资金变动；PaymentDone 无 sim_info；费用配置不等于真实扣款；BillsDelivered 不代表账单已付 |
| 活动／情境开始、参与角色、目标、结束与成绩 | 未记录领域事实 | [base_situation.py](../../sims4-python/ea-source/EA/simulation/situations/base_situation.py)、[situation_goal_tracker.py](../../sims4-python/ea-source/EA/simulation/situations/situation_goal_tracker.py)、[scenario.py](../../sims4-python/ea-source/EA/simulation/gameplay_scenarios/scenario.py) | SituationEnded 的发送有条件，不能保证所有情境都有终态；共同参与者、job、目标与结果要保留身份关联 |
| 家庭变更、迁居、旅行到达 | 未记录领域事实；有运行／范围边界 | [sim_info.py](../../sims4-python/ea-source/EA/simulation/sims/sim_info.py) assign_to_household；[zone.py](../../sims4-python/ea-source/EA/simulation/zone.py) 加载屏结束发送 SimTravel | 读档也经过加载屏，不能直接都叫旅行；HouseholdChanged／SimHomeZoneChanged 未找到字面量发送点；跨运行历史仍不自动串联 |
| 俱乐部／组织、经营、王朝等扩展玩法 | 未记录；通用交互可能留下旁证 | [club.py](../../sims4-python/ea-source/EA/simulation/clubs/club.py) 成员／领袖事件；Business、SmallBusiness、Dynasty 各事件发送点见附表 | 可补成员与职务变化、营业开关、雇员、顾客交易等；以安装的资料片与需求逐项选择，经营数据刷新不等于一笔新交易 |

### 4.4 环境与外部影响

| 待补内容 | 当前情况 | 源码入口 | 边界 |
| --- | --- | --- | --- |
| 季节／天气、节日、社区政策等变化 | 未记录 | [season_service.py](../../sims4-python/ea-source/EA/simulation/seasons/season_service.py) 旧／新季节通知；[weather_service.py](../../sims4-python/ea-source/EA/simulation/weather/weather_service.py) update_weather_type；[holiday_tracker.py](../../sims4-python/ea-source/EA/simulation/holidays/holiday_tracker.py) activate／deactivate；政策见附表 | 本地环境事实与角色亲身感知分别表示；加载恢复不当作新节日／新天气；不记录逐帧天气量 |
| 火灾、着火／灭火、雷击及损害 | 未记录独立领域事实；可能有反应交互／Buff／损坏状态 | [fire_service.py](../../sims4-python/ea-source/EA/simulation/services/fire_service.py) spawn／burn／remove／extinguish；[lightning.py](../../sims4-python/ea-source/EA/simulation/weather/lightning.py) strike_terrain／object／sim | 火源、受影响实体、行动与损失分开；FireInsuranceFraud 是资金相关通知，不能替代火灾生命周期 |

疾病、天气、人生里程碑等说明：**仅补 TestEvent 订阅不能覆盖全部有价值事实**。也不应一次接入所有领域；以上是待选清单，不是已承诺全部纳入 0.6.0 的范围。

## 5. 补全前必须解决的共同问题

1. **事件语义与默认展示。** 社交 mixer 要根据真实玩法意义重新分类；交互自然结束、outcome 分支选择、效果实际落地分开保存，不把它们合成未经证实的“成功”。
2. **共享事件与参与角色。** 在现有实体事件 ID 容器上补 actor、receiver、participant、product、affected 等有依据的关系。多个 Sim 收到同一通知不自动生成多个独立事实；围观／感知必须有单独证据。
3. **无需 sim_info 的通知。** [EventManager](../../sims4-python/ea-source/EA/simulation/event_testing/event_manager_service.py) 允许仅带实体 ID、家庭、物件或世界参数。当前状态分支要求 live Sim，新增领域需各自解析主体，不能把事件名直接加进注册列表就算完成。
4. **本地事件的终态边界。** 死亡、进入库存、销毁等事件到达时实体可能已离开实例集合。需要事件发生时的范围证据和短期身份引用；这不等于重新启用场外角色后台采集。新生儿和跨边界参与者怎样表示仍需设计。
5. **前后值与因果。** 显式 set_value 路径可提供实际 old／new；关系通知的 delta 可能在 clamp 前计算，而且另一方通知不含同样字段。不能一律用 `before = after - delta`。需按源确认真正变更值，保留失败／未知状态。
6. **持续效果的收尾。** [statistic_element.py](../../sims4-python/ea-source/EA/simulation/interactions/utils/statistic_element.py) 可把交互的连续需求／技能增益转换为速率 modifier；[continuous_statistic.py](../../sims4-python/ea-source/EA/simulation/statistics/continuous_statistic.py) 按流逝时间结算。仅 Hook 一次性 Loot 会漏掉这类效果；后续访谈已明确本轮不保存持续活动的开始／结束数值，只保留具有明确来源和真实前后值的直接效果。不得恢复全局数值更新流或定时采样。
7. **发生、恢复与重复。** from_load、初始化、补授、事件向家庭成员广播、重复 Buff、双向关系和多个 Hook 都可能重复报告。按源的发生身份去重，不能用“同名且时间接近”吞掉两次真实发生。
8. **覆盖可发现。** 新领域应让 SDK／Context 消费者知道已支持、未启用、缺资料片、入口失效和范围受限的区别。现有 200,000 条上限和显式过载失败规则继续适用，扩展后另做容量与事件量验证。

## 6. 建议讨论顺序

以下保留访谈前的建议。访谈后的实际顺序见[实施约定](event-expansion-plan.md)：本轮接入实际反应／效果作用，但不建立活动容器；数值只收直接效果；先源码与离线检查交付试用。下列候选清单不再直接作为本轮范围：

1. **先让已有数据正确表达玩法：** 社交 mixer 分类、多参与者及角色、交互结果和触发／效果来源。
2. **闭合日常场景的结果：** 事件级数值前后值、情绪、完整 Buff 信息、制作产物、物品转移；用做饭／吃饭和社交场景检验关联是否可信。
3. **补低频高信息量节点：** 技能升级、特征／解锁、关系知识／Sentiment、工作结算、年龄／家庭／人生里程碑。
4. **再按需求扩展：** 情境与反应、健康／超自然、经济／经营、环境与其他资料片。自主候选评分作为可选诊断项，保持先前“触发来源即可”的决定。

连续数值曲线、场外角色持续跟踪、跨读档历史自动拼接、推测动机／心理活动均不属于本次补全默认范围。本次只更新调研与版本状态文档，没有新增采集代码，没有构建、安装或启动游戏。
