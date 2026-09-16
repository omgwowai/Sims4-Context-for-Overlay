# 游戏资源语义目录与运行时文本

本轮继续使用 MOD 0.6.0、API／SDK 1.1.0、schema 1。新增字段兼容原有消费方式；实现及验证范围见[验证记录](validation/2026-09-15-resource-semantics.md)。

2026-09-16 补充可读资源详情、缺失参数诊断和部分数值／时间格式，见[补充验证](validation/2026-09-16-semantic-supplements.md)。该构建已安装并取得[首局实机数据](validation/2026-09-16-semantic-supplements-first-live.md)，确认描述进入导出；Buff 与聊天参数仍待完善，界面视觉及新增数值／日期格式未在本局验收。

首局后的[参数修复](validation/2026-09-16-semantic-parameter-fixes.md)已通过测试和输入回放，尚未安装。下述持有者绑定和 UI 名称回退行为属于该新构建。

## 文本依据

资源名称与描述来自本机游戏资源，不按英文 tuning 名猜译，不使用 LLM。`scripts/build_resource_catalog.py` 直接读取安装目录中的官方 STBL 与 combined tuning，使用 `sims4-python/tools/dbpf.py` 和 `extract_tuning.py` 的只读解码器；输出中记录游戏版本、解码器／字段映射摘要和配置来源。

先按 `Resource.cfg`／`ResourceClient.cfg`／`ResourceSimulation.cfg` 的 `Priority` 选择**同一完整 TGI（type、group、instance）** 的资源。优先级相同且内容不同则保留冲突。之后才合并已选 STBL：不同有效 TGI 使用同一字符串键且文本不一致时，不以路径顺序选赢家。被整个资源覆盖的旧键不会从旧包补回来。

这是“本机已安装官方资源”的目录，不证明运行时启用了所有资料片，也不自动识别其他 MOD 的同键文本覆盖。运行时名称字段仍来自实际已加载的对象／tuning。遇到第三方 MOD 提供的新 hash，可能出现 `string_key_missing`；同键覆盖尚不能自动确认，`string_source.third_party_overrides` 明确为 `not_verified`。

## 三个文本角色

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

## 模板支持与缺口

在原有姓名、普通数值、嵌套字符串、物件名称／描述、简单 M/F 分支之上，新增 `ObjectCatalogName`、`ObjectCatalogDescription`、Sim token 的 `ObjectName` 别名及小写 m/f 分支。目录名称／描述不会被自定义名称替换。修复空自定义代词槽 `|||||` 的处理；真实自定义代词或中性分支仍保留未解析。

日期 token 的原始字段和 SIM_LIST 中无独立 type 的子项能正确保留。整数 `Money` 现按“1234 模拟币”表达，`TimeShort` 使用 24 小时 `HH:MM`，`DayOfWeekShort/Long` 支持 EA 游戏日期中的“周一／星期一”。星期要求 month/year 均为零、date 为 1–7（星期日为 7），依据 `DateAndTime.populate_localization_token`。返回 `format_profile=context_overlay_zh_CN_v1`，表示项目的确定性中文格式，不承诺与客户端逐字符一致。

小数金额不猜取整方式；日期自定义格式 hash、真实日历日期、`DateShort/TimeLong`、列表、`T/DAE` 年龄条件、姓名前缀和复杂代词仍明确未解析。现有逆向资料尚未给出全部客户端规则，不能把 Sim 参数强制改成 String 来填充聊天对象。

`unresolved` 增加 `category`，区分 `parameter_evidence`（缺参数、参数不完整、类型不匹配、缺字段或无效值）、`grammar_support`（未支持的表达式／格式）、`budget` 和 `resource_or_format`。类型不匹配另附参数位置、实际类型及期望类型；`status=unresolved_tokens` 保持兼容。审计分别统计未解析文本出现次数与表达式类别，同一文本可能包含多个表达式。离线重解释保留 `no_verified_name_accessor`，不再把没有已验证读取入口改记为无名称 key。

解析继续受深度、节点、token 数和字符预算限制。模板不支持、缺字段和超限都保留具体原因，不能标记为成功。无参数静态文本也保存 template，便于消费者核对。

## 下游调用

公共入口不变。`get_api_info()` 新增能力 `text.resource_details` 和 `resource_text` 说明。SDK 1.1.0 直接透传这些可选字段，无需升级调用方式：

```python
from context_overlay_client import Client

client = Client()
packet = client.get_context(fields=["identity", "buffs"], include_history=False)
buff_field = packet["snapshot"]["buffs"]
if buff_field["status"] == "available":
    for buff in buff_field["value"]:
        title = buff["name"].get("text")
        detail = buff.get("description")
        if detail and detail["status"] in ("resolved", "raw_text"):
            # 交给自己的 Overlay；失败／未解析状态另行展示。
            text = detail["text"]
```

接口仍需在游戏模拟线程调用，返回数据不持有游戏对象引用。窗口列表继续显示简短名称；进入状态条目或事件详情后附加已取得的资源说明，沿用正文分页。条件提示明确尚未判断条件，基础情绪说明明确未判断年龄／特征覆盖。

`rendered.resource_details` 是可直接展示的补充说明：包含 `items`、`truncated`、`limit`。每项记录资源身份、`label`、`role`、`text`、`status`、`basis`、属性／变体索引及 `evidence_ref`。相同内容重复出现在事件原因和 metadata 时只展示一次；每包最多 128 项，检查节点最多 20,000，超出会报告截断。单个游戏详情页最多 32 项。未解析模板仍标记未完整解析，空描述不生成说明。

运行时读取、未验证条件的 tooltip、基础情绪说明和静态参考分别标记来源。离线重解释的引用指向 `semantic_view.snapshot/history/target`，普通运行时引用指向原包；静态名称只作为参考名称出现，不覆盖当时未取得的名称。

## 构建与离线使用

仓库根目录执行（PowerShell）：

```powershell
$py = "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe"
& $py scripts/build_resource_catalog.py --game "D:/Games/The Sims 4" --reference C:/sources/sims4-python
& $py scripts/build.py --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json
```

输出位于 `.local/resource-semantics/`，均为本机生成数据，不提交 Git：

| 文件 | 内容 |
| --- | --- |
| `manifest.json` | 配置、资源候选、TGI 选择结果、计数和产物 SHA-256 |
| `strings_zh.json` | 只含已选定、无冲突的中文键值 |
| `string_sources.json` | 紧凑来源索引；多个字符串共享来源组，降低游戏内存占用 |
| `string-candidates.json` | 所有候选文本，包括被覆盖版本；仅离线审计使用 |
| `resource_catalog.json` | v2 类型目录，分角色保存字段、hash、数组索引、来源和冲突 |

脚本包只内置中文词表及紧凑来源索引；完整 tuning 目录与全部候选不在游戏启动时加载。`build.py` 检查词表 SHA-256 和资源目录的游戏版本，拒绝混配。

重新解释 Context 导出：

```powershell
& $py scripts/translate.py INPUT.json .validation/reinterpreted-context.json `
  --strings .local/resource-semantics/strings_zh.json `
  --string-sources .local/resource-semantics/string_sources.json `
  --catalog .local/resource-semantics/resource_catalog.json
```

原 `snapshot/history/target` 不变，新结果在 `semantic_view`。新增的 `reference_semantics.fields.<角色>.alternatives` 保存静态参考文本，各自标注 `tokens_basis=not_captured_for_this_field`；不会从今天的 Sim 或另一个字段补写参数。v1 名称目录仍可读取，v2 会用当前词表重新解释已有 hash，包括原先被标记成功的名称，并记录本次重解释依据。

旧记录缺少资源类型时，允许用“ID＋完整 tuning 名”在目录中唯一匹配来补静态参考，标记 `kind_basis=unique_catalog_identity_not_observed`；同身份对应多类资源或有资源冲突则拒绝匹配。这不会回填历史资源类型、动态参数或原名称。

审计一个或多个运行目录：

```powershell
& $py scripts/audit_localization.py RUN_DIRECTORY `
  --strings .local/resource-semantics/strings_zh.json `
  --string-sources .local/resource-semantics/string_sources.json `
  --catalog .local/resource-semantics/resource_catalog.json `
  --output .validation/resource-semantics-audit.json
```

审计分别统计原有显示文本的变化、未解析原因，以及日志中出现资源的静态描述补充。相同事实在 journal 和 Context 中可能重复，不能把 occurrence 当作独立事件数量。
