# 数据语义化模块

版本：0.4.0。日期：2026-09-15。游戏资源名称与动态参数解析已实现，完成离线回归、旧日志核对和本地游戏 protobuf 兼容检查；本版尚未安装或实机验收。结果见[语义解析验证记录](validation/2026-09-15-semantic-resolution.md)。

0.6.0 增加生活事件类别说明、原始 payload、动作关联效果摘要及明确的待解释标记；广播回调、技能后备通知和里程碑补授不扩写成已证明的玩法结果。新增类别见[覆盖说明](event-coverage-0.6.0.md)。这仍是确定性表达，语义未知时保留原数据。

## 1. 名称和事件描述各自从哪里来

中文输出仍是确定性转换，不调用大语言模型。游戏名称优先来自 **EA 运行时名称接口 → LocalizedString 的 hash 与 tokens → 中文 STBL**；事件的排队、运行、结束、触发来源和已观测前后状态则由项目规则组织成逐条说明。

0.3.2 已能查询不含参数的 STBL 文本，但只保留 hash，遇到动态模板便回退到内部标识。0.4.0 同时保存和解析动态参数，并补齐不同资源的名称入口。新增内容依据游戏接口和字段，未根据样本中的英文片段批量编写翻译。

| 数据 | 名称来源 | 参数与处理 |
| --- | --- | --- |
| 交互 | 实例 `get_name(target=..., context=...)` | 接收 EA 处理后的名称及参数；调用异常才尝试队列名称／显示名称和 `get_localization_tokens`，保留错误 |
| Buff | `buff_name` | 传入所属 Sim；保留可见性 |
| 关系标记 | `display_name` | 传入关系双方，沿用 EA 调用顺序 |
| 需求资源 | `stat_name` | 从已加载的统计量类型读取 |
| 配方 | `get_recipe_name(...)` | 从制作流程读取配方，传入已知制作者／付款人 |
| 情绪 | `mood_names[intensity]` | 有已观测强度时选对应名称；强度未知时仅使用基础名称，source 标注 base_mood_name，不补造强度 |
| 事件原因中的交互 | `get_name(target=..., context=...)` | 复用交互实际名称入口，不把交互当作普通物件 |
| 物件状态类型／值 | `display_name` | 使用游戏 display mixin；没有名称时才尝试原有精确别名 |
| 物件实例 | `LocalizationHelperTuning.get_object_name(obj)` | 通过实例名称组件和目录文本读取，支持自定义名称；失败保留目录回退与原因 |

样本中的 `relbit_SocialContext_Casual → 随意的谈话`、`friendship-acquaintances → 相识`、`familyTrope_difficult → 困难` 来自资源指向的中文文本。旧手写“不喜欢”也可按官方名称改为“被讨厌”。运行时读取实际已加载的 tuning；STBL 来自构建时输入的中文词表，并不自动加载其他 MOD 新增的字符串表。

## 2. 动态文本解析

实现位于 `src/context_overlay/localization.py`。适配器在游戏线程上将 LocalizedString 复制为普通 JSON 数据，转换器仅消费这些数据和词表，不持有游戏对象引用。

当前支持姓名、物件名称／描述、原始文本、嵌套字符串、数值，以及有明确性别且无自定义代词时的简单 M/F 分支。例如 `和{1.SimFirstName}聊天` 和 `吃{1.ObjectName}`，使用 **游戏返回的 token 位置**，不假设所有交互都以 actor/target 为第 0/1 个参数。`和{0.String}闲聊` 可以继续展开游戏提供的嵌套文本。

下面是合成参数示例，不是一条真实采集的历史：

```json
{
  "text": "和小明聊天",
  "status": "resolved",
  "hash": "0x482BA41C",
  "template": "和{1.SimFirstName}聊天",
  "localization": {
    "hash": "0x482BA41C",
    "tokens": [
      {"type": "INVALID"},
      {"type": "SIM", "first_name": "小明", "last_name": "张", "is_female": false}
    ]
  }
}
```

客户端拥有完整的本地化语法。本版不完整支持 SIM_LIST、金额／日期语法、自定义代词和姓名前缀等格式；无法确认的部分显示 `〈未解析：表达式〉`，同时保留模板、参数和原因，不标记为完整成功。原始用户文本不再作为模板执行。protobuf token 类型优先读取运行环境的枚举描述，避免把新版编号按旧参考源码误读。

采集最多保留 8 层结构、每层 64 个参数、256 个有效结构节点及 16,384 个文本字符；渲染另有共享工作预算及输出长度上限。截断和超限均留下原因。参数证据会增加事件大小，既有记录内存和磁盘预算继续生效，200,000 条仍是数量上限，不是容量保证。

## 3. 状态、回退和事实边界

| 名称状态 | 含义 |
| --- | --- |
| `resolved` | 已用词表和所需参数完整解析当前支持的表达式 |
| `raw_text` | 游戏直接提供的文本 |
| `empty_display_name` | 有字符串键但解析文本为空；保留证据，以原始资源名兜底，不展开整段 token 字典 |
| `unresolved_tokens` | 已取得模板，仍缺参数或语法支持；可读部分照常显示 |
| `no_display_name` | 运行时未提供有效名称 key，或离线参考 tuning 未配置名称；以 `reason` 和 `source` 区分 |
| `unmapped` | 字符串 key 未收录、名称读取失败或结构超限等，保留具体原因 |
| `rule_resolved` | 原有少量精确别名，要求 ID 与 tuning 名共同匹配，原结果留在 `raw_name` |

隐藏 Buff 和内部动作可能本来就没有玩家可见名称。`visible=false` 独立保存，不删除记录，也不因名字中含有 `Hidden` 就推断可见性。离线参考缺少名称不证明当时没有动态覆盖；界面显示“参考资源未配置显示名称”，来源带 `runtime_override_not_verified=true`。

通用资源读取通过已核验的 buff_name、stat_name、mood_names、get_recipe_name、trait_type 等资源字段选择命名入口；尚无可靠入口时以 `unmapped/no_verified_name_accessor` 表达，不宣称游戏未配置名称。实体引用仅接受 Sim 身份和真正的 BaseObject 实例，拥有 id 的广播器／资源／交互不自动成为物件。首局修正细节见[验证记录](validation/2026-09-15-event-quality-fixes.md)。

事件解释仍只表达已观测事实：自然结束不能扩写成“做饭成功”“吃饱”或“关系改善”；状态变化不能仅凭时间接近归因于某次行为。名称解析不会改变事件身份、阶段、参与者、数值、时间或结果。0.6.0 删除了连续数值采样区间的专用解释，当前数值由 Context 读取。

解释按 subject／target／initiator 等角色组织主语，不能把全部索引关联实体当成效果接收者。制作描述保留“游戏报告制作产物”的通知边界；库存描述使用真实容器、数量和拆分产物；情绪种类与强度分别显示。目标／阶段完成通知默认使用中性名称并注明 aspiration_type，不把 whim set 或 utility 目标说成玩家抱负完成。

## 4. 重新解释旧日志

`scripts/build_name_catalog.py` 从本地 `sims4-python/data/tuning/combined_tuning_*.xml` 生成带类型的名称索引，解析共享节点引用和显示名称变体。匹配要求 **资源类型 + ID + 原 tuning 名** 一致；冲突条目排除。索引记录原文件 SHA-256 和名称字段，明确排除 2014 年的 `combined_tuning_BASEFull.xml`。

`NameCatalog` 优先使用已记录的本地化证据，其次使用旧记录中的非零 hash，最后才查询静态索引。不会从当前人物或物件状态补写过去的参数。旧记录中的 `sim_Chat` 可恢复为 `和〈未解析：1.SimFirstName〉聊天`，无法恢复当时已丢弃的人名参数。

`translate(packet, catalog=...)` 返回独立结果：原 `snapshot/history/target` 保留；资源解释放入 `semantic_view`，中文输出在 `rendered`，并登记索引依据。输入文件不改写。生成和使用方法见[运行说明](runtime-usage.md)。全量索引和 EA 中文词表仅保留在本机，不提交 Git；运行中的 MOD 直接读取已加载的资源，不加载这个离线索引。

## 5. 后续核验

新版本的下一次实机检查应同时核对：聊天对象姓名、吃饭／阅读的目标名称、自定义物件名称、关系标记和隐藏资源说明，并查看导出中的 hash/tokens 与游戏 UI 是否对应。不同 DLC/MOD 的名称覆盖、复杂 token 和长时负载仍需实测。

核心转换不依赖控制台、文件写入或模型调用，游戏窗口、事件导出和其他 MOD 可复用。稳定公共 SDK、活动聚合与 LLM 叙事不属于此次名称解析改动。总体边界见[三模块设计](modular-context-provider.md)，EA 依据见[接口登记](runtime-interfaces.md)。
