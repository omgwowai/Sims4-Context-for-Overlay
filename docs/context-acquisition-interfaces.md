# Context 的技术获取方式与接口分类

版本：v0.1。日期：2026-09-11。

本文按“采集代码接入哪里、怎样取得数据”分类，用于选择采集适配器。前一份[运行时内容分类](runtime-context-taxonomy.md)描述数据的游戏含义；同一类内容通常需要组合多种技术入口。

接口名来自[当前参考版本](reference-baseline.md)的源码核对。本轮没有运行游戏；以下游戏内部 Python 接口不代表 EA 承诺兼容的公开 SDK，也不能在普通外部 Python 进程中直接 `import services` 使用。

## 1. 技术分类总览

| 编号 | 获取方式 | 主要入口/接口 | 取得的数据形态 | 执行位置 |
| --- | --- | --- | --- | --- |
| T01 | 运行时对象查询 | `services` → manager → SimInfo / tracker / component / getter | 当前状态、实体集合、已暴露的计算结果 | 游戏内 Script Mod |
| T02 | 事件与变化订阅 | `EventManagerService.register`、stat watcher/listener、对象/zone callback | 事件通知、部分前后值、阈值到达、生命周期通知 | 游戏内 Script Mod |
| T03 | 业务函数 Hook | 包装交互、Loot、Buff、Situation 等真实执行函数 | 调用参数、返回结果、执行前后值、调用关系 | 游戏内 Script Mod |
| T04 | GSI 与既有调试归档 | `archive_interaction`、`archive_autonomy_data`、GSI archiver | 已整理的交互归档、候选、评分、诊断明细 | 游戏内采集，再导出 |
| T05 | Tuning 与资源读取 | instance manager、`sims4.resources`、DBPF / STBL 解析 | 定义、规则参数、名称、资源身份与文本 | 游戏内或离线工具 |
| T06 | 持久化状态读取 | `PersistenceService.get_*_proto_buff`、存档数据解析 | 已维护/已保存的 Sim、家庭、地块等持久化表示 | 游戏内；离线解析需专用工具 |
| T07 | Python 执行跟踪 | `sys.setprofile`、线程 profile、调用帧；必要时单独评估 `sys.settrace` | 调用路径、参数摘要、返回值、部分临时上下文 | 游戏内，通常限诊断窗口 |
| T08 | MOD 自有数据与协作接口 | 自定义 provider、JSONL/JSON、已有本地 HTTP/SSE 服务 | 已记录经历、画像、Overlay 历史、第三方 MOD 数据 | 游戏内外协作 |
| T09 | Native / 客户端补充观测 | Native 专项插桩、客户端接口、同步消息观测、截图/视觉解析 | Python 未暴露的状态，或实际画面/UI 信息 | 客户端或 Native/外部工具 |

这是**工程接入分类**，不是互斥的数据集合。例如，T04 的 GSI 数据常通过 T03 的包装方法截获；一个底层调用 C++ 的普通 getter 仍可以通过 T01 使用。记录采集来源时，建议同时保存“数据面”和“接入机制”，而不是把不同入口看到的同一次发生计成多个事件。

这些分类也不全是“游戏本体已经保存好的数据”：运行时观察需要 MOD 留存才能形成历史，T08 还包含派生记忆和 Overlay 自有内容，T09 部分能力需额外开发。Experience 各类事件的具体归属，以及能否用于状态重建、日回顾和在线生成，见[状态与经历应用说明](state-history-and-generated-events.md)。

## 2. T01：运行时对象查询

**方式：**在游戏允许访问模拟状态的时点，通过服务定位实体，再读属性、tracker 或 component。适合初始快照、按需查询和定期对账。

| 层级 | 已核对的入口示例 | 作用 |
| --- | --- | --- |
| 服务 | `services.sim_info_manager()`、`services.object_manager()`、`services.inventory_manager()` | 定位 SimInfo、已加载地块对象、库存对象管理器 |
| 场景 | `services.current_zone()`、`services.time_service()`、`services.get_zone_situation_manager()` | 当前地块、模拟时钟和情境管理器 |
| 实体枚举 | SimInfo manager 的 `get_all()` / `instanced_sims_gen()`；object manager 的 `get(id)` | 全部已管理 SimInfo、已实例化 Sim 或指定对象；各自范围不同 |
| 统计量 | `tracker.get_statistic(stat_type, add=False)`、`tracker.get_value(stat_type, add=False)` | 按类型读取统计对象或数值 |
| 关系 | `relationship_tracker.get_relationship_score(target_sim_id, track=...)`、`get_all_bits(...)`、`get_knowledge(target_sim_id, initialize=False)` | 关系状态及角色已知信息 |
| 对象组件 | `StateComponent.has_state(state)`、`get_state(state)`；库存组件、Buff 组件 | 物件状态、库存和当前效果；组件方法可能导出到宿主对象 |

**能得到：**现在是什么状态；当前有哪些对象；某个已暴露查询的结果。

**不能单独得到：**期间发生过的全部变化、每次变化的原因、从未实例化或未暴露的数据。

**接口细节：**

- `get_all()` 的 SimInfo 与 `instanced_sims_gen()` 的 Sim 不同；库存管理器与地块对象管理器也不能混为一个枚举范围。
- 本版本 `BaseStatisticTracker.get_value()` 在找不到 stat 时返回类型默认值。默认值应注明来源，不能伪装成已观测到的实例值。
- `add=False` / `initialize=False` 可以避免部分显式创建路径，但 getter 仍可能有惰性恢复、缓存或计算行为；本版本 `get_statistic()` 就包含恢复逻辑。
- 快照要记录时点、枚举范围及失败项。周期轮询可以发现净变化，不能证明两次快照间没有先增后减。

**依据：**[服务入口](../../sims4-python/ea-source/EA/simulation/services/__init__.py)、[统计 tracker](../../sims4-python/ea-source/EA/simulation/statistics/base_statistic_tracker.py)、[关系 tracker](../../sims4-python/ea-source/EA/simulation/relationships/relationship_tracker.py)、[Observer 状态采集](<../../sims4-python/ea-source/My Script Mods/autonomy_observer/Scripts/autonomy_observer/state_capture.py>)。

## 3. T02：事件与变化订阅

**方式：**使用游戏已有的通知机制登记处理器，在事件发生或值变化时接收数据。这一类不需要替换被观察的业务函数。

| 通知类型 | 注册/撤销接口 | 回调拿到什么 |
| --- | --- | --- |
| TestEvent | `services.get_event_manager().register(handler, event_types)` / `unregister(...)`；另有 `register_single_event`、`register_with_custom_key` | 调用 `handler.handle_event(sim_info, event_type, resolver)`；payload 通过 resolver 读取 |
| 统计量变化 | `tracker.add_watcher(callback)` → handle；`remove_watcher(handle)` | 本版本 watcher 签名为 `(stat_type, old_value, new_value)` |
| 统计量阈值 | `tracker.create_and_add_listener(stat_type, threshold, callback, ...)` / `remove_listener(listener)` | 阈值监听通知；具体回调契约需按统计类型核验 |
| 物件状态 | `StateComponent.add_state_changed_callback(callback)` / `remove_state_changed_callback(callback)` | 组件发出的状态通知；具体参数按调用处核验 |
| Zone 生命周期 | `zone.register_callback(callback_type, callback)` / `unregister_callback(...)` | 对应 zone 阶段的通知；本版本可能在注册时立即调用已达到阶段的 callback |

当前 `TestEvent` 枚举中可以看到 `InteractionComplete`、`AddRelationshipBit`、`RemoveRelationshipBit`、`BuffBeganEvent`、`BuffEndedEvent`、`RelationshipChanged`、`KnowledgeChanged` 等。枚举存在只是候选入口，还要核对哪些代码会发送、发送条件是什么、payload 是否够用。

**适合：**已提供通知的状态增量、阈值事件、知识变化和生命周期。

**边界：**

- 事件总线只包含实际发送的事件；watcher 只覆盖经过对应通知路径的变化，不能代替全部写入的审计。
- 通知不一定带直接原因，有时需要与 T03 的调用上下文或 T01 的状态关联。
- 注册时机、对象重建、zone 切换、重复注册和注销是采集器必须处理的生命周期问题。Zone callback 也不能默认当成永久订阅。
- 阈值 listener 的建立可能涉及 stat 初始化或 listener seed；需要核验它是否会改变被观察对象的状态。

**依据：**[事件管理器](../../sims4-python/ea-source/EA/simulation/event_testing/event_manager_service.py)、[TestEvent 枚举](../../sims4-python/ea-source/EA/simulation/event_testing/test_events.py)、[统计 tracker](../../sims4-python/ea-source/EA/simulation/statistics/base_statistic_tracker.py)、[状态组件](../../sims4-python/ea-source/EA/simulation/objects/components/state.py)、[Zone](../../sims4-python/ea-source/EA/simulation/zone.py)。

## 4. T03：业务函数 Hook

**方式：**保存原函数并安装包装器，在真实调用前后采集信息；生成器则需要按 yield 生命周期处理。用于补齐没有合适通知的入口，或取得事件总线不提供的参数、结果和调用关系。

| Hook 位置 | 已有采集示例 | 可补充的信息 |
| --- | --- | --- |
| Buff 增删 | `BuffComponent.add_buff` / `remove_buff` | Buff 类型、handle、来源参数、效果生命周期 |
| Loot 容器 | `LootActions.apply_to_resolver`、`get_loot_ops_gen` | resolver、容器、嵌套来源与实际 op |
| Loot 操作 | 各具体 op 的 `_apply_to_subject_and_target` | subject/target、操作参数、调用结束时可读取的结果 |
| 情境成员 | `BaseSituation._on_add_sim_to_situation` / `_on_remove_sim_from_situation` | Situation 实例、加入离开与成员身份 |
| 状态写入或交互边界 | 经过核验的 setter、生命周期方法、选择/推送入口 | 真正的 before/after、执行阶段、嵌套调用关联 |

Experience 提供了自定义工具 `injector.observe`、`injector.wrap`、`injector.wrap_generator`。它们是参考 MOD 的包装实现，不是游戏官方注册 API。其中 `observe` 在原函数正常返回后调用观察者，原函数抛异常时不会经过该观察回调。

**适合：**实际调用的证据、显式参数、来源传播、操作前后值和没有专用 callback 的变化。

**边界：**

- Hook 基类不一定覆盖子类重写；安装以后新定义的类、其他 MOD 替换函数等路径都需检查。
- 普通函数、生成器、descriptor 和装饰器包装的方法不能默认使用同一种注入方式。
- 相同调用链可能多次经过包装点，需要传播调用身份和区分层次。时间窗去重不能可靠识别独立的连续发生。
- 返回 `None` 不能自动证明成功或空转；需要核验返回语义或读取实际前后状态。
- 读取参数引用后再延迟序列化，值可能已经变化；应在回调中提取必要的稳定字段，并保持原函数行为与异常语义。

**依据：**[Experience injector](../../Sims4-Experience-Mod/src/experience_recorder/injector.py)、[Loot/Buff hook](../../Sims4-Experience-Mod/src/experience_recorder/hooks/loot_hook.py)、[Situation hook](../../Sims4-Experience-Mod/src/experience_recorder/hooks/situation_hook.py)。

## 5. T04：GSI 与既有调试归档

**方式：**复用游戏已经为调试收集的结构化数据。可以在归档入口包装转写，也可以在已确认支持的环境读取归档；需要另外持久化历史。

| 数据 | 当前源码中的入口 | 典型内容 |
| --- | --- | --- |
| 交互归档 | `gsi_handlers.interaction_archive_handlers.archive_interaction(sim, interaction, status)` | 交互状态、参与者、退出原因、取消调用栈等已收集信息 |
| 自主决策归档 | `gsi_handlers.autonomy_handlers.archive_autonomy_data(sim, result, mode_name, gsi_data)` | 候选、对象、需求、评分、概率表、部分选择结果 |
| 开关管理 | `sims4.gsi.archive.set_archive_enabled(archive_type, enable=True)` | 对已注册 archiver 开启数据归档；用 `is_archive_enabled` 核对状态 |

本地源码的交互模块名是 `interaction_archive_handlers`。Experience 的 `_ia_module()` 先导入它，再尝试旧名 `interaction_archive`；文档中不能把旧名字直接当成所有版本都存在的模块。

**适合：**快速取得较丰富的交互/决策上下文，检查业务采集的解释能力。

**边界：**

- 数据生成可能受 archiver 开关门禁控制。当前源码区分 `interaction_archive` 与 `interaction_archive_mixer`，应分别检查；`autonomy` 又是另一归档。
- 内存归档有记录数限制。本版本默认设置为 50，但具体保留量受 archiver 配置影响；它不是完整历史日志。
- 调试 schema、模块名、启用方式和返回字段都可能随版本改变，字段还可能已经字符串化或聚合。
- GSI 没记录的内容，不能从这个接口补回来。Observer 自建的 DecisionCase/Test Readset 也不应全部标成游戏原生 GSI 数据，它们还组合了 Hook 和快照。

**依据：**[交互归档](../../sims4-python/ea-source/EA/simulation/gsi_handlers/interaction_archive_handlers.py)、[Autonomy 归档](../../sims4-python/ea-source/EA/simulation/gsi_handlers/autonomy_handlers.py)、[GSI archive](../../sims4-python/ea-source/EA/core/sims4/gsi/archive.py)、[Experience 兼容处理](../../Sims4-Experience-Mod/src/experience_recorder/hooks/interaction_hook.py)。

## 6. T05：Tuning 与资源读取

**方式：**读取当前加载的定义，或解析游戏和 MOD 的资源包。为运行时 ID 补充名称、类型、规则和配置含义。

| 入口 | 已核对的接口/工具 | 取得什么 |
| --- | --- | --- |
| 已加载 tuning | `services.get_instance_manager(resource_type).get(id_or_key)`；manager 的 `types` | 当前已加载的 tuned class 及其字段 |
| 资源管理器 | `sims4.resources.get_resource_key(...)`、`get_all_resources_of_type(...)`、`ResourceLoader(key).load()` | 资源键、枚举结果、资源内容 |
| DBPF 包解析 | 参考工具 `dbpf.read_index(path)`、`read_resource(path, entry)` | `.package` 索引及资源字节 |
| Tuning / 本地化解析 | `tools/extract_tuning.py`；`stbl.parse_stbl(bytes)`、`load_language(...)` | tuning XML 和 STBL 文本表 |

**适合：**解释 Buff/交互/物件的 ID，提供规则条件与参数，建立可缓存的定义目录。

**边界：**

- instance manager 要等待对应 tuning 加载；`get()` 取得的是定义，不是场景里的物件或 Sim 实例。
- 离线资源必须解析包覆盖、删除标记、语言和 DLC/MOD 版本；磁盘上的某份 tuning 不一定是当前生效的值。脚本 MOD 还可能在加载后修改定义。
- 本地化字段可能带 token，原始 STBL 模板不一定等于客户端最终显示文字。
- 配置的效果数值和实际效果分开：实际结果可能经过条件、倍率、上限或其他 MOD 修改。

**依据：**[InstanceManager](../../sims4-python/ea-source/EA/core/sims4/tuning/instance_manager.py)、[resources](../../sims4-python/ea-source/EA/core/sims4/resources.py)、[DBPF 工具](../../sims4-python/tools/dbpf.py)、[Tuning 工具](../../sims4-python/tools/extract_tuning.py)、[STBL 工具](../../sims4-python/tools/stbl.py)。

## 7. T06：持久化状态读取

**方式：**通过游戏持久化服务读内存中的存档数据结构，或使用另行验证的解析器读取磁盘存档。

已核对的游戏内入口：

```text
services.get_persistence_service()
  .get_save_game_data_proto()
  .get_save_slot_proto_guid()
  .get_save_slot_proto_buff()
  .get_sim_proto_buff(sim_id)
  .get_household_proto_buff(household_id)
  .get_zone_proto_buff(zone_id)
```

**适合：**获取已保存在持久化表示中的角色、家庭和地块信息，补充尚未实例化对象的部分资料，定位存档身份，建立加载时基线。

**边界：**

- `get_*_proto_buff` 的结果是持久化服务当前维护的数据结构，不能默认认为与当前 live 对象的每个字段同步，也不必然等于磁盘文件最后一次写入的内容。
- 不是所有运行状态都会进入存档；历史顺序和因果尤其不能从终态恢复。
- 游戏内 protobuf 对象读取与磁盘 `.save` 的容器解码是两项工作。本轮核验了前者接口，没有验证一套可直接使用的完整离线存档解析方案。
- 只提取需要的字段，避免修改共享 proto；加载/保存边界、存档标识、另存与回滚分支需要分别记录。

**依据：**[PersistenceService](../../sims4-python/ea-source/EA/simulation/services/persistence_service.py)、[Experience 地块名称读取](../../Sims4-Experience-Mod/src/experience_recorder/hooks/zone_hook.py)。

## 8. T07：Python 执行跟踪与调用帧

**方式：**在解释器执行层观察调用，而不是为每个业务函数单独编写包装器。

Observer 的 `tracer.py` 使用 `sys.setprofile` 和 `threading.setprofile`，并从 `frame.f_code`、`frame.f_locals` 等提取函数身份和参数摘要，记录调用/返回及 Python 可见的 C 调用边界。

**适合：**发现真实执行路径、寻找 Hook 位置、短窗口检查 Hook 是否漏掉调用、调查临时参数和返回值。

**边界：**

- profile 的 call/return 事件不能自动覆盖每次局部变量赋值和分支；逐行/操作级或 Python exception 跟踪需单独评估 `sys.settrace` 等机制。源码中存在处理 `exception` 的分支，不等于 `setprofile` 会产生所有这类事件。
- `c_call/c_return/c_exception` 只能说明 Python 可见的 C 调用边界，不能直接取得 C++ 栈内全部参数、返回值或内部状态。
- 当前 Observer 对参数做摘要，包含深度/长度限制；不能把 trace 当作完整内存快照。
- 跟踪仅覆盖实际执行、已安装 profile 的线程与时间窗口；`threading.setprofile` 对既存线程的覆盖不能默认成立。
- 通常开销较高，优先用于诊断和验证，再把稳定的必要字段下沉为 T01–T04 的定向采集。

**依据：**[Observer tracer](<../../sims4-python/ea-source/My Script Mods/autonomy_observer/Scripts/autonomy_observer/tracer.py>)、[采集模式说明](<../../sims4-python/ea-source/My Script Mods/autonomy_observer/README.md>)。

## 9. T08：MOD 自有数据与协作接口

**方式：**读取已由 MOD 保存的数据，或由拥有该数据的 MOD 提供快照/事件接口。适合采集器之间协作，以及把采集结果送到游戏外。

| 入口 | 现有实例或拟议接口 | 状态 |
| --- | --- | --- |
| 事件文件 | Experience 的 `events/<slot>/<sim>.jsonl` | 已有，实现的是其采集范围内的事件 |
| 快照/派生文件 | `profiles/<slot>/*.cards.json`、`health/session.json` | 已有，包含画像和采集健康信息 |
| 本地查询 | Experience Debug Server 的 `/api/events`、`/api/event/<id>`、`/api/cards`、`/api/health`、`/api/slots` | 已有外部服务路由；需要服务运行，它们不是游戏本体接口 |
| 增量传输 | Debug Server 的 `/api/stream` | 已有 SSE 路由；恢复边界需要按实现核验 |
| 协作 provider | 由 MOD 提供 `get_snapshot` / `subscribe` / `export_since` 一类契约 | 建议的接口形态，本项目尚未实现，也不是通用 Sims 4 API |

**适合：**经历和印象复用、Overlay 连续状态、第三方 MOD 私有数据，以及向 LLM 服务提供查询结果。

**边界：**

- 读取 JSONL 或 HTTP 只能取得上游实际保留的内容，无法补回上游过滤掉的事件。
- JSONL、HTTP、SSE、IPC 是传输/消费方式。最初事实可能来自 T01/T02/T03/T04，原始来源应继续保留。
- 需要版本、命名空间、事件 ID、水位、分页和重复消费约定。当前 `/api/events` 默认 limit 为 500，返回一批结果不能代表完整历史。
- 画像、推断和原始事件分开读取。第三方 MOD 不提供接口时，要针对它实际使用的游戏状态或私有实现另做适配，不能假定存在统一查询 API。

**依据：**[Experience writer](../../Sims4-Experience-Mod/src/experience_recorder/writer.py)、[Debug Server 路由](../../Sims4-Experience-Mod/tools/debug_server.py)、[Experience README](../../Sims4-Experience-Mod/README.md)。

## 10. T09：Native / 客户端补充观测

这一类用于补足 Python 层尚未暴露的信息，按下面三种情况分开评估。

| 情况 | 接入方式 | 能说明什么 |
| --- | --- | --- |
| 已有 Native Python 包装 | 如 `routing.test_connectivity_batch(...)`、`estimate_path_batch(...)` 最终调用 `_pathing` | 通过 T01 取得已暴露的查询结果；不需要因此就做二进制插桩，但查询行为/开销要核验 |
| 模拟器 → 客户端同步 | 在 `Distributor.add_op`、`_send_view_updates` 等边界定向观察；机制属于 T03 | 模拟器准备或已经发送的状态投影/操作，不等于客户端已实际渲染 |
| 未暴露 Native 状态或实际 UI/画面 | 专项 Native 插桩、可用的客户端接口，或同步截图、OCR/视觉解析 | 所选底层状态或屏幕观测；需独立开发验证，现有 Script Mod 未完整提供 |

**适合：**Python 接口不足以回答的问题，以及依赖屏幕位置、镜头、UI 实际展示的 Overlay。

**边界：**内部 IPC、protobuf ViewUpdate、C ABI 和 `c_api_*` 不是一个现成的外部 Context REST API。它们的方向、生命周期和字段覆盖必须分别核对。截图描述属于视觉观测或推断，不具备运行时结构化数据的完整性，也不能替代“某 Sim 已知道什么”的记录。

**依据：**[routing 包装](../../sims4-python/ea-source/EA/simulation/routing/__init__.py)、[Distributor](../../sims4-python/ea-source/EA/simulation/distributor/system.py)、[客户端架构](../../sims4-python/docs/client-architecture.md)、[五条接缝契约](../../sims4-python/docs/seam-contracts.md)。

## 11. 游戏内接口与 LLM 接口的关系

建议将边界组织为：

```text
游戏内：服务查询 / 通知 / Hook / GSI / tuning / persistence
    ↓ 提取必要字段，记录实体、时间、来源与采集状态
采集记录：快照 + 事件 + 定义引用 + 缺口/水位
    ↓ 文件或已设计的 IPC/网络传输
游戏外：存储、去重、历史重建、规则/资源解析、按需查询
    ↓ 结构化 Context
LLM / Overlay
```

这是建议架构，尚未实现。游戏内只在经核验的模拟时点访问对象；导出稳定的数据副本，游戏外处理存储、检索和模型请求。避免把游戏对象引用交给外部工作线程读取，也不让同步 LLM 请求阻塞模拟回调。

传输时保留 64 位 ID 的字符串表示、schema/版本、session/存档分支、发生与采集时点，以及缺失/默认/截断标记。文件或 HTTP 接通，只能证明数据通路连通；采集覆盖仍要单独验证。

## 12. 选型与接口登记建议

| 具体需求 | 建议组合 |
| --- | --- |
| 当前饥饿值及变化 | T05 定位 statistic 定义；T01 初始值；T02 watcher/阈值；必要时 T03 补来源；T01 对账 |
| 关系状态和“为何改变” | T01 关系快照；T02 相关事件；T03 实际效果/写入及调用上下文 |
| 交互完成、取消及参与者 | T04 既有归档；T03 补开始/中断等缺口；T01 读取当前队列和运行交互 |
| 自主选择的依据 | T04 决策归档 + Observer 定向 T03/T01；T05 解释规则；T07 短窗口核验 |
| 未实例化 Sim 的已保存资料 | T01 SimInfo + T06 持久化表示；分别注明 LOD 和数据时点 |
| 物件/交互名称与效果配置 | T05；若要知道本次真实效果，再用 T02/T03 |
| 长期记忆和 Overlay 上轮内容 | T08；记忆保留其底层 T01–T06 证据和派生标记 |
| 屏幕上实际显示了什么 | 按需要接入 T09；T03 同步消息可辅助，但不能独立确认显示结果 |

建议优先核验 **T01 查询 + T02 通知**；没有合适通知或需要来源链时补 **T03 Hook**；**T04 GSI** 复用丰富诊断数据；**T05/T06** 补定义和持久化基线；**T08** 组织游戏外消费。**T07/T09** 按具体缺口使用。

每个采集项下一步应登记：技术类别、完整模块/类/方法名、目标版本、运行时机、输入参数、返回/回调字段、枚举范围、副作用/默认值语义、生命周期与解除方式、来源身份、验证状态。事件项还应登记发送条件和与其他入口重合时的关联规则。

## 迭代记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-09-11 | v0.1 | 从获取方式与接口建立 9 类技术分类，核对具体源码入口，区分游戏内采集与游戏外传输。 |
