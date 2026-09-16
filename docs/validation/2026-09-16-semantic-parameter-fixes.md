# 2026-09-16 实机样本后的语义参数修复

针对[首局实机报告](2026-09-16-semantic-supplements-first-live.md)暴露的问题完成三项修复。MOD 继续为 0.6.0、API／SDK 为 1.1.0。**本轮完成代码、测试、输入回放和本地打包，没有安装新包或启动游戏。** 机器可读证据见[验证数据](2026-09-16-semantic-parameter-fixes.json)。

## 修复内容

1. **Buff 描述绑定明确的持有者参数。** 对普通描述文本，只在没有现成 tokens、所有未解析位置均为第 0 参数的 Sim 姓名或简单 M/F 表达式时绑定。使用调用入口明确传入的唯一持有者，并通过 EA 的本地化 token 生成入口复制其参数。已有 tokens、可调用描述工厂输出、多参与者、其他参数位置和未验证格式保持原样。绑定失败保留原未解析结果，不影响名称。
2. **聊天等交互名称回退到实际 UI 队列文本。** 通用名称未完整解析时，读取当前 Sim 的 UIManager 中与交互 ID 一致的 display_name；若提供弱引用，还要匹配同一交互实例。只采用完整解析的候选文本，不调用 UI 更新函数。交互事实与来源交互资源共用此入口。记录不存在、ID 不匹配、引用过期、读取出错或候选仍未解析时，原结果继续保留。
3. **保留名称读取入口缺失诊断。** 离线重解释不再将 no_verified_name_accessor 改为 no_display_name。它们代表不同的问题；当前没有读取入口不等于资源没有文案。

Buff 输出的 source.token_binding 保存 basis、持有者 ID、参数位置及原始 unbound_localization；有效参数保留在 localization。可读详情标为 observed_buff_owner_context，窗口显示“按持有者解析”。UI 名称来源标为 runtime_ui_queue，原失败名称保留在 source.fallback_from。原生字段与绑定语境均可追溯。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| CPython 3.7.9 完整测试 | **191 项通过，0 失败**；新增 10 项参数修复测试 |
| 本局 Buff 输入回放 | **92／92 处完整解析**，涉及 16 个 Buff 资源，未跳过样本 |
| 本局 Context 中的两处缺口 | 音乐偏好和“生活气息”说明均可完整解析 |
| 真实游戏字节码函数与 protobuf 额外检查 | 91 处通过；1 个中文姓名受离线纯 Python protobuf setter 的编码限制，见下文 |
| 旧文件保护 | journal 与 Context SHA-256 保持不变 |
| 离线诊断重解释 | 130 处 no_verified_name_accessor 保持原状态，未伪装成名称覆盖提升 |
| 文本差异检查与构建 | diff --check、Python 3.7 字节码及资源指纹检查通过 |

输入回放使用同一 Buff 名称字段中已经记录的人物 token，持有者 ID 来自对应事件的 subject 或 Context 目标，再调用新适配器。统计为“修复后代码接受这组输入时的结果”，**不是重新取得的实机文本，也没有自动把旧名称参数移进原始描述字段**。常规离线翻译仍保留旧描述缺参数的事实。

两处 Context 示例：

- 音乐偏好：“Nyssa如鱼得水！因为她在做自己喜欢的事情，因此她能从中获得更多乐趣！”
- 生活气息：“一点点灰尘给Nyssa的家增添了一抹舒适的烟火气。不会太干净也不会太脏乱，刚刚好。……”（报告节选，输出未截断）

额外检查从本机 core.zip 读取 `_create_localized_string` 和 `create_tokens` 的已编译函数，配合本机 generated.zip 中的真实 protobuf 类执行，没有导入游戏服务或启动游戏。91 处通过；另 1 个已记录的中文姓名在离线环境向该 protobuf 类赋值时产生 Latin-1 编码错误。相同原始 Unicode 字符串通过 JSON 安全 token 路径和单独的 Unicode 回归测试，未用音译或乱码替代。该姓名的真实游戏内 token 构造仍需下次实机确认。

聊天路径的测试覆盖：通用名称的错误 SIM 参数、UI 返回含多个名字的 String、来源交互资源、无 UI 记录、错误 ID、过期引用、仍未解析的 UI 文本、读取异常及原名称已解析时不额外查询。旧日志没有记录对应 UI 文本，**不能声称本局 215 处聊天缺口已全部恢复**；其实际改善数量须在新构建实机运行后统计。

## 未强行填充的部分

- 28 处情绪描述缺少强度，来源主要为 Buff 关联的 mood_type，而非当前活跃情绪。不能套用角色当前情绪强度来选择这些资源的描述。
- 6 处物件 token 没有目录名称或自定义名称，继续保留标识与缺口。
- 本轮不扩展年龄选择器、复杂代词、列表和自定义日期规则。
- UI 名称仅在对应记录仍存在时可读；交互刚创建、已移出队列或未展示时仍可能只有通用名称。

## 构建与交付

新脚本包 `dist/ContextOverlay.ts4script` 的 SHA-256：`c927fd8fe63e47374da550358f6ff0ca6f22e3c2a03157f37b00da4e2992b5d3`。

本地 Windows 试用包和 SDK 包已包含本报告及首局实机报告，各有对应 `.zip.sha256`。当前游戏仍安装上一份已实跑构建 `54836702f2068abf80e230ec655dc6586c97a0668b378db254ad404ab9aac058`；不要根据相同的 0.6.0 版本号判断二者相同。

核心验证命令：

```powershell
$py = "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe"
& $py -X utf8 -m unittest discover -s tests -q
& $py -X utf8 .validation/verify_parameter_repairs.py
& $py -X utf8 scripts/build.py --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json
& $py -X utf8 scripts/package_sdk.py
& $py -X utf8 scripts/package_trial.py
```

输入回放脚本及完整逐项结果保存在本工作区 `.validation/`；共享验证 JSON 包含计数、样例和输入指纹。没有帧率、Hook 耗时或新界面截图，本报告不作性能和视觉验收结论。
