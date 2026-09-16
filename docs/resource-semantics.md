# 游戏资源语义目录与运行时文本

本轮继续使用 MOD 0.6.0、API／SDK 1.1.0、schema 1。新增字段兼容原有消费方式；实现及验证范围见[验证记录](validation/2026-09-15-resource-semantics.md)。

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

每个文本对象保留 `text`、`status`、`hash`、`template`、`localization` 和 `source`。`localization.tokens` 是当时复制的证据；`string_source.sources` 是当前字典来源 ID，可在相同构建的 `string_sources.json` 中查询包名、TGI、资源摘要与配置优先级。构建与查询的 `provenance.string_sources_sha256` 标识这份目录。

## 模板支持与缺口

在原有姓名、普通数值、嵌套字符串、物件名称／描述、简单 M/F 分支之上，新增 `ObjectCatalogName`、`ObjectCatalogDescription`、Sim token 的 `ObjectName` 别名及小写 m/f 分支。目录名称／描述不会被自定义名称替换。修复空自定义代词槽 `|||||` 的处理；真实自定义代词或中性分支仍保留未解析。

日期 token 的原始字段和 SIM_LIST 中无独立 type 的子项现在能正确保留。日期、金额、列表的最终语言格式以及 `T/DAE` 年龄条件、姓名前缀、复杂代词暂未完整实现。现有逆向资料尚未给出所有条件映射，不能仅根据一个示例推定语法。

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

接口仍需在游戏模拟线程调用，返回数据不持有游戏对象引用。当前游戏窗口继续优先显示名称与事件事实，长描述通过 JSON／SDK 供下游使用，不自动塞进每一行历史正文。

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

审计一个或多个运行目录：

```powershell
& $py scripts/audit_localization.py RUN_DIRECTORY `
  --strings .local/resource-semantics/strings_zh.json `
  --string-sources .local/resource-semantics/string_sources.json `
  --catalog .local/resource-semantics/resource_catalog.json `
  --output .validation/resource-semantics-audit.json
```

审计分别统计原有显示文本的变化、未解析原因，以及日志中出现资源的静态描述补充。相同事实在 journal 和 Context 中可能重复，不能把 occurrence 当作独立事件数量。
