# 事件整理、对账与规则比较

适用 ContextOverlay **0.10.2**；API / SDK **2.2.0**。四层查询入口、分页和原始记录格式不变。规则版本为 `experience_view_v1_3` / `experience_recap_v1_2`，具体规则文件和代码哈希随快照保存。

## 重要事件和活动阶段

精确匹配游戏版本、资源 ID 和 tuning name 后，火灾反应、惊慌、着火、灭火及撤离动作作为重要活动保留，带 `importance=critical`。它们仍分别保留每次执行、行动者、入队、开始、结束和退出原因。仅看到灭火入队不能写成已经灭火；自然退出或引擎的 SUCCESS 也不能写成救援成功。

新增的 38 条资源规则有官方 tuning 的包位置、TGI、源 SHA-256、展开 XML SHA-256 和类结构依据，保存在 `experience_resources.json` 的 `reviewed_activity_rules`。这些是审阅过的资源说明，不是游戏运行状态的推断；不能证明第三方 MOD 没有覆盖同一资源。

阅读、看电影、洗澡、下棋、着火持续动作等阶段，只在实际记录的父实例或 mixer provider 关系通过校验后归入所属活动。校验包含人物、到访、实例唯一性、资源身份、provider 的对象和选择时间；已有的阶段对象不能与 provider 冲突。多个 provider stage 不选最后一个覆盖前一个。不同活动不会因时间相近被归并。并行的 provider 子动作不延长根活动的时间；有明确 parent_event_id 的延续阶段可以延长观察到的活动终点。

没有可靠父活动的已分类阶段留在 `details`，原始动作、执行状态、关联缺口以及由它产生的后果仍可查询。打架、歌曲演奏等独立内容保留为活动；不会因为它们使用 Mixer 类就一概当成噪音。记录名称 `[Join]guitar_Watch` 不等于官方 `guitar_Watch`，仍需另行核查。

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
