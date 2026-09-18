# 短版回顾：重复感、名称缺口与时间空白

2026-09-18，针对 Eddie 阅读反馈修正离线消费层。当前版本为 `experience_recap_v1_1`、`experience_view_v1_2`。本轮没有改变事件筛选与归并条件，也没有修改游戏采集代码。

## 证据范围

原始会话 `99a3d4b1597743fb83c303f01a293b2f`，日志 SHA256 为 `c57e7c636dd3b594e2f1664b3df45502be9662b6ee0654301654da6be3b2c0fc`，共 6,775 个最新逻辑事件。调查同时读取最新记录中的 observation 和 JSONL 修订链，不能只看最终退出记录。

以下 `r`、`e` 引用仅属于此次生成的 Eddie 快照。原事件保留于 `.local/analysis/2026-09-18-recap/debug/case-events.json`、`case-revisions.json`、`gap-events.json`；修复前的 Eddie bundle、Markdown 和四人物不变量也已保存。新样例位于 `.local/analysis/2026-09-18-recap/`，应使用同目录 bundle 中的 snapshot_id 查询。

## 1. r18、r19：独立尝试被显示成相同标题

| 条目 | 行动者 | 交互实例 | 入队 | 开始 | 退出 |
| --- | --- | --- | --- | --- | --- |
| r18 | Lila | 2262 | 12:11:28.520 | 未观测到 | 12:19:01.720，交互不兼容 |
| r19 | Ava | 2274 | 12:11:43.920 | 未观测到 | 12:19:01.720，交互不兼容 |

两者目标都是 Eddie，动作显示名都是“和Eddie聊天”。原事件 ID 包含不同 actor 和 interaction instance，均无 parent/provider 归并依据。修订链分别从 `QUEUED` 进入 `EXITED`，没有开始记录。

旧版把主体放在末列、时间压到分钟，并将首次观测时间放进“活动起止”的左边界，放大了重复感。修复后主体成为独立的靠前列，入队时刻显示到秒，执行开始保持未知。两条尝试继续保留；同名、同分钟、同目标、同退出原因都不是去重条件。同一行动者的不同实例也不能按这些字段直接去重。

## 2. 英文名称与模板残留：压缩时丢失名称质量

| 样例 | 原始证据 | 新呈现 |
| --- | --- | --- |
| r4／r7 | `190163 / Sim_RainStart_Reaction`，hash 为零，`no_display_name` | 对开始下雨作出反应 |
| r11／r39 | `130523 / socialMixer_Greetings_Wave`，`no_display_name` | 挥手打招呼 |
| r12／r13／r40 | `130519 / socialMixer_Greetings_AnnoyedSigh`，`no_display_name` | 不耐烦地叹气打招呼 |
| r26，以及其他人物的多个闲聊动作 | `unresolved_tokens`；模板为“和{0.String}闲聊”，token 0 实际为 SIM，要求 RAW_TEXT／STRING | 闲聊（名称参数缺失） |

前一类原本没有游戏显示名称，不能称为中文翻译器漏译。后一类是名称参数证据不符合模板：安装的 `Idle_Chatting_STC` tuning 同时配置队列名称 `0x98041977` 与 Actor／Object 参数，记录确实保存了类型不匹配。适配器读取交互 `get_name`，失败时还尝试读取同一个交互实例的 UI 名称；这批记录没有取得可用的完整名称。没有证据支持拿“参与者列表中的第一个人”补成聊天目标，也不能把 SIM 参数强转成模板要求的目标文字。

共同缺陷发生在消费层：`compact` 只留下 text，丢掉名称的解析状态和资源身份；后续展示把 fallback tuning 和未完成模板当成普通名称。

修复包含：

- 压缩后仍保留 tuning 身份、名称状态和参数缺口标志。原始 hash、模板、token、来源保留在可回查证据中。
- 统一名称解析入口覆盖活动、活动内话题、结果、关系数值、状态和待核查动作。游戏显示名有效时优先保留；已有两项经核验的错误名称纠正继续保留原文和依据。
- 13 个中文释义按资源种类、ID、tuning 名和兼容游戏版本匹配。已从安装的 `1.126.73.1030` 资源提取 XML，核对来源块及 XML 哈希；`src/context_overlay/experience/experience_labels.json` 保存核验依据。这些是编辑释义，不冒充官方中文显示名，也不决定事件的归并或结果。
- 任意新增资源遇到未映射、空名、缺失参数或内部名称回退时，统一显示“名称未解析”，进入名称质量清单；不会因未添加特定字符串规则而悄悄漏过。普通英文名称和 Sim 自定义姓名仍可保留。
- `labels` 查询返回原名、状态、释义依据和所属单元；这些记录及名称规则文件哈希都纳入快照校验。明确保留“释义可用但参数仍缺失”与“完整名称已解析”的区别。
- `participant` 显示为“参与者”；退出代码有中文解释，未知的新退出代码显示“退出原因待解析”，原码仍在详情，不猜测取消主体。

Eddie 仍有 3 个名称存在缺口：r26 的闲聊名称参数、r153 的目标名称、r429 的待核查动作名称。它们是公开标明、能回查的缺口，没有为了让质量清单归零而删除记录或猜译。

## 3. r53 → r54：入队阶段在短版消失

| 事件 | 时间 | 原始证据 |
| --- | --- | --- |
| 第一次吃沙拉尝试入队，实例 5933 | 19:24:37.240 | queued observation |
| 第一次尝试退出，未见开始 | 19:25:08.320 | INTERACTION_INCOMPATIBILITY |
| 第二次吃沙拉入队，实例 5941 | 19:25:29.360 | queued observation；同实例修订 1、2 为 QUEUED |
| 第二次吃沙拉开始 | 19:33:50.520 | InteractionStart；修订 3 出现 started_time |
| 第二次吃沙拉退出 | 19:55:51.640 | 自然退出 |

19:25–19:33 并非完全没有采集。区间内还有站立、持物、动画与入座等记录。它们按原规则下沉为执行细节；空间移动的连续路径没有足够证据，不能把全部间隔叙述为“走到餐桌”或“坐着等待”。第一次退出到第二次入队约 21 秒的间隔也不补写为空闲。

真正丢失的是后续交互的入队里程碑：组织器过去用 `started_time or first_observed_time` 作为统一 time，短版只保留执行区间。现在每个活动保留首次观测及实际 queued observation；短版将 `queued_at`／`observed_at` 与执行 `time` 分开。无入队证据时只说“首次观测”；没有开始记录时 `time[0]` 必须为 null。入队不证明此后一直等待，时间空白也不证明人物没有活动。

## 交叉验收

新增 10 项测试覆盖名称元数据保留、精确身份／版本匹配、模板缺口、未来未知资源、普通英文／自定义姓名、嵌套结果，以及不同 actor／相同 actor 的独立实例、队列与执行分离、名称回查快照、未知退出代码。全量 Python 3.7 测试共 314 项通过。

| 人物 | 活动数（前后相同） | 非数值结果（前后相同） | 显示的入队／首次观测里程碑 | v1 token | v1.1 token |
| --- | ---: | ---: | ---: | ---: | ---: |
| Eddie | 62 | 16 | 59 | 7,921 | 8,992 |
| Ava | 85 | 17 | 78 | 8,526 | 9,807 |
| Nyssa | 71 | 15 | 64 | 8,258 | 9,427 |
| Lila | 79 | 16 | 75 | 8,069 | 9,262 |

所有原有活动 ID、开始／结束、角色、退出、产物关联、结果关联和证据集合均与修复前相同。短版增加了时间及名称质量信息，因此体积上升，没有为压低 token 删事实。计量仍为 `tiktoken 0.12.0 / o200k_base` 的完整默认紧凑 JSON。

原有 33 项固定经历／知识、10 项结果关联、Eddie 75 个独立人工证据和旧实机 9 项边界检查通过。同日已完成的实机日志另重放 10 项检查：演奏后取消、无开始的研究、同一电视的不同人物及两次独立观影仍正确。本次 debug 没有重新启动游戏；这属于既有实机证据回归，不是新一次实机观察。

全量测试另发现上轮新增计划文档未列入分发文档清单，修复了该文档依赖；现有测试只在临时夹具目录验证分发内容。没有运行分发命令或在项目中生成新 ZIP。离线脚本不进入游戏包，无需重新安装。

## 使用与复现

```powershell
python -B -X utf8 scripts/experience_recap.py query bundle.json --snapshot SNAPSHOT_ID --ref r4 --facet labels
python -B -X utf8 scripts/experience_recap.py query bundle.json --snapshot SNAPSHOT_ID --ref @labels --facet labels --limit 100
python -B -X utf8 scripts/experience_recap.py query bundle.json --snapshot SNAPSHOT_ID --ref r54 --facet raw --journal journal.jsonl
```

`labels` 同样分页，按照 `next_offset` 继续读取。查询必须使用重新生成的 bundle 快照；不能拿旧快照的短编号解释新版本。完整本地回归脚本为 `.local/analysis/2026-09-18-recap/verify_recap.py` 和 `debug/verify_debug.py`，后者核对四人物修复前不变量及本次三个问题的具体预期。

这些结果只覆盖固定日志与测试，不是总体召回率或所有游戏资源均已解析的承诺。
