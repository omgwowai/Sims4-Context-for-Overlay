# Autonomy 0.7.0 实现验证

2026-09-16。测试使用 CPython 3.7.9 和本机游戏 1.126.73.1030 的字节码，**未安装、未启动游戏，不是实机采集结果**。实现边界见[使用说明](../autonomy-capture.md)，原始设计见[确认方案](../autonomy-capture-plan.md)。

后续状态：该构建随后已安装，但[首局实机检查](2026-09-16-autonomy-first-live.md)发现 `get_multitasking_roll` 的基础 Sim 方法挂载假设不成立，Autonomy 初始化失败。下列测试结果仍是当时的离线结果；夹具预先定义了基础类方法，未覆盖实际仅向具体子类导出的情况。

## 验证内容

全量 **215 项测试通过**，包含本轮 24 项专项测试；原有事件源的 **58 个 Hook 符号**核验无缺失，Autonomy 另核验 15 个字节码入口。`git diff --check` 通过。

`scripts/validate_autonomy.py` 可重复执行 24 项专项测试、15 个原生符号核验，并生成[机器可读报告](2026-09-16-autonomy-implementation.json)。其中原生选择、路线权重换算、提供者选择、mixer 生成器与评分、AOP 执行和立即执行入口均调用安装包内的函数体，依赖使用受控替身。队列本体只核验符号；“通知后返回失败”由队列替身构造，不代表验证了所有游戏队列分支。

覆盖以下边界：

- 实际八项选择池、游戏自身 Top 5、赢家排名第八时强制保留、原概率和遗漏质量、并列与 N=1。
- 确定性最高分、均匀抽取、原分为零但路线权重为正；不混淆原始分数和最终权重。
- 多任务抽中后被阻止不写正式决策；脚本请求保留来源并跳过不适用门槛。
- 入队通知后完整提交失败；入队成功后未开始即取消；立即执行返回 false 或抛异常仍保留进入执行证据；只有开始通知不够。
- 同请求复用、缓存倒序提交、行为／目标两层选择、失效清理、无关交互和纯延续不关联。
- 原生提供者／组别／具体行为三层；缓存评分不重新计算；生成器在 `next/send/throw/close`、交错执行和暂停时保持正确上下文。
- 所保留候选读取可用的关系／Buff 评分组成，缺失项标记；筛除原因只计数；落选者不进入实体索引。
- 包装静态／类方法、继承与退出恢复；EA 组件导出捕获旧函数时仍观测实际 Sim 入口；外部 GSI 使用者接管后不被关闭。
- 开关前后所选实例、随机调用数和惰性多任务 getter 调用数一致；原异常对象与返回值不变。
- 弱引用、过期、范围退出、容量／内存上限、身份校验和停止清理。

报告附带一个**受控输入示例**：原分 1～8 的八项候选，实际选中第八名。输出保留分数 8、7、6、5、1，赢家原概率为 1/36，遗漏概率为 9/36。示例只验证格式、关联与中文表达，不来自用户游戏。

## 可重复执行

```powershell
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 scripts/validate_autonomy.py
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 -m unittest discover -s tests -q
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 scripts/build.py --strings .local/resource-semantics/strings_zh.json --string-sources .local/resource-semantics/string_sources.json
```

构建和分发分别处理；按项目 [AGENTS.md](../../AGENTS.md)，日常修改不自动生成 ZIP 分发包，仅在用户明确要求时运行打包脚本。

## 实机仍需确认

1. 新包加载后的 `autonomy.coverage.state=installed`、`last_error=null` 和两个缓冲的丢弃计数。
2. 正常生活与聊天时的实际决策覆盖、缓存提交关联、入队取消和立即交互表现；确认没有改变游戏行为。
3. 游戏自身 GSI 生成与归档开销、总帧耗时、每局日志体积及缓冲峰值。离线回调耗时不能作为游戏性能结论。
4. 原生 Inspector 的长详情分段、内部层筛选及资源名称表现。

评分明细存在游戏未提供／不能唯一关联的字段，尤其 mixer 的个别倍率；GSI 前的淘汰原因也未覆盖。报告保留缺失项，不根据最终动作反推动机。此前的运行日志无法补出本次新增证据。
