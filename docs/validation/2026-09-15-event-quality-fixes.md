# 0.6.0 首局问题修正：事件质量与调用来源

日期：2026-09-15。版本保持 0.6.0，API／SDK 1.0.0，schema 1。已完成代码修改、离线检查、构建和本机更新安装；**本轮没有启动游戏，新入口与名称结果仍需下一局复测**。此前首局日志作为诊断输入，保持原文件不变。

机器可读的策略对照、方法符号检查、构建与安装摘要见[验证数据](2026-09-15-event-quality-fixes.json)。问题基线见[首局分析](2026-09-15-event-expansion-first-live.md)。

## 已修改的行为

1. **屏蔽计时噪声。** 在直接数值通知进入事件缓冲之前，排除 tuning 名中独立 `TimeSince…` 段的计时统计，包括距上次睡眠、社交和做饭的计时。它们不再成为内存事件或 JSONL 事件，不留恢复开关。仅汇总计时通知屏蔽次数；该数也包含原本未必能形成事件的通知，不能当作精确删减事件数。Context 当前读取及其他直接数值效果保持原语义。
2. **修正已知内部步骤。** 进食 active/passive、动作捕捉游戏 active/passive 根据 ID 与 tuning 名共同匹配进入 internal。参考 BASE tuning 确认游戏垫使用隐藏的 one_shot／looping_animation，未将所有 mixer 或不可见交互一概隐藏。未知 Buff、统计量和具体社交动作仍保留。
3. **资源适配。** Sim 身份与真正的 BaseObject 才生成实体引用。广播器保存服务实例 ID 与资源，真实广播源另有实体角色。配方使用 get_recipe_name，来源交互使用 get_name，情绪使用 mood_names 与观测强度；通用资源根据已核验字段选择入口。未核实名称入口明确报告 no_verified_name_accessor。
4. **情绪与通知语义。** 增加 old_intensity／new_intensity／change_kind；同一种情绪的强度改变保留为事件。缺少旧强度时不回填猜测。抱负通知保留 aspiration_type，WHIM_SET、NOTIFICATION 和已识别 utility 目标归入内部层。制作描述明确是游戏产物通知，特征添加不等同于性格获得。
5. **角色与中文。** 数值、Buff、广播区分受影响者和发起者；库存区分物品、来源容器、目的容器和拆分产物。索引仍关联全部相关实体，正文按角色取主语。空显示文本保留 hash／tokens 证据，以资源名兜底，不再把整个字典塞进正文。
6. **诊断落盘。** session_start 和 session_end 保存 event_coverage 与 event_diagnostics；公共状态 API 也返回这些汇总。diagnostics 记录适配器回调和屏蔽通知次数，timing=not_measured，不声称测量了帧耗时。

## 查找到并接入的调用入口

本机没有单独的 Sims4-Experience-Atlas 目录，使用现有 `C:/sources/Sims4-Context-Atlas` 定位制作调用链，再对照 `sims4-python` 和本机游戏字节码。Atlas 的固定源码为 `1003b250…`，本项目 EA 参考为 `12718ed9…`，没有将静态图中的候选关系直接视为本机运行事实。

| 入口 | 取得的证据与用途 |
| --- | --- |
| ItemCrafted → CraftingComponent._crafting_process | 读取当前流程的配方和 `_current_crafting_interaction`；有实际交互对象时关联产物，不按时间配对 |
| CraftingPhaseSuperInteractionMixin._go_to_next_phase | 同步阶段完成上下文，为嵌套的产物通知及实际效果保留交互来源 |
| CraftingProcess.pay_for_item | 游戏可能不给底层资金方法传 Sim，且完成后清空付款字段；在入口保存付款人、配方和当前交互，再由原资金 Hook 核对真实前后值 |
| _Payment.make_payment、已加载实现的 on_payment | 利用真实 resolver／Sim，覆盖相应扣款及支付目标调用范围；不把请求金额当成实际金额 |
| InventoryTransfer._do_behavior | 通过 self.interaction 关联实际库存插入／移除 |
| CarryElementHelper._do_enter_carry／_do_exit_carry | 仅在同步携带状态变更调用期间保留 self.interaction，覆盖其中实际触发的库存变化 |
| CarrySystemInventoryTarget.carry_event_callback | 回调对象明确保留携带者、物品和容器；没有交互证据时只保存携带者与依据，不编造 event_id |
| BuffComponent._update_current_mood | 在同一组件调用内核对旧 mood／intensity 和通知时的新值；无法匹配时强度留空 |

所有新增包装针对同步入口，保持返回值与原异常。上下文在调用结束或抛异常时清理，不跨生成器暂停或延迟回调保留。返回一个稍后执行的回调并不会自动继承早先调用的原因。

这里没有完成所有异步库存、建造模式、无人设备延迟完成或自定义 MOD 路径的关联。同步入口是否在下一局的具体玩法中触发，要看新日志中的回调计数和事实。调用上下文缺失时仍允许原因为空。

## 离线验证与旧日志对照

CPython 3.7.9 下 **138 项测试通过**。在原 126 项基础上新增 12 项质量回归，覆盖过滤后的零写入与 Context 边界、来源角色、情绪强度、资源类型和命名入口、付款前后及字段清空、制作来源、库存携带者、异常／延迟回调上下文清理、目标子类型和空文本。既有退出清理测试也核对健康状态随 session_end 写盘。

`audit_event_hooks.py` 在安装游戏的 simulation.zip 字节码中找到 **58 个声明的方法符号**，无未解析；包含本轮新增入口和支付基类方法。此项检查没有运行 EA 代码，不证明所有动态子类或具体玩法已实机验证。

对原运行 `44d1bce892184d86bcf3f45e02f707a6` 严格重放完整成功。只把新筛选规则应用到其已有字段，得到以下对照：

| 规则 | 原日志中匹配的独立事件数 |
| --- | ---: |
| TimeSinceLastSlept | 1871 |
| TimeSinceLastSocial | 757 |
| TimeSinceCooked | 48 |
| 合计不再记录 | **2676**，约原事件的 31.4% |
| 按此过滤剩余 | **5847**，原为 8523 |
| 进食内部交互改为 internal | 40 |
| 游戏垫内部交互改为 internal | 303 |

移入 internal 的 343 条交互仍占事件容量，依然可以查询。以上不是重新跑游戏得到的数量，也没有改写旧日志。新的名称入口、制作／付款／库存关联无法凭旧记录补造，需要下一局取得运行时证据。

旧事件用新的正文逻辑可正确区分“给 Lila 添加 Buff，发起者为 Nyssa”，空情绪 Buff 则显示原始资源名与空文本提示。缺失的聊天 token、尚未支持的客户端年龄条件语法仍明确未解析，未用推测文本填补。

## 构建与安装

使用匹配游戏字节码的 Python 3.7 及现有中文 STBL 重新构建。脚本 SHA-256：`6713b39e11b6b4d1c9922e8df377cc7714ddaa21e602256d2515787dc8248034`。

通过项目安装器更新 `Documents/Electronic Arts/The Sims 4/Mods/ContextOverlay/ContextOverlay.ts4script`，旧脚本备份到 `ContextOverlay/install-backups/20260915T0847581206893Z/ContextOverlay.ts4script`。安装前后摘要与源包核对；存档、游戏选项、原配置和其他 MOD 保留。

Windows 包仍名为 `ContextOverlay-0.6.0-Windows.zip`，SDK 包仍为 `ContextOverlay-SDK-1.0.0.zip`；用旁边的 `.sha256` 或 build-manifest 区分同版本的不同构建。未更新飞书附件，没有 commit／push／merge。

下次复测建议继续使用睡眠、聊天、做饭／吃饭、书本或食物取放场景；结束后检查新运行的日志与入口健康。原始首局日志 SHA-256 仍为 `e1577d5f624ddce2428f3e70440fc2a1a88dde3bf7b8072c71d45d31d60bc81d`。
