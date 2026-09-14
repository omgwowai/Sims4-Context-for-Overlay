# 参考资料基线

> 归档说明（2026-09-14）：本文保留整理前的阶段研究和结论，文中的“当前”“下一步”均属于原阶段，不作为新实现要求。仅修正位置相关链接及失效锚点。当前开发以[三模块总体设计](../../modular-context-provider.md)和[开发与参考基线](../../reference-baseline.md)为准。原路径：`docs/reference-baseline.md`。

核验日期：2026-09-11。以下均来自本地文件，本轮没有拉取 GitHub 更新，也没有运行游戏。

## 1. 版本与来源

| 来源 | 本地基线 | 备注 |
| --- | --- | --- |
| `C:\sources\sims4-python` | `12718ed96470fc2edffbc7875d10cf537b1f0e57` | Git 工作区干净；origin 为 `git@github.com:omgwowai/sims4-python.git` |
| `C:\sources\Sims4-Experience-Mod` | `9c25822f4fc9cbc68b88cd383d6c884dc489b0c1`；`VERSION=0.7.25` | Git 工作区干净；origin 为 `git@github.com:omgwowai/Sims4-Experience-Mod.git` |
| `C:\sources\Sims4-Context-Atlas` | 导出 `site_version=25`；`source_commit=f5117cee19d55f2efb537978e9762a0a8e48bdd4` | 本地是无 `.git` 的导出目录；版本来自 `export-manifest.json`，没有核验远端同名项目的当前提交 |
| Atlas 内嵌 Python | `1003b2507e5e68dd59eb10135140187bb5ed7750` | 来自 `inputs/python-source-receipts.json`；与当前本地 sims4-python HEAD 不同，不把两者视为同一版本 |
| Atlas 关系账本 | `4dec7ebaaf3ecba38f462827dbbbf4fee212d88c` | 来自 `dist/dashboard/manifest.json` 的 `source.ledger` |

用户最初提供的 GitHub owner 为 `omgwow`；本地前两个仓库的 origin 与 Atlas README 的来源说明使用 `omgwowai`。后续引用以可核验的本地版本和路径为准。

游戏版本也需分开看：sims4-python 根 README 声明研究版本为 `1.126.73.1030 / 1.126.78.1220`；Experience 的部分采集注释和实机审计针对 `1.127`。尚未取得本项目目标游戏安装的版本、DLC 和 MOD 清单。

## 2. 证据索引

下列路径均相对于对应参考项目根目录。链接按四个项目同处 `C:\sources` 的本地布局组织；独立克隆本仓库后，需要另外取得参考项目。这里只记录路径与研究结论，不复制 EA 源码或个人游戏数据。

| 编号 | 来源与定位 | 能支持的结论 |
| --- | --- | --- |
| A1 | [Atlas README](../../../../Sims4-Context-Atlas/README.md)，「执行图」「Context 全景看板」 | 静态解释、代码身份与运行时变量身份不同；不保证游戏行为全覆盖 |
| A2 | [Atlas 看板清单](../../../../Sims4-Context-Atlas/dist/dashboard/manifest.json)，`groups`、`totals`、`parseFailures`、`source` | 271,134 个身份的三组口径；8 个语法缺损文件；固定输入版本 |
| A3 | `Sims4-Context-Atlas/inputs/ledger/relations.jsonl.gz` | 本轮直接统计 45,178 条原账本关系：5,878 accepted、38,941 pending、359 unresolved；这些是原始状态标签 |
| A4 | [Atlas 验证报告](../../../../Sims4-Context-Atlas/dist/unified/validation.json) 与 [看板验证报告](../../../../Sims4-Context-Atlas/dist/dashboard/validation.json) | 既有验证检查的是数据身份、引用、源码与布局；本轮没有重新运行全部原项目验证器 |
| P1 | [Observer 边界契约](<../../../../sims4-python/ea-source/My Script Mods/autonomy_observer/Scripts/autonomy_observer/semantic_capture.py>)，`SEMANTIC_BOUNDARIES`，第 98 行起 | 已加载地块实例化 Sim；场外/Away Action 不做决策追踪；Native 路径与随机源有边界 |
| P2 | [Observer 状态采集](<../../../../sims4-python/ea-source/My Script Mods/autonomy_observer/Scripts/autonomy_observer/state_capture.py>)，`snapshot_sim`、`snapshot_all_instanced_sims` | 按 tick 读取部分状态；通过 `instanced_sims_gen()` 枚举，不能替代全存档状态目录 |
| P3 | [Observer 跟踪器](<../../../../sims4-python/ea-source/My Script Mods/autonomy_observer/Scripts/autonomy_observer/tracer.py>)，`summarize_value`、`start` | Forensic 记录调用与参数摘要；集合采样和深度限制仍存在；并非完整内存快照 |
| P4 | [Observer README](<../../../../sims4-python/ea-source/My Script Mods/autonomy_observer/README.md>)，「0.12.16 测试盲区闭环」 | 项目文档声明 BASE_GAME、7200 tick 的一次 93,971 事件观测；本轮没有重跑该实机验证 |
| P5 | [客户端架构](../../../../sims4-python/docs/client-architecture.md)，「资源覆盖」「脚本层」「边界」 | 有 Native 扩展、资源覆盖和删除标记；现有逆向研究也有范围限制 |
| P6 | [统计量写入](../../../../sims4-python/ea-source/EA/simulation/statistics/base_statistic.py)，`set_value`、`_notify_change`、`add_value`，第 174 行起 | 游戏存在直接状态写入与 tracker 通知入口；Loot hook 不能被直接等同于所有写入 |
| P7 | [SimInfo 保存](../../../../sims4-python/ea-source/EA/simulation/sims/sim_info.py)，`_generate_sim_protocol_buffer`，第 1910 行 | 存档消息包含 `sim_id`；身份排查应核验持久 ID，不能只按名字或人格合并 |
| P8 | [TestEvent 分发](../../../../sims4-python/ea-source/EA/simulation/event_testing/event_manager_service.py)，`process_event`，第 144 行起 | 该入口处理显式发送的事件；没有据此证明所有状态变更都经过它 |
| E1 | [Experience 模型](../../../../Sims4-Experience-Mod/src/experience_recorder/model.py)，`KINDS`、`next_event_id`、`obj_ref` | 六种经历投影、基于会话秒戳和序号的事件 ID、Sim/Part 引用处理 |
| E2 | [Experience 交互 hook](../../../../Sims4-Experience-Mod/src/experience_recorder/hooks/interaction_hook.py)，`_on_archive`，第 258 行起 | 终态采集、幂等集合、50,000 上限后清空；参与者字段最多 6 个，同场物件有界采样 |
| E3 | [Experience 决策 hook](../../../../Sims4-Experience-Mod/src/experience_recorder/hooks/decision_hook.py)，`TOP_K`、`_candidates`、`_on_decision` | 候选表只取前 5 条；skip 按 300 秒现实时间窗口合并。前 5 条是否总按分数排序，本轮未证明 |
| E4 | [Experience 感知 hook](../../../../Sims4-Experience-Mod/src/experience_recorder/hooks/perception_hook.py)，`_dedup`、`_on_reaction`、`install` | 广播 120 秒冷却；TestEvent 暂不接入；Reaction 观察回调没有通过返回值单独验证效果成功 |
| E5 | [Experience Loot hook](../../../../Sims4-Experience-Mod/src/experience_recorder/hooks/loot_hook.py)，`_on_op_applied`、`install`、`install_buff` | 对象为 Sim 才记录该类经历；遍历安装时已存在的 Loot 子类；调用记录并非都带真实前后值 |
| E6 | [Experience 过滤](../../../../Sims4-Experience-Mod/src/experience_recorder/filters.py)，三个 blacklist 与 `INERT_LOOT_LISTS` | 部分事实在落盘前被丢弃；另一些只打 inert 标记，两者不应混为一谈 |
| E7 | [Experience 写入](../../../../Sims4-Experience-Mod/src/experience_recorder/writer.py)，`emit`、`emit_ledger`、`flush`、`begin_launch` | 每 Sim 缓冲 1000 条；台账 deque 上限 5000；先出队后写盘；启动时间戳不等于存档身份 |
| E8 | [Experience 初始化](../../../../Sims4-Experience-Mod/src/experience_recorder/__init__.py)，`install_all_hooks`、`_on_zone_ready` | 首次 zone 就绪才装主体 hook；本轮未在注册清单中发现设计稿所述 Career/Away Action 或独立 RelBit hook |
| E9 | [Experience Situation hook](../../../../Sims4-Experience-Mod/src/experience_recorder/hooks/situation_hook.py)，`_on_add`、`_on_remove` | 每 Sim 只有一个 `CURRENT_CONTAINER`；Situation 时长由现实时间差计算 |
| E10 | [Experience 待办](../../../../Sims4-Experience-Mod/OPTIMIZATIONS.md)，§4.5、§4.6、§4.7、§4.9 | 多 Buff 情绪归因、身份疑点、跨存档混合、悬空 parent 的历史分析；其中推测不能当成已复现根因 |
| E11 | [Experience 开发文档](../../../../Sims4-Experience-Mod/DEVELOPMENT.md)，§2、§3、§5 | 可复用的六类经历、因果与情境分离、快照分离设计；需逐条核验实现程度 |

## 3. 已发现的文档与实现口径差异

- sims4-python 根 README 写 Observer `0.11.1`；其源码 `__init__.py` 实际为 `0.12.16`。本轮以源码核验为准。
- Observer README 部分段落写 Capture Contract schema `6`；`semantic_capture.capture_contract()` 当前返回 `7`。
- Experience 设计稿列出了 Career、Away Action、RelBit、完整生命周期等采集目标，当前注册清单和源码不能支持“全部已实现”的结论。
- `win0910` 的 health 声明 `0.7.24`；当前源码是 `0.7.25`，且同一旧版本号下也可能包含不同采集时点的修复。历史样本只证明样本自身的情况。
- Experience 历史文档把同名且人格相同的 Sim 多 ID 解释为旅行重实例化。本轮未证实该根因；EA 保存代码已经提供持久 `sim_id` 线索。名字与人格均不适合作为自动合并的唯一依据。

## 4. 历史审计方法与结果

2026-09-14 已删除旧参考数据审计脚本，Experience 部分准备重新开发。本节保留 2026-09-11 的审计方法和结果，不再提供当前可运行的复现命令。

当时的脚本仅使用 Python 标准库，仅读取 Atlas 数据和 Experience 的三个既有样本，不导入或执行参考项目代码。若源文件不是合法 JSON/事件结构，检查会报错，不静默跳过坏行。

[结果文件](research/2026-09-11-reference-audit.json) 保存聚合计数及输入摘要。摘要算法是：按相对 POSIX 路径排序，每个文件形成 UTF-8 的 `路径 + NUL + 文件 SHA-256 + LF`，再计算整体 SHA-256。摘要用于锁定本轮输入，不是对整个参考仓库的文件校验。

本轮重新统计了 Atlas 身份、关系状态、事件 ID、引用闭合与字段存在性；没有重建 Atlas AST、运行游戏、验证所有语义重复，或复验参考项目声明的游戏覆盖率。
