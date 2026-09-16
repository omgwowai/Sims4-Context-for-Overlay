# Autonomy 初始化修复验证（0.7.1）

2026-09-16。针对[0.7.0 首局实机](2026-09-16-autonomy-first-live.md)的 `AttributeError: get_multitasking_roll` 完成代码修复。**源码与本机安装版本为 0.7.1，全量 222 项测试通过；已按用户要求构建并安装，修复后的实机采集尚未验收。** API／SDK 保持 1.1.0，schema 保持 1。

后续证据：[新一局暂停实机快照](2026-09-16-autonomy-paused-live.md)已确认初始化修复生效，取得 1,162 条决策及对应交互；下文保留修复与安装阶段的验证记录。立即执行、独立目标选择层与完整会话收尾仍需后续验收。

## 修复行为

旧初始化直接要求基础 `sims.sim.Sim` 具有多任务 getter，实际 EA 组件把转发方法挂到具体实例所属类型，基础类可以没有该方法。现在在 `choose_best_interaction` 原函数执行前，以本次请求的实际 Sim 类型静态检查并按需包装其转发方法。后创建的类型及后挂载的方法在首次使用时接入；已包装的方法和继承的包装不会重复安装。

不再同时包装组件 getter 与基础 Sim getter。EA 实际调用哪个类型上的转发入口，就从该入口的原始返回值观察多任务随机值；采集器不调用 getter 来探测或补算。关闭采集时恢复自己的包装，保留外部替换；被外部删除的方法也不会导致其他 Hook 的清理中断。

多任务入口安装失败时，单独增加 `multitasking_hook_unavailable` 与错误诊断，其余选择／队列入口继续工作。真实加权抽取、均匀抽取和 GSI 能提供的证据仍保留，无法取得的数据明确缺失。每层 `multitasking` 新增 `hook` 和 `roll_source`，区分从 getter 实际返回值、原生 GSI 取得数据，或未观测到；不把缺失随机值描述成已观测。

## 验证证据

测试夹具改为执行本机游戏的原生 `build_exported_func` 与 `apply_component_methods`，让基础 Sim 保持没有 getter，只对具体类型导出。应用修复前，该夹具在原有候选采集测试的初始化阶段重现实机错误；应用修复后通过。

| 检查 | 结果 |
| --- | --- |
| 全量 Python 3.7 测试 | 222 项通过，无跳过 |
| Autonomy 专项 | 31 项通过，无跳过，较此前新增 7 项 |
| 游戏字节码入口核验 | 17 项通过，包含原生导出函数及具体类型挂载函数 |
| 基础类缺失、具体类型已导出 | 初始化成功，取得实际候选池和 getter 返回值 |
| 安装后才创建的新类型 | 首次选择前接入，成功入队后产生关联决策 |
| 子类继承已包装方法 | 不重复包装；关闭时保留原继承关系 |
| 类型入口缺失、之后才导出 | 先以可用 GSI 证据降级采集，随后自动接入；保留本局曾有缺口的诊断 |
| 可选 Hook 安装失败且无 GSI | 仍保留加权选择与入队决策，随机值明确为缺失 |
| 原生惰性 getter 开关对照 | 两次选择均调用 getter 两次、加权随机两次、惰性随机生成一次；关闭采集后所选实例和次数完全一致 |
| 方法被外部删除后的关闭清理 | 其他 Hook 正常恢复，不重新添加被删方法 |

既有候选五项限制、原概率、缓存关联、成功入队后取消、立即执行失败、子行为选择与评分组成测试继续通过。这里的原生函数使用受控依赖执行，不能当作真实队列／游戏性能的验证。

专项结果、函数参数、字节码哈希和受控输出见[机器可读报告](2026-09-16-autonomy-initialization-fix.json)。可重复执行：

```powershell
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 scripts/validate_autonomy.py --output .validation/autonomy-initialization-fix.json
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 -m unittest discover -s tests -q
```

## 本地安装记录

2026-09-16 15:19:20（北京时间）通过现有 Python 安装器完成安装。安装前确认游戏已退出，24 个运行时源文件与新构建 manifest 一致，包内元数据及字节码校验通过。

- 安装目标：`C:/Users/ZixuanMin/Documents/Electronic Arts/The Sims 4/Mods/ContextOverlay/ContextOverlay.ts4script`。
- 新版 SHA-256：`086ddade457fce270e6592b65f092d338940cd282bcd3a93408a80a5186d182a`。
- 旧版 SHA-256：`91d65fd70548faac7519c96f19d68afcd43ea2ae59ec31e9317e60f6d275c81b`。
- 旧版备份：`.validation/install-backups/20260916T071920040583Z/ContextOverlay.ts4script`。
- 安装回执：`.validation/inspector-install.json`；安装后哈希匹配，配置与游戏选项摘要不变。

本次只构建游戏所需的脚本文件，没有生成 ZIP 分发包或启动游戏。用户已授权以后小修复通过检查后自动本地安装，约定已写入根目录 [AGENTS.md](../../AGENTS.md)。

## 安装后的验收条件

1. 新运行加载 0.7.1，`autonomy.enabled=true`、`coverage.state=installed`、`last_error=null`。首次选择前 `coverage.multitasking_roll=pending_sim_type` 是正常的延迟接入状态；选择发生后应变为 `native_sim_type_method`。
2. 实际发生并成功入队的自主行为产生 `autonomy.decision`，能与对应交互互相引用；核对每层最多五项且包含赢家，检查评分组成和原概率。
3. 若 `coverage.multitasking_roll=partial`，检查 `multitasking_hook_unavailable` 和具体阶段的 `roll_source`。该状态保留本局出现过的缺口，即使后续方法已接入，也不会抹去前面的失败。
4. 继续验收真实缓存、聊天子行为、立即执行、GSI 成本与日志体积。旧局没有采到的决策过程无法由本次修复追溯补齐。
