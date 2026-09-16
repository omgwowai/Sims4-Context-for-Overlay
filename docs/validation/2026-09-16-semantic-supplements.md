# 2026-09-16 数据语义化补充验证

本轮补充资源说明的展示、旧记录的静态参考匹配，以及可验证的本地化格式与缺口诊断。MOD 继续为 0.6.0，API／SDK 为 1.1.0，schema 为 1。已完成代码、自动化测试、旧日志重解释和本地构建；没有安装新包或启动游戏，本报告不代表新行为通过实机验收。

## 已完成内容

- `rendered.resource_details` 汇集资源描述、条件提示和缺失名称时的静态候选名称，保留资源标识、角色、状态、依据及 `evidence_ref`。重复内容去重，默认最多 128 项，截断会显式标记。
- 游戏内状态条目和事件详情附加说明，沿用现有正文分页。列表保持简短；条件提示明确未判断触发条件，静态候选明确未确认当时使用。
- 旧记录缺失资源类型时，允许以资源 ID 和完整 tuning 名称在目录中唯一匹配静态参考。类型存在歧义或该 ID 涉及资源冲突时拒绝匹配；不补写原记录的资源类型、名称或动态参数。
- 整数 `Money` 输出“1234 模拟币”，`TimeShort` 输出 24 小时 `HH:MM`，游戏日期的 `DayOfWeekShort/Long` 输出“周一／星期一”。结果带 `format_profile=context_overlay_zh_CN_v1`，表示项目的确定性中文格式，不承诺与游戏客户端逐字符一致。
- 未解析诊断区分参数证据、未支持语法、预算限制和资源／格式问题。类型不匹配附参数位置、实际类型及期望类型。
- 情绪基础说明仅在运行时暴露该属性、且已知实际强度时读取；明确未判断客户端的年龄／特征覆盖。属性为客户端专用而未暴露时不提供。
- 将“未配置显示名称／用途待解释”的笼统提示改为“未取得显示名称”，避免把读取缺口误写为原生文案不存在。

## 旧日志对照

数据为现有会话 `44d1bce892184d86bcf3f45e02f707a6` 的 journal 最新事件修订及 Context 导出，沿用上一轮相同官方资源目录。对照基线见[上一轮报告](2026-09-15-resource-semantics.json)。以下字段按资源类型／ID、角色、属性、索引和 hash 去重，数量不是全部游戏资源的覆盖率，也不是新增的历史观测。

| 指标 | 上一轮 | 本轮 |
| --- | ---: | ---: |
| 可解析的静态描述字段 | 98 | 125（+27） |
| 可解析的静态名称字段 | 186 | 211（+25） |
| 仍缺参数的静态描述字段 | 21 | 21 |
| 已识别为空文本的静态描述字段 | 0 | 3 |
| 未完整解析的名称文本出现次数 | 60 | 60 |

60 处名称文本共含 62 个未解析表达式：44 个 `0.String` 为参数类型不匹配，14 个 `0.ObjectName` 为参数类型不匹配，2 个 `T0` 和 2 个 `DAE0` 为未支持语法。因此诊断统计为 `parameter_evidence=58`、`grammar_support=4`。这次新增的金额、时间和星期格式没有消除这个旧样本中的未解析名称，不将测试用例的成功算作真实日志覆盖提升。

原名称状态分布保持上一轮结果：`resolved=5235`、`no_display_name=16937`、`rule_resolved=567`、`empty_display_name=57`、`unresolved_tokens=60`。审计错误为空。

样例 Context 重解释产生 28 项去重后的补充内容：24 项描述、4 项静态候选名称，未截断。全部 `evidence_ref` 均可定位到对应文本；离线补充字段指向 `semantic_view`。原始 `snapshot`、`history`、`target` 三部分逐项相等，输入文件 SHA-256 保持不变。

例如，隐藏环境 Buff `buff_Environment_Hidden_Positive`（240234）没有取得名称，却存在官方说明：“一个优美的环境能使你的模拟市民心情变好。”隐藏或中间态资源不等于没有自然语言描述。相反，聊天模板需要 String 而旧记录只保留了 Sim token 时，即使有原生模板，也不能据此确定对话对象。

## 验证与构建

- CPython 3.7.9：完整测试集 181 项通过，失败 0，运行用时 3.690 秒。
- 新增 9 项测试覆盖中文格式、无效／缺失参数、未验证年龄语法、聊天对象不猜填、描述去重与边界、静态匹配歧义、证据路径、情绪强度和现有窗口详情导航。
- `git -c core.autocrlf=false diff --check` 通过。
- 构建校验本机游戏版本 `1.126.73.1030` 和 Python 字节码 magic `420d0d0a`，同时校验字典与来源文件一致。
- 脚本包：`dist/ContextOverlay.ts4script`，SHA-256 为 `54836702f2068abf80e230ec655dc6586c97a0668b378db254ad404ab9aac058`。完整源文件与字典指纹见[机器可读报告](2026-09-16-semantic-supplements.json)和包内 `build-manifest.json`。
- 本地交付为 `dist/ContextOverlay-0.6.0-Windows.zip` 与 `dist/ContextOverlay-SDK-1.1.0.zip`；各有相邻 `.zip.sha256` 文件。版本号相同，需按构建 hash 区分。

复现核心命令（在仓库根目录执行）：

```powershell
$py = "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe"
$run = "$env:USERPROFILE/Documents/Electronic Arts/The Sims 4/ContextOverlay/runs/44d1bce892184d86bcf3f45e02f707a6"
& $py -X utf8 -m unittest discover -s tests -q
& $py -X utf8 scripts/audit_localization.py $run --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json --catalog .local/resource-semantics/resource_catalog.json --output .validation/semantic-supplements-audit.json
& $py -X utf8 scripts/translate.py "$run/context-96e9f816dfae4920b414ce10035223ca.json" .validation/semantic-supplements-context.json --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json --catalog .local/resource-semantics/resource_catalog.json
& $py -X utf8 scripts/build.py --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json
& $py -X utf8 scripts/package_sdk.py
& $py -X utf8 scripts/package_trial.py
```

## 保留缺口

1. 旧记录未采集的动态参数不能追溯补回；不以当前人物名称填充过去的聊天对象。
2. `T/DAE` 年龄选择器、复杂代词、列表、姓名前缀、`DateShort/TimeLong`、自定义日期格式 hash 和小数金额仍保留未解析。现有 Python 参考不能充分证明完整客户端渲染规则。
3. 情绪说明可能为客户端专用数据；即使获得基础文本，也不等于确认客户端当时选择的年龄／特征覆盖版本。
4. 静态目录无显式字段不能证明继承、默认值或客户端专用文案不存在。当前来源限本机官方资源，未核验第三方 MOD 覆盖和资料片授权状态。
5. 新窗口内容与运行时可选字段仅经过测试替身和离线验证，游戏内可见性、布局与实际运行开销仍待实机复测。
