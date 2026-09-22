# 事件整理、对账与规则比较

适用 ContextOverlay **0.10.10**；API / SDK **2.2.0**。四层查询入口、分页和原始记录格式不变。规则版本为 `experience_view_v1_6` / `experience_recap_v1_3`，具体规则文件和代码哈希随快照保存。0.10.7 调整内存预算，0.10.8 按预算复用解码结果，0.10.9 移除来源日志的独立字节上限，0.10.10 修正完整页面预算计算；这些调整均未修改分类或总结规则。

## 重要事件和活动阶段

精确匹配游戏版本、资源 ID 和 tuning name 后，火灾反应、惊慌、着火、灭火及撤离动作作为重要活动保留，带 `importance=critical`。它们仍分别保留每次执行、行动者、入队、开始、结束和退出原因。仅看到灭火入队不能写成已经灭火；自然退出或引擎的 SUCCESS 也不能写成救援成功。

资源规则有官方 tuning 的包位置、TGI、源 SHA-256、展开 XML SHA-256 和类结构依据，保存在 `experience_resources.json` 的 `reviewed_activity_rules`。这些是审阅过的资源说明，不是游戏运行状态的推断；不能证明第三方 MOD 没有覆盖同一资源。0.10.4 新增 48 条精确资源规则，覆盖椅子小睡、电脑游戏和电影变体、洗盘子、清洁、铲雪、社交及相应内部步骤，并纠正原有洗浴姿态规则。

0.10.5 继续核对 23 条资源，其中 18 条为新增审阅身份：覆盖这一组全部 10 部电影、美食烹饪阶段及配方选择器、节日／天气社交、扔饮料与附带聊天。美食制作阶段通过实际父实例链延续烹饪终点，并保留成品关联；选择配方的 router 不单列成一次做饭，其自主决策用 `decision_role=action` 保留活动方向。吃饭仍是独立活动，未见执行的社交仍是尝试。

0.10.6 核对 54 个固定版本 tuning，增加 54 条审阅身份（含一个明确的 Join 代理身份），覆盖烧烤、游泳、日光浴、床上午睡、洗碗机、拿餐、烹饪指导、社交变体及相应细节。烧烤的加工和取出成品阶段通过实际父链归入制作活动；拿餐、进食和洗碗仍分别保留。自主烧烤／游泳／午睡的选择入口不写成已执行活动，记录下来的自主选择仍保留。游泳与躺椅姿态只作为支持证据，起止不一致或可见的姿态不套用内部洗浴姿态的严格归并规则。

`[Join]GroupCooking_Mentor` 已按 `ProxyInteraction.generate` 和 `JoinInteraction.proxy_name` 核对，并作为独立人物的烹饪指导执行保留。只增加这一条精确身份，不普遍剥离 `[Join]` 等前缀，也不据此将指导者视作食物制作者。新增 48 条名称回退释义带来源，保留原始名称和缺失参数；电影反应的资源身份不能证明具体观影结果。

阅读、看电影、洗澡、下棋、着火持续动作等阶段，只在实际记录的父实例或 mixer provider 关系通过校验后归入所属活动。校验包含人物、到访、实例唯一性、资源身份、provider 的对象和选择时间；已有的阶段对象不能与 provider 冲突。多个 provider stage 不选最后一个覆盖前一个。不同活动不会因时间相近被归并。并行的 provider 子动作不延长根活动的时间；有明确 parent_event_id 的延续阶段可以延长观察到的活动终点。

没有可靠父活动的已分类阶段留在 `details`，原始动作、执行状态、关联缺口以及由它产生的后果仍可查询。打架、歌曲演奏等独立内容保留为活动；不会因为它们使用 Mixer 类就一概当成噪音。记录名称 `[Join]guitar_Watch` 不等于官方 `guitar_Watch`，仍需另行核查。

`generic_Bath` 是提供洗浴姿态的内部交互，分类为 `activity_support`，不再另列一次“洗澡”。它不一定带父实例引用：仅在固定资源身份、POSTURE_GRAPH 来源、内部不可见 SuperInteraction、同人物／到访／物件、完整且精确相同的执行起止时间、唯一可见根活动均通过校验时，才挂到根活动，关联依据为 `reviewed_posture_provider_same_execution`。已有父引用与候选冲突时也拒绝关联。缺少结束、多候选或不匹配时保留独立详情；支持步骤不延长根活动，不改变其入队时间或结果。原始交互、修订和支持步骤的后果仍可回查。

`sim_BeAffectionate` 的官方 tuning 指定在双方运行附带的 `sim_Chat`。只有精确资源关系、同到访／行动者／目标、完整且相同的执行起止、触发来源、退出原因、结果和人物角色全部一致，且双方实例和配对均唯一时，才按各自行动方向归并。附带聊天须首次观测于执行开始，没有独立决策及冲突父引用；`run_direct_gen` 会先记录入队，因此允许与开始同一 tick 的入队，拒绝提前、延后或时间不明的入队。归并依据为 `reviewed_additional_social_same_execution`，保留根活动入队时间、结果及两条原始证据，话题和后果继续关联，活动时长不延伸。仅重叠、未结束、不同方向或多候选均不依此规则归并；双方视角也不合成单一行动者。

## 问题原因分别表达

单元和 policy 解释的 `review_reasons` 是可重叠的标记，不是互斥分类，也不改变执行事实。

| 代码 | 含义 | 处理 |
| --- | --- | --- |
| `classification_missing` | 尚无可靠分类，或资源身份／版本不匹配 | 保留 review；名称可读也不等于分类已完成 |
| `name_unresolved` | 阅读输出里的名称或参数缺失 | 保留原名、解析状态和释义依据；不补猜人物或物件 |
| `association_missing` | 找不到通过校验的所属活动／原因 | 保留独立记录或详情，不按时间猜测关联 |
| `protected_detail` | 执行细节被其他事件作为原因引用 | 保留详情与后果，不能作为普通噪音去掉 |
| `unsupported_observation_shape` | 状态或数值形状不足以安全组织 | 留在 review，原始字段可回查 |

`review_actions` 继续是一条执行实例一项的平面数组，新增资源、到访、完整时间阶段和原因标记。Markdown 按原因、精确资源、人物角色和到访分组，每组可展开所有引用、时间和退出状态。分组只影响阅读，不合并事件，也不删除重复外观的不同实例。

公开查询可通过 organized 单元读取原因；`explain_event_view(..., facet="policy")` 返回单元的去向与原因，`labels` 返回名称证据，`events` / `revisions` 回查原始事件。离线 bundle 继续支持 `@review`、`@details`、`@audit` 等入口。名称问题统计针对阅读时实际解析的标签，不声称覆盖原始日志的每个字符串。

物件目标名称与活动名称使用同一解析与审计流程。组织时保留物件原始名称及其解析状态，阅读时将无法解析的目标明确显示为“物件名称未解析”，不直接拼接 `{0.ObjectName}` 等占位符。新识别的活动可能暴露此前未统计的物件名称缺口，因此名称问题计数不保证随分类覆盖改善而单调下降。缺失的聊天 String 参数继续明示缺口，不根据参与者顺序补猜。

## 自动对账文件

正常结束或手动 `co.export_views` 时，整个快照一起生成并发布：

- 全局 `quality.json`：来源截点、原始记录／修订／观察数量、采集状态和各人物摘要。
- 每个人物的 `quality.md`：各层数量、正文与待核查数量、来源去向和检查结果。
- 每个人物的 `quality.json`：每个源事件的稳定 ID、修订、单元、去向、规则、内容哈希、问题原因，以及高频未知资源和重要事件保留情况。

这些文件与其他输出一起受生成预算约束，并写入 manifest 的长度／哈希清单。对账失败或写文件失败不发布新 latest；旧快照和原始日志保留。文件位置、关闭完整性的判断见[运行输出](run-output.md)。

源事件的主去向按 `recap → review → detail → external → not_expanded` 优先选择一个，因此人物的去向总数能与其最新事件数相等。同一源事件可以支持多个单元或同时支持正文和详情；完整映射仍保留。`not_expanded` 指按明确规则不在回顾中展开，不是从 journal 删除。人物之间共享来源，不能把人物计数相加当作全局唯一事件数。

检查通过只证明来源有交代、已确认参与的重要火灾动作保留在正文，不证明所有游戏机制已采集，也不等同于语义准确率或玩法成功率。旧 bundle 没有细分原因时，比较报告标记 `legacy_review_unspecified`，不反推不存在的旧分类。

## 离线生成和比较

在仓库根目录，用 Python 3.7 或兼容的开发解释器执行。示例中的人物 ID 和文件路径需要换成实际值。

```powershell
python -B -X utf8 scripts/experience_recap.py build tmp/journal.jsonl --entity sim:123 --game-version 1.126.73.1030 --output tmp/after.bundle.json --markdown tmp/after.md --quality tmp/quality.json --quality-markdown tmp/quality.md
python -B -X utf8 scripts/experience_recap.py quality tmp/after.bundle.json --output tmp/quality.json --markdown tmp/quality.md
python -B -X utf8 scripts/experience_recap.py compare tmp/before.bundle.json tmp/after.bundle.json --output tmp/comparison.json
```

比较会先验证两个 bundle 的快照身份，再要求源 SHA-256、最新事件集哈希、session 和人物完全相同。不同局、不同人物或不同采集截点不能当成规则改进比较。支持来源的依赖集合可以随规则变化，但人物关联的输入事件集合必须一致。

结果包含前后规则哈希、层级数量和每个变化事件的前后去向／单元内容哈希；`changed_sections` 另外检测活动、结果等阅读部分的文字变化。对齐使用稳定事件和单元 ID；r18、r19 等阅读引用重新编号不算变化。标签文字、执行边界或单元内容变化也会被检测。变化不自动被判定为改善，需结合原始证据验收。保留旧 bundle，再对冻结的同一 journal 用新规则重建，才能进行有效比较。
