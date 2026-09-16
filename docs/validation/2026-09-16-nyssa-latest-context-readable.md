# Nyssa Landry：最后一次 Context 的可读版

采集时点：游戏第 3 天 **07:03:33**；现实时间 2026-09-16 **15:30:40（北京时间）**。这是已保存的状态快照，不是重新查询游戏后的状态。

Nyssa 刚结束早晨的一段清理活动，正在准备与 Ava 聊天：聊天已进入队列，但没有开始执行的记录。她的精力读数偏低，身上仍有“小睡恢复活力”和“美味菜肴”等效果。与 Lila、Eddie、Ava 的关系均带有“被讨厌”标记，而且分别保留伤心或争吵记仇的情感。

本整理版只使用这一份 Context：包含当前状态和最近 **50 条**历史，历史条目的时间约为第 3 天 06:19—06:57。更早的一局经历没有并入；聊天准备、排队和完成分别表达。

## 当前在做什么

| 项目 | 快照中的状态 |
| --- | --- |
| 和 Ava 聊天 | 自主安排，已排队；尚未观测到开始 |
| 面向 Ava 的隐藏问候动作 | 已准备，未确认执行；原生中文名称缺失 |
| 聊天／听话内部动作 | 已排队；名称的字符串参数未能解析，不能补猜对象 |
| 站立姿态内部流程 | 处于 STAGED 阶段，不单独解释为一个生活事件 |

位置只记录到当前地块及坐标，没有可用的房间名或地块名称。坐标约为 x=197.451、y=151.245、z=168.510。

## 当前需求

下表是游戏原始统计值，不是百分比，也未套用额外的需求等级阈值。

| 需求 | 读数 |
| --- | --- |
| 精力 | -34.1 |
| 卫生 | 10.2 |
| 膀胱 | 39.6 |
| 社交 | 44.7 |
| 娱乐 | 57.3 |
| 饥饿 | 87.8 |

## 当前持有的可读状态

以下四项有可用的原生说明；其中音乐喜好效果属于隐藏 Buff。状态效果与当前精力数值分别记录，“打盹而恢复活力”不表示精力已经充满。

| 状态 | 原生说明 |
| --- | --- |
| 我就爱这个调！ | Nyssa如鱼得水！因为她在做自己喜欢的事情，因此她能从中获得更多乐趣！ |
| 打盹而恢复活力 | 没什么比小睡片刻更能让身体重新充满活力。 |
| 美味菜肴 | 令人满足的一餐！我会饱上好一阵子。 |
| 生活气息 | 一点点灰尘给Nyssa的家增添了一抹舒适的烟火气。不会太干净也不会太脏乱，刚刚好。这种自然温馨的环境有助于模拟市民们彼此熟悉、发展感情、一起玩耍和享受社交。 |

## 与家人的关系

| 对象 | 亲属关系 | 友谊值 | 关系与情感 |
| --- | --- | --- | --- |
| Lila | 孙女 | -30.0 | 被讨厌；Nyssa 觉得 Lila 对不起自己，与她相处会回想起伤痛 |
| Eddie | 孙子 | -41.0 | 被讨厌；家庭动力“困难”；Nyssa 仍对双方的争吵记仇 |
| Ava | 孙女 | -30.0 | 被讨厌；社交契合度不佳；Nyssa 仍认为双方的冲突没有结束 |

以上情感描述来自已记录的关系标记，不代表每次谈话都会再次争吵。三人的浪漫关系轨道均未实例化，不能当作数值为零。

## 刚才发生了什么

| 游戏时间（第 3 天） | 可确认的经历 |
| --- | --- |
| 06:19—06:26 | 出现多次选择熟人聊天的准备流程。面向 Lila 的聊天在 06:25 被取消，未观测到开始，不能写成已经与 Lila 聊过天。 |
| 06:28—06:33 | 自主选择“疯狂拖地”，随后该交互自然结束且结果分支为 SUCCESS。目标原生名称是“\*\* 调试 \*\*”，资源名为 Puddle_Small。 |
| 06:34 | 自主选择“打扫”，目标为纯粹简约洗手台。交互自然结束，但结果分支为 NONE，不单凭结束记录确认清洁效果。 |
| 06:39 | 针对奶酪饼干的“打扫”交互自然结束，结果分支为 SUCCESS；随后脚本请求通过 Autonomy 选择在酒庄柜洗手台“洗盘子”。 |
| 06:48 | 获得“生活气息”状态；原生描述指出家中有少量灰尘。 |
| 06:49—06:50 | 奶酪饼干对象关联的餐具计数从 1 降到 0.5，再降到 0，发起者是 Nyssa。 |
| 06:54 | “洗盘子”自然结束且结果分支为 SUCCESS。之后再次选择熟人聊天准备流程。 |
| 06:56 | 社交目标选择流程给出 Ava；“和 Ava 聊天”进入队列。截至 07:03 快照，仍无开始记录。 |

## 这份 Context 中的 6 次 Autonomy 决策

以下为本包实际附带的决策，按时间排序。表中的“准备聊天”是对隐藏技术流程的解释，原名为 aggregate_SocialObservation_Chat；不能据此认定实际对话发生。所有概率以当层完整候选池为分母，以下各层均已保留全部候选。

### 06:25:35 · 寻找熟人聊天的准备流程〔技术名称解释〕

来源：自主选择；已成功入队。

| 候选 | 目标 | 原权重 | 原概率 | 结果 |
| --- | --- | --- | --- | --- |
| 寻找熟人聊天的准备流程〔技术名称解释〕 | 未提供 | 3.3721 | 100.00% | 选中 |

选中项记录到的非零评分贡献：社交：3.3721。这些是评分证据，不直接等同于人物内心动机。

多任务检查：通过。

证据：`9ba4280cb22d4c65b469cad11439a0c7:autonomy:decision:8443`；对应交互 `9ba4280cb22d4c65b469cad11439a0c7:interaction:896523281981505978:8544`。

### 06:26:42 · 寻找熟人聊天的准备流程〔技术名称解释〕

来源：自主选择；已成功入队。

| 候选 | 目标 | 原权重 | 原概率 | 结果 |
| --- | --- | --- | --- | --- |
| 寻找熟人聊天的准备流程〔技术名称解释〕 | 未提供 | 3.3757 | 62.88% | 选中 |
| 疯狂拖地 | \*\* 调试 \*\* | 1.9924 | 37.12% | 未选中 |

选中项记录到的非零评分贡献：社交：3.3757。这些是评分证据，不直接等同于人物内心动机。

多任务检查：通过。

证据：`9ba4280cb22d4c65b469cad11439a0c7:autonomy:decision:8450`；对应交互 `9ba4280cb22d4c65b469cad11439a0c7:interaction:896523281981505978:8548`。

### 06:28:11 · 疯狂拖地 → \*\* 调试 \*\*

来源：自主选择；已成功入队。

| 候选 | 目标 | 原权重 | 原概率 | 结果 |
| --- | --- | --- | --- | --- |
| 寻找熟人聊天的准备流程〔技术名称解释〕 | 未提供 | 3.3803 | 62.92% | 未选中 |
| 疯狂拖地 | \*\* 调试 \*\* | 1.9924 | 37.08% | 选中 |

选中项记录到的非零评分贡献：整洁评分通道（StaticCommodity_Tidy）：1.9924。这些是评分证据，不直接等同于人物内心动机。

多任务检查：通过。

证据：`9ba4280cb22d4c65b469cad11439a0c7:autonomy:decision:8475`；对应交互 `9ba4280cb22d4c65b469cad11439a0c7:interaction:896523281981505978:8558`。

### 06:34:09 · 打扫 → 纯粹简约洗手台

来源：自主选择；已成功入队。

| 候选 | 目标 | 原权重 | 原概率 | 结果 |
| --- | --- | --- | --- | --- |
| 打扫 | 纯粹简约洗手台 | 19.9783 | 78.73% | 选中 |
| 寻找熟人聊天的准备流程〔技术名称解释〕 | 未提供 | 3.3989 | 13.40% | 未选中 |
| 打扫 | 普通塑料垃圾桶 | 1.9969 | 7.87% | 未选中 |

选中项记录到的非零评分贡献：整洁评分通道（StaticCommodity_Tidy）：19.9783。这些是评分证据，不直接等同于人物内心动机。

多任务检查：通过。

证据：`9ba4280cb22d4c65b469cad11439a0c7:autonomy:decision:8516`；对应交互 `9ba4280cb22d4c65b469cad11439a0c7:interaction:896523281981505978:8574`。

### 06:39:26 · 洗盘子 → 酒庄柜洗手台

来源：脚本请求调用自主选择算法；已成功入队。

| 候选 | 目标 | 原权重 | 原概率 | 结果 |
| --- | --- | --- | --- | --- |
| 洗盘子 | 酒庄柜洗手台 | 0.9750 | 100.00% | 选中 |

选中项记录到的非零评分贡献：洗盘子评分通道（StaticCommodity_WashDishes）：0.9750。这些是评分证据，不直接等同于人物内心动机。

多任务检查：本次脚本请求不适用。

证据：`9ba4280cb22d4c65b469cad11439a0c7:autonomy:decision:8522`；对应交互 `9ba4280cb22d4c65b469cad11439a0c7:interaction:896523281981505978:8600`。

### 06:54:13 · 寻找熟人聊天的准备流程〔技术名称解释〕

来源：自主选择；已成功入队。

| 候选 | 目标 | 原权重 | 原概率 | 结果 |
| --- | --- | --- | --- | --- |
| 寻找熟人聊天的准备流程〔技术名称解释〕 | 未提供 | 3.4616 | 100.00% | 选中 |

选中项记录到的非零评分贡献：社交：3.4616。这些是评分证据，不直接等同于人物内心动机。

多任务检查：通过。

证据：`9ba4280cb22d4c65b469cad11439a0c7:autonomy:decision:8602`；对应交互 `9ba4280cb22d4c65b469cad11439a0c7:interaction:896523281981505978:8670`。

## 附录：50 条原始语义化条目

以下保留系统已生成的中文摘要，按原包从新到旧排列。隐藏技术名和“未解析”标记不被改写成推测事实；长评分与筛除模板未重复粘贴，可在源 JSON 中查阅。

1. **06:57:51** — Nyssa Landry的commodity_ChildhoodInspiration_DiscoveryPhoneCallTimer（隐藏资源，未取得显示名称）：0.999 → 480（直接效果）。

2. **06:56:49** — Nyssa Landry的“SocialPickerSI（隐藏资源，未取得显示名称）”交互已自然结束，目标为Ava Landry；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。未取得已提交的决策明细。

3. **06:56:49** — Nyssa Landry的“和〈未解析：0.String〉闲聊（名称未完整解析，待解释）”交互已排队；自主选择。未观测到开始时间。已关联选中本交互的 Autonomy 决策。

4. **06:56:49** — Nyssa Landry的“si_nonTouching_Greetings_Glare（隐藏资源，未取得显示名称）”交互已排队，目标为Ava Landry；自主选择。未观测到开始时间。未取得已提交的决策明细。

5. **06:56:49** — Nyssa Landry与Ava Landry新增关系标记：SpecialBits_Greeted（隐藏资源，未取得显示名称）。

6. **06:56:49** — Nyssa Landry的“和Ava聊天”交互已排队，目标为Ava Landry；自主选择。未观测到开始时间。未取得已提交的决策明细。

7. **06:56:49** — Nyssa Landry的“autonomousSimPicker_SocialObservation_FamiliarChat（隐藏资源，未取得显示名称）”交互已自然结束；自主选择。玩法结果分支：NONE（与交互退出类型分别记录）。未取得已提交的决策明细。

8. **06:56:49** — Nyssa Landry的“aggregate_SocialObservation_Chat（隐藏资源，未取得显示名称）”交互已自然结束；自主选择。已关联选中本交互的 Autonomy 决策。

9. **06:56:49** — Nyssa Landry的“Emotion_Idle（隐藏资源，未取得显示名称）”交互已自然结束；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。已关联选中本交互的 Autonomy 决策。

10. **06:54:53** — Nyssa Landry新增Buff：buff_Environment_Hidden_Negative_Snob（隐藏资源，未取得显示名称）。

11. **06:54:53** — Nyssa Landry移除Buff：buff_Environment_Hidden_Positive（隐藏资源，未取得显示名称）。

12. **06:54:13** — Nyssa Landry通过 Autonomy 选择“aggregate_SocialObservation_Chat（隐藏资源，未取得显示名称）”，已成功入队；记录了 1 层选择。请求来源：自主选择。入队或进入执行不代表行为完成。

13. **06:54:03** — Nyssa Landry的“洗盘子”交互已自然结束，目标为酒庄柜洗手台；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。已关联选中本交互的 Autonomy 决策。

14. **06:50:04** — 奶酪饼干的statistic_Object_Dishes_DishCount（未取得显示名称）：0.5 → 0（直接效果）。发起者：Nyssa Landry。

15. **06:49:04** — 奶酪饼干的statistic_Object_Dishes_DishCount（未取得显示名称）：1 → 0.5（直接效果）。发起者：Nyssa Landry。

16. **06:48:04** — Nyssa Landry的buff_Dust_ReactionCooldown（隐藏资源，未取得显示名称）：541.733 → 720（直接效果）。

17. **06:48:04** — Nyssa Landry新增Buff：生活气息。

18. **06:40:13** — Nyssa Landry移除Buff：发布一则图书测评。

19. **06:39:26** — Nyssa Landry的“打扫”交互已自然结束，目标为奶酪饼干；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。未取得已提交的决策明细。

20. **06:39:26** — Nyssa Landry通过 Autonomy 选择“洗盘子”，已成功入队；记录了 1 层选择。目标：酒庄柜洗手台。请求来源：自主选择，脚本请求。入队或进入执行不代表行为完成。

21. **06:37:01** — Nyssa Landry移除Buff：Buff_hidden_MoodReplacement（隐藏资源，未取得显示名称）。

22. **06:37:01** — Nyssa Landry新增Buff：现在觉得很好（不应被看见）。[用途待解释：保留原始条目]

23. **06:34:09** — Nyssa Landry的“打扫”交互已自然结束，目标为纯粹简约洗手台；自主选择。玩法结果分支：NONE（与交互退出类型分别记录）。已关联选中本交互的 Autonomy 决策。

24. **06:34:09** — Nyssa Landry的“stand_Passive（隐藏资源，未取得显示名称）”交互已取消；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。已关联选中本交互的 Autonomy 决策。

25. **06:34:09** — Nyssa Landry通过 Autonomy 选择“打扫”，已成功入队；记录了 1 层选择。目标：纯粹简约洗手台。请求来源：自主选择。入队或进入执行不代表行为完成。

26. **06:33:58** — Nyssa Landry的“疯狂拖地”交互已自然结束，目标为\*\* 调试 \*\*；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。已关联选中本交互的 Autonomy 决策。

27. **06:33:58** — Nyssa Landry的commodity_SelfDiscovery_Tracker_Hygiene（隐藏资源，未取得显示名称）：-14.645 → -29.645（直接效果）。

28. **06:33:58** — Nyssa Landry：广播效果执行。broadcaster：instance_id：405，resource：Broadcaster（名称未完整解析，待解释），effect：BroadcasterEffectAffordance，effect_success：not_inferred，observed_callbacks：1，perception：not_inferred，phase：remove_callback_returned，scope_evidence：local_at_call仅表示效果执行回调，不推断目睹、理解或效果成功。

29. **06:33:54** — Nyssa Landry的“扰人的捣乱”交互已取消，目标为\*\* 调试 \*\*；脚本触发。未观测到开始时间。

30. **06:28:11** — Nyssa Landry的“stand_Passive（隐藏资源，未取得显示名称）”交互已取消；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。已关联选中本交互的 Autonomy 决策。

31. **06:28:11** — Nyssa Landry通过 Autonomy 选择“疯狂拖地”，已成功入队；记录了 1 层选择。目标：\*\* 调试 \*\*。请求来源：自主选择。入队或进入执行不代表行为完成。

32. **06:27:22** — Nyssa Landry移除Buff：寒酸的装潢。[用途待解释：保留原始条目]

33. **06:27:22** — Nyssa Landry新增Buff：buff_Environment_Hidden_Positive（隐藏资源，未取得显示名称）。

34. **06:27:22** — Nyssa Landry移除Buff：buff_Environment_Hidden_Negative_Snob（隐藏资源，未取得显示名称）。

35. **06:26:42** — Nyssa Landry的“SocialPickerSI（隐藏资源，未取得显示名称）”交互已自然结束，目标为Ava Landry；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。未取得已提交的决策明细。

36. **06:26:42** — Nyssa Landry的“autonomousSimPicker_SocialObservation_FamiliarChat（隐藏资源，未取得显示名称）”交互已自然结束；自主选择。玩法结果分支：NONE（与交互退出类型分别记录）。未取得已提交的决策明细。

37. **06:26:42** — Nyssa Landry的“aggregate_SocialObservation_Chat（隐藏资源，未取得显示名称）”交互已自然结束；自主选择。已关联选中本交互的 Autonomy 决策。

38. **06:26:42** — Nyssa Landry的“stand_Passive（隐藏资源，未取得显示名称）”交互已取消；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。已关联选中本交互的 Autonomy 决策。

39. **06:26:42** — Nyssa Landry通过 Autonomy 选择“aggregate_SocialObservation_Chat（隐藏资源，未取得显示名称）”，已成功入队；记录了 1 层选择。请求来源：自主选择。入队或进入执行不代表行为完成。

40. **06:26:02** — Nyssa Landry移除Buff：buff_Food_HasEaten_withinHour（隐藏资源，未取得显示名称）。

41. **06:25:35** — Nyssa Landry的“SocialPickerSI（隐藏资源，未取得显示名称）”交互已自然结束，目标为Lila Landry；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。未取得已提交的决策明细。

42. **06:25:35** — Nyssa Landry的“autonomousSimPicker_SocialObservation_FamiliarChat（隐藏资源，未取得显示名称）”交互已自然结束；自主选择。玩法结果分支：NONE（与交互退出类型分别记录）。未取得已提交的决策明细。

43. **06:25:35** — Nyssa Landry的“aggregate_SocialObservation_Chat（隐藏资源，未取得显示名称）”交互已自然结束；自主选择。已关联选中本交互的 Autonomy 决策。

44. **06:25:35** — Nyssa Landry的“stand_Passive（隐藏资源，未取得显示名称）”交互已取消；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。已关联选中本交互的 Autonomy 决策。

45. **06:25:35** — Nyssa Landry通过 Autonomy 选择“aggregate_SocialObservation_Chat（隐藏资源，未取得显示名称）”，已成功入队；记录了 1 层选择。请求来源：自主选择。入队或进入执行不代表行为完成。

46. **06:25:25** — Nyssa Landry的“和Lila聊天”交互已取消，目标为Lila Landry；自主选择。未观测到开始时间。未取得已提交的决策明细。

47. **06:25:25** — Nyssa Landry的“和〈未解析：0.String〉闲聊（名称未完整解析，待解释）”交互已取消；自主选择。未观测到开始时间。已关联选中本交互的 Autonomy 决策。

48. **06:19:14** — Nyssa Landry的“SocialPickerSI（隐藏资源，未取得显示名称）”交互已自然结束，目标为Lila Landry；自主选择。玩法结果分支：SUCCESS（与交互退出类型分别记录）。未取得已提交的决策明细。

49. **06:19:14** — Nyssa Landry的“autonomousSimPicker_SocialObservation_FamiliarChat（隐藏资源，未取得显示名称）”交互已自然结束；自主选择。玩法结果分支：NONE（与交互退出类型分别记录）。未取得已提交的决策明细。

50. **06:19:14** — Nyssa Landry的“aggregate_SocialObservation_Chat（隐藏资源，未取得显示名称）”交互已自然结束；自主选择。已关联选中本交互的 Autonomy 决策。

## 附录：全部当前 Buff 与关系标记

原包共有 58 个当前 Buff，很多是隐藏开关、计时器或技术状态，不能把每一个都解释成可见情绪。

| Buff 名称／原始技术名 | 是否可见 | 名称状态 |
| --- | --- | --- |
| Buff_Trait_Elder | 隐藏 | 无可用显示名 |
| buff_Lifestyles_CanUnlockLifestyles | 隐藏 | 无可用显示名 |
| buff_SelfDiscovery_CanMakeProgress | 隐藏 | 无可用显示名 |
| buff_Attraction_CanLearnAttractionRequiredStuff | 隐藏 | 无可用显示名 |
| buff_MasteryPerk_Hidden_Controller | 隐藏 | 无可用显示名 |
| buff_Hidden_SkillLocks | 隐藏 | 无可用显示名 |
| buff_ScandalSecret_AffordanceMods_TYAE | 隐藏 | 无可用显示名 |
| buff_Route_UsePhone_PC_Only | 隐藏 | 无可用显示名 |
| buff_BalanceSys_Hidden_Balance | 隐藏 | 无可用显示名 |
| 良好（默认为开启并隐藏） | 隐藏 | 已解析 |
| buff_SimPreference_HasPreference_Music | 隐藏 | 无可用显示名 |
| 我就爱这个调！ | 隐藏 | 已解析 |
| buff_SimPreference_HasPreference_Activity | 隐藏 | 无可用显示名 |
| buff_SimPreference_HasPreference_Color | 隐藏 | 无可用显示名 |
| buff_SimPreference_Color_HasLike | 隐藏 | 无可用显示名 |
| Buff_Trait_Snob | 隐藏 | 无可用显示名 |
| Buff_Trait_FamilyOriented | 隐藏 | 无可用显示名 |
| Buff_Trait_ArtLover | 隐藏 | 无可用显示名 |
| buff_Burnout_Hidden_MentalTracker | 隐藏 | 无可用显示名 |
| buff_Burnout_Hidden_CreativeTracker | 隐藏 | 无可用显示名 |
| buff_Region_generic | 隐藏 | 无可用显示名 |
| buff_GlobalTemperature_Freezing | 隐藏 | 无可用显示名 |
| buff_Temperature_DiscourageOutsideInteractions_Hidden | 隐藏 | 无可用显示名 |
| buff_Sim_Weather_Hidden_SnowOnGround | 隐藏 | 无可用显示名 |
| buff_SimPreference_GainCooldown | 隐藏 | 无可用显示名 |
| buff_Dust_Enabled | 隐藏 | 无可用显示名 |
| buff_Sim_Weather_Hidden_Cloudy_Partial | 隐藏 | 无可用显示名 |
| buff_ClothingCatagory_Temperature_Modifer | 隐藏 | 无可用显示名 |
| buff_FamilyDynamics_FamilyTropeAdventureCooldown | 隐藏 | 无可用显示名 |
| buff_FamilyDynamics_Hidden_Difficult | 隐藏 | 无可用显示名 |
| buff_Fear_BeingJudged_Tracker | 隐藏 | 无可用显示名 |
| buff_Food_HasEatenToday_Hidden | 隐藏 | 无可用显示名 |
| buff_UnfinishedBusiness_AutonomousGoalGain_Medium | 隐藏 | 无可用显示名 |
| buff_SimPreference_Timer_ComputerRecreation | 隐藏 | 无可用显示名 |
| buff_Object_ChessTable_Cooldown | 隐藏 | 无可用显示名 |
| buff_SimPreference_Timer_Guitar | 隐藏 | 无可用显示名 |
| buff_MasteryPerk_Hidden_Adventure_Cooldown_Generic | 隐藏 | 无可用显示名 |
| buff_ClothingCatagory_Temperature_ColdClothing | 隐藏 | 无可用显示名 |
| buff_Dust_ReactionCooldown | 隐藏 | 无可用显示名 |
| buff_Temperature_Hidden_Neutral | 隐藏 | 无可用显示名 |
| 打盹而恢复活力 | 可见 | 已解析 |
| buff_Fame_Quirk_PublicNumber_Hidden_Cooldown | 隐藏 | 无可用显示名 |
| 现在觉得活力充沛（不应被看见） | 隐藏 | 已解析 |
| 美味菜肴 | 可见 | 已解析 |
| Buff_Motives_InvisDecayMods_EnergyYellow | 隐藏 | 无可用显示名 |
| buff_Holiday_Base_Singing_Terrain | 隐藏 | 无可用显示名 |
| buff_Holiday_Base_Active | 隐藏 | 无可用显示名 |
| buff_HolidayTraditions_Hidden_Sing_BoxOfDecorations | 隐藏 | 无可用显示名 |
| buff_HolidayTraditions_Hidden_BeFestive | 隐藏 | 无可用显示名 |
| buff_HolidayTraditions_Hidden_Sing_HolidayTree | 隐藏 | 无可用显示名 |
| buff_HolidayTraditions_Hidden_GiveGiftsActive | 隐藏 | 无可用显示名 |
| buff_HolidayTraditions_Hidden_Sing_PresentPile | 隐藏 | 无可用显示名 |
| buff_HolidayTraditions_Hidden_Sing_GrandMeal | 隐藏 | 无可用显示名 |
| buff_HolidayTraditions_Hidden_FatherWinter | 隐藏 | 无可用显示名 |
| buff_Holidays_Hidden_BaseSocials | 隐藏 | 无可用显示名 |
| 现在觉得很好（不应被看见） | 隐藏 | 已解析 |
| 生活气息 | 可见 | 已解析 |
| buff_Environment_Hidden_Negative_Snob | 隐藏 | 无可用显示名 |

**Lila Landry**：已碰面（不应被看见）；bit_NoLongerFriends；被讨厌；SpecialBits_Greeted；随意的谈话；孙女；伤心了。

**Eddie Landry**：已碰面（不应被看见）；bit_NoLongerFriends；被讨厌；随意的谈话；SpecialBits_Greeted；随意的谈话；随意的谈话；随意的谈话；困难；孙子；争吵记仇；relationshipBit_Fear_BeingJudged_MeanTracker。

**Ava Landry**：已碰面（不应被看见）；bit_NoLongerFriends；被讨厌；契合度不佳；SpecialBits_Greeted；孙女；争吵记仇。

## 附录：原包中的 19 项资源说明

- **我就爱这个调！**：Nyssa如鱼得水！因为她在做自己喜欢的事情，因此她能从中获得更多乐趣！
  证据位置：`snapshot.buffs.value[11].description`。

- **打盹而恢复活力**：没什么比小睡片刻更能让身体重新充满活力。
  证据位置：`snapshot.buffs.value[40].description`。

- **美味菜肴**：令人满足的一餐！我会饱上好一阵子。
  证据位置：`snapshot.buffs.value[43].description`。

- **生活气息**：一点点灰尘给Nyssa的家增添了一抹舒适的烟火气。不会太干净也不会太脏乱，刚刚好。这种自然温馨的环境有助于模拟市民们彼此熟悉、发展感情、一起玩耍和享受社交。
  证据位置：`snapshot.buffs.value[56].description`。

- **buff_Environment_Hidden_Negative_Snob（隐藏资源，未取得显示名称）**：一个优美的环境能使你的模拟市民心情变好。
  证据位置：`snapshot.buffs.value[57].description`。

- **已碰面（不应被看见）**：已碰面（不应被看见）
  证据位置：`snapshot.relationships.value[0].bits[0].description`。

- **被讨厌**：双方关系紧张，有可能会变得扭曲！
  证据位置：`snapshot.relationships.value[0].bits[2].description`。

- **随意的谈话**：只是一般的礼貌性的对话。  试试聊天，看看会发生什么事。
  证据位置：`snapshot.relationships.value[0].bits[4].description`。

- **伤心了**：Nyssa觉得Lila对不起自己，待在Lila身边会让Nyssa回想起伤痛。
  证据位置：`snapshot.relationships.value[0].bits[6].description`。

- **随意的谈话**：只是一般的礼貌性的对话。  试试聊天，看看会发生什么事。
  证据位置：`snapshot.relationships.value[1].bits[5].description`。

- **随意的谈话**：只是一般的礼貌性的对话。  试试聊天，看看会发生什么事。
  证据位置：`snapshot.relationships.value[1].bits[6].description`。

- **随意的谈话**：只是一般的礼貌性的对话。  试试聊天，看看会发生什么事。
  证据位置：`snapshot.relationships.value[1].bits[7].description`。

- **困难**：当模拟市民身处困难的家庭动力中时，他们就不会像其他大多数家庭成员一样，能与人融洽相处。在社交时，他们通常倾向于抱怨，甚至是进行刻薄的互动。他们也更有可能对彼此产生暴怒的情绪。
  证据位置：`snapshot.relationships.value[1].bits[8].description`。

- **争吵记仇**：Nyssa和Eddie还有没解决的纠葛。至少对Nyssa来说，他们之间的冲突还没有结束，关系非常紧张。
  证据位置：`snapshot.relationships.value[1].bits[10].description`。

- **契合度不佳**：从这两位模拟市民喜欢和不喜欢的事物可以看出，他们之间社交契合度不佳。他们一开始就不会走得特别近，但说不定还会更糟。虽然建立友谊对他们来说是个挑战，但总是值得一试的！
  证据位置：`snapshot.relationships.value[2].bits[3].description`。

- **争吵记仇**：Nyssa和Ava还有没解决的纠葛。至少对Nyssa来说，他们之间的冲突还没有结束，关系非常紧张。
  证据位置：`snapshot.relationships.value[2].bits[6].description`。

- **buff_Environment_Hidden_Positive（隐藏资源，未取得显示名称）**：一个优美的环境能使你的模拟市民心情变好。
  证据位置：`history.events[10].before.description`。

- **Buff_hidden_MoodReplacement（隐藏资源，未取得显示名称）**：当主宰心情指数的特征应被阻挡时，替换心情指数
  证据位置：`history.events[20].before.description`。

- **寒酸的装潢**：这所房子的美学设计仅仅是乏味与单调而已。
  证据位置：`history.events[31].before.description`。

## 来源与边界

- Context 状态：complete；历史仍在记录，本包只返回最近 50 条，已标记截断。

- 请求不包含内部历史层，但当前分层仍有少量隐藏技术条目混入。

- 物件状态字段对 Sim 不适用；房间名称、完整当日经历、真实谈话内容未提供。

- 本文只做可读性编排，没有修改原始数据或补入另一份日志。

- [原始 Context JSON](<C:/Users/ZixuanMin/Documents/Electronic Arts/The Sims 4/ContextOverlay/runs/9ba4280cb22d4c65b469cad11439a0c7/context-6c73a45969a341068e94452721cdc19d.json>)

- 原始文件 SHA-256：`621fa8ad6822639c12413d7e318415a40f5b9031c7407f129b16de5038e7c30b`。
