# 0.4.0 游戏资源名称解析：离线验证

日期：2026-09-15。分支：`zixuan/semantic-resource-resolution`。本轮完成代码、离线检查、旧记录重解释和本地构建；**未安装新包、启动游戏、修改存档或更新飞书附件**。机器可读依据见[验证数据](2026-09-15-semantic-resolution.json)。

## 1. 发现的问题与改动

旧版采用中文 STBL 和少量精确别名，但关系标记等资源未读取游戏的名称字段；动态交互只保存 hash，未保存 tokens。因此即使词表已有“和某人聊天”“吃某个食物”的模板，旧输出也无法填入参与者和目标。

本版从 EA 的名称入口取得 LocalizedString，保存 hash 与参数，再解析对应中文。关系标记使用 `display_name`、统计量使用 `stat_name`、物件状态使用 display mixin；物件实例通过名称组件取得实际 token。交互显式传入 target/context，失败回退仍使用 EA 的 token provider，避免自行猜测参数位置。

大量 Buff 和内部交互在参考 tuning 中没有名称或明确不可见。这部分显示缺少名称的原因并保留内部标识，没有用英文单词拆分来编造自然语言含义。方法、状态和限制见[语义化模块](../semanticizer.md)。

## 2. 输入依据

- EA 源码参考：`sims4-python`，提交 `12718ed96470fc2edffbc7875d10cf537b1f0e57`。
- 本地游戏：`D:/Games/The Sims 4`，构建读取版本 `1.126.73.1030`。
- 中文资源：`sims4-python/data/strings/CHS_CN.json`，文件摘要见验证 JSON。
- tuning：83 个当前 `combined_tuning_*.xml` 文件；排除 2014 年的 `combined_tuning_BASEFull.xml`。生成 69,886 个带类型的条目，其中 38,765 个有显式名称 hash，冲突 0 个。这是参考索引规模，不是游戏内已采集或已经翻译成功的数量。
- 实际数据：用户目录中最近 8 次运行的 `journal.jsonl`，运行 ID 和各文件 SHA-256 列在验证 JSON。所选运行没有 Context 导出文件。原日志均只读。

运行时直接读取当前加载的 tuning；全量离线索引不放进 MOD。中文 STBL 仍是构建时的固定输入，其他 MOD 新增或覆盖的字符串需要相应词表才能解析。

## 3. 旧数据的实际变化

先通过 `storage.replay` 验证每份日志并只保留各事件的最新修订，再对其中的显示名称进行比较。`raw_name` 属于回退证据，不重复计数。这里统计的是名称出现次数，不是事件数；同一名称可以在多个参与者、状态或事件中出现。8 份日志回放均无完整性错误。

| 名称状态 | 原记录出现次数 | 重解释后出现次数 | 原记录去重条目 | 重解释后去重条目 |
| --- | ---: | ---: | ---: | ---: |
| 完整资源解析 `resolved` | 1,291 | 1,428 | 149 | 159 |
| 精确别名 `rule_resolved` | 111 | 85 | 9 | 7 |
| 缺少参数／语法支持 `unresolved_tokens` | 111 | 135 | 5 | 6 |
| 参考资源未配置名称 `no_display_name` | 0 | 2,332 | 0 | 138 |
| 仍未映射 `unmapped` | 2,636 | 169 | 151 | 4 |
| 合计 | 4,149 | 4,149 | 314 | 314 |

去重按转换前后的文本、hash 和状态组合，代表不同名称转换，不代表不同游戏实体。**无显示名称的重新分类不是翻译成功**；`unmapped` 的下降主要来自这个区分。完整资源解析净增加 137 次出现、10 个不同条目；其中也包含将原手写别名换成游戏文本。静态参考未配置名称不证明当时没有动态覆盖，结果附有来源和 `runtime_override_not_verified`。

| 旧显示 | 本轮游戏资源解析结果 | STBL key |
| --- | --- | --- |
| `relbit_SocialContext_Casual` | 随意的谈话 | `0xF6AC10BE` |
| `friendship-acquaintances` | 相识 | `0xA25D7DE4` |
| `familyTrope_difficult` | 困难 | `0x6C1FD3B3` |
| `familyTrope_Jokesters` | 诙谐 | `0xA6DF0EFB` |
| `relbit_SocialContext_Friendship_Distasteful` | 不愉快的对话 | `0x8117F710` |
| `relbit_SocialContext_Friendship_Offensive` | 冒犯的对话 | `0x5AFD9D97` |
| 不喜欢（旧精确别名） | 被讨厌 | `0x24175290` |

旧 `sim_Chat` 可以显示为 `和〈未解析：1.SimFirstName〉聊天`，旧 `generic_consume_food` 可以显示为 `吃〈未解析：1.ObjectName〉`。旧日志没有保存参数，这些缺口无法恢复。`Buff_Near_Family`、`watch-movie`、`generic_cook` 等在参考数据中属于不可见／未配置名称；不将内部动作硬译为玩家看到的菜单活动。

## 4. 测试与产物

CPython 3.7.9 下 **90 项测试通过**。新增检查覆盖参数位置、中文与自定义名称、嵌套文本、缺参数、未知语法、循环与展开预算、实际枚举描述、EA 名称调用的上下文／回退、失败隔离、tuning 共享引用、类型隔离和旧事实不变；已有历史索引、UI 导航、持久化及安装器检查一并通过。

另外在游戏外导入本地 `generated.zip` 中的真实 `Localization_pb2`，用合成参数核对序列化与渲染，得到“和Nyssa聊天”“吃汉堡蛋糕”。这验证了实际消息字段兼容性，不是一次实机采集。游戏外加载的纯 Python protobuf setter 拒绝直接设置非 Latin-1 姓名，所以该项使用 ASCII 人名；中文姓名通过普通参数数据测试。没有修改游戏 protobuf 或绕过游戏环境的校验，原生环境的中文姓名仍需下一次实测。

EA 反编译的 localization wrapper 源码存在丢失参数展开符的情况；本地 `core.zip` 的字节码核对显示实际调用使用 `*tokens`。实现调用游戏原接口，没有照抄该处反编译代码。

本地产物：

- `dist/ContextOverlay.ts4script`：0.4.0，Python 字节码魔数与已安装游戏一致；包 SHA-256 为 `0b109ab660a6930eaf109c5fa57366817439adacc842873ca964499533da152d`。
- `dist/ContextOverlay-0.4.0-Windows.zip`：本地候选包，含现有安装器，未上传飞书。
- `.local/name-catalog.json`：完整离线索引，未进入 Git。
- `.validation/semantic-after.json`：本地详细名称对比。
- `.validation/semantic-examples/latest-run-before.json`、`latest-run-after.json`、`latest-run-after.txt`：最近一次运行的 459 个最新事件版本及重解释结果。逐事件中文引用仍对应相同事件 ID／修订，原 `history` 与重解释文件中的原始 `history` 相等。

原始日志、全量游戏资源及个人游戏事件副本均不提交仓库。验证 JSON 只保留统计、输入摘要及有限名称例子。

## 5. 复现和实机待验项

在仓库根目录先运行：

```powershell
python scripts/build_name_catalog.py C:/sources/sims4-python/data/tuning .local/name-catalog.json
$semanticEvidence = Get-Content docs/validation/2026-09-15-semantic-resolution.json -Raw | ConvertFrom-Json
$semanticRuns = $semanticEvidence.audit.runs | ForEach-Object { Join-Path "$env:USERPROFILE/Documents/Electronic Arts/The Sims 4/ContextOverlay/runs" $_ }
python scripts/audit_localization.py @semanticRuns --strings C:/sources/sims4-python/data/strings/CHS_CN.json --catalog .local/name-catalog.json --output .validation/semantic-after.json
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 -m unittest discover -s tests -v
```

用户目录如有重定向，替换为实际位置。输入摘要应与验证 JSON 对照；后续新运行不自动纳入本次统计。

实机待核对：聊天对象姓名、进食／阅读物件名称、自定义名称、关系标记、隐藏资源说明与游戏原显示是否一致，并检查导出的 tokens、名称错误和记录器状态。SIM_LIST、复杂语法、第三方字符串覆盖和长时性能仍不在已验证范围内。新增参数会增加单条事件体积，当前条数、内存和磁盘预算继续生效。
