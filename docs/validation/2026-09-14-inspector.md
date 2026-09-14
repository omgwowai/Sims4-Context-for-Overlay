# 原生状态与历史窗口：离线验证与安装（0.3.0）

日期：2026-09-14。分支：`zixuan/first-runtime-implementation`。

本报告保留 0.3.0 当时的离线检查与安装状态。后续用户亲自试验并确认窗口出现，反馈需求变化刷屏与事件行红色 X 占位图标；这些反馈及 0.3.1 的修改见[后续记录](2026-09-14-inspector-refinement.md)。当前构建和安装包已更新，不必与下面的旧摘要一致。

## 交付状态

已实现、完成离线检查、构建并安装 0.3.0。**本轮没有启动或控制游戏；新窗口的实际外观、点击菜单兼容性和完整游戏行为等待用户亲自试验。** 使用方式与验收步骤见[手动试验说明](../inspector-manual-test.md)。

0.1.0 的[实机结果](2026-09-14-first-round.md)和 0.2.0 的[索引／容量测量](2026-09-14-history-optimization.md)作为历史证据保留。此次安装包含 0.2.0 的索引、分页、资源预算与 200,000 条事件上限，不能把之前的 0.1.0 实机结论直接延伸到新版本。

## 本次实现

- 普通点击当前地块内 Sim／物件时追加“查看状态与历史”菜单。保留原菜单生成器、避免重复和转发入口；只读即时交互不加入自主选择。
- 原生 OBJECT_TEXT 单选窗口：固定被点击实体、概览、状态分类、分类分页、历史筛选、上一页／下一页、单条事件详情、长说明分段、刷新与关闭。
- 概览最多展示 5 条最近更新的主要事件；历史默认近 24 游戏小时，按首次观测时间倒序，每页 15 条，可筛选交互／状态变化、时间与内部层。
- 从 Collector／Recorder 内存接口直接读取，不通过文件交换。页面固定历史版本，关闭、调整筛选、返回概览和运行结束释放查询；过期或预算拒绝提供恢复操作。
- 查看工具交互从记录和当前交互快照中排除。UI 初始化错误单独报告，配置开关 `inspector_enabled` 默认开启，`co.inspect` 为备用入口。
- 仍交付单个 `.ts4script`，通过 EA 的 tuningless immediate interaction 和 AOP 接入，不增加 `.package` 或第三方框架依赖。

实现入口：[导航层](../../src/context_overlay/inspector.py)、[EA 界面接入](../../src/context_overlay/native_ui.py)、[生命周期](../../src/context_overlay/game_runtime.py)。EA 原始类与方法登记在[接口文档](../runtime-interfaces.md)。

## 离线验证

CPython 3.7.9 下 **63 项测试通过**（原 49 项加 14 项 UI 检查）。最后一次测试命令：

```powershell
& "$env:LOCALAPPDATA/Sims4ContextDev/python37/python.exe" -X utf8 -m unittest discover -s tests -p 'test_*.py' -q
```

新增检查覆盖：固定目标、概览数量与无文件导出、Collector 停用时仍可查历史、状态与长文本分页、历史上一页／下一页与固定修订、时间／类型／内部层筛选、游标过期与刷新、查询预算拒绝、关闭及运行结束释放、旧回调隔离、查看操作不污染事件、原生选择确认／取消、菜单追加／去重／卸载和执行前目标校验。原有生命周期测试补充窗口清理，控制台测试补充 `co.inspect` 分发。

原生 UI 桥接测试使用游戏模块替身，检查项目传入参数和响应处理；**没有加载实际游戏 UI，不能证明布局、窗口暂停、选中高亮或鼠标操作已经通过。**

另外使用之前保存的 Nyssa Landry 与汉堡蛋糕数据包，通过新导航层生成 **34 个页面内容样本**（20 + 14），未出现渲染内容异常。它们是纯文本／列表数据，不是游戏截图。源数据包只导出了部分状态字段；缺少的字段在该离线试验中明确标为样本未提供，不补造实时数据。此检查不测量原生客户端绘制效果或完整游戏查询延迟。

测试源文件见 [test_inspector.py](../../tests/test_inspector.py)，机器可读的摘要、构建清单和安装结果见[证据记录](2026-09-14-inspector.json)。

## 构建与安装

- 包版本：0.3.0；schema：1；字节码魔数：`420d0d0a`，与本地游戏核对。
- 输出：`dist/ContextOverlay.ts4script`；15 个 Python 源文件编译入包。
- 安装：`C:/Users/ZixuanMin/Documents/Electronic Arts/The Sims 4/Mods/ContextOverlay/ContextOverlay.ts4script`。
- 新包 SHA-256：`2984df0458dfb9024f26dd0124426214e402c892e12faa68293bec37a629eea3`。
- 原 0.1.0 包 SHA-256：`78ec93373f82ec12ffc28e68f2ce53bf6c55a616156514c24e04af301ca2edf7`。
- 旧包备份：`C:/sources/Sims4-Context-for-Overlay/.validation/install-backups/20260914T084158319140Z/ContextOverlay.ts4script`。

通过新 [install.py](../../scripts/install.py) 安装：检查 Windows 游戏进程、验证构建清单、备份并核验旧包、原子替换本项目脚本文件、回读新包摘要。备份位于 Mods 之外。安装前后 Options.ini 与 ContextOverlay/config.json 的摘要一致，没有运行 prepare/restore 脚本，没有编辑存档或游戏配置。

## 留给用户的实机检查

优先检查：普通点击菜单是否出现且不重复；当前 Sim、另一位 Sim、食物和普通物件的目标是否正确；原生选择行和确认按钮是否正常；状态详情可读性；做饭／吃饭后的历史；翻页与返回；游标过期恢复；关闭／重开和旅行后的运行隔离。

如果窗口失败，先通过 `co.status` 区分 inspector 与 recorder 的状态，再用 `co.inspect` 区分菜单问题与窗口问题，并保留 runtime.log 相关错误。尚未实现常驻／可拖动浮窗、自动刷新、跨运行历史、自由布局或跨 MOD 稳定公开 SDK。
