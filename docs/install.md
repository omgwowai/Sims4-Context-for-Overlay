# 安装与使用

拿到试用包后先装起来，跑一次自检。自己的 Overlay 怎么接，接着看[快速接入](quickstart.md)。当前包为 0.9.0，已在游戏 `1.126.73.1030`、简体中文环境测试；其他版本需要再确认。

## 安装

1. 从[团队飞书文档](https://omgwowai.feishu.cn/wiki/AAvPw03vJiR1NSkdDtmcxP04ng6)的附件获取已编译的 `ContextOverlay-0.9.0-Windows.zip` 并完整解压。GitHub 源码 ZIP 没有游戏脚本包，源码用户先按[开发说明](development.md)构建。
2. 退出《模拟人生 4》，双击 `Install.cmd`。安装不要求 Python 或管理员权限。
3. 安装器选择含 `Mods` 和 `Options.ini` 的游戏用户目录，包括常见 OneDrive 文档目录；这不是游戏程序安装目录。无法唯一确定时会提示选择。
4. 游戏选项启用“自定义内容与模组”和“允许脚本模组”，按游戏要求重启。

安装器将完整的 `ContextOverlay.ts4script` 放入 `Mods/ContextOverlay/`，不要解压这个文件，也不要在其他 Mods 子目录保留同名副本。

安装前检查游戏进程、包哈希、内嵌构建信息和 Python 3.7 字节码。源码目录使用默认构建时，还比较全部运行时源文件与 manifest；源码有新增、删除或变动时必须重新构建。配置、存档及其他 MOD 不随普通安装修改。

命令行安装与预览：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -UserData "D:\My Documents\Electronic Arts\The Sims 4" -NonInteractive
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -UserData "D:\My Documents\Electronic Arts\The Sims 4" -WhatIf
```

不确定安装目录时可先加 `-WhatIf` 预览，它不会安装文件。

## 查看状态与历史

进入地块后，点击 Sim 或物件的“查看状态与历史”。备用入口为控制台 `co.inspect`，默认查看当前操控 Sim；`co.inspect object 物件实例ID` 可指定物件。

首页按钮依次为“当前状态 → 历史事件 → 刷新 → 关闭”。状态和历史采用横向文字行，长详情放在正文中；详情包括来源、缺失说明和已取得的资源描述。历史可以筛选类型和内部步骤、翻页，Autonomy 详情按层展示候选、原概率及评分。

- 需求数字是游戏内部单位，不是百分比；多个同时运行的交互可以并存。
- 历史只包含开始记录以后观察到的事实；退出、取消、失败和未知结果分别显示。
- 刷新会创建新查询；历史分页冻结原查询的修订，翻页不会自动看到新事件。关闭窗口时释放分页资源。
- 游戏窗口默认隐藏内部层。聊天等内部 Autonomy 决策需开启“含内部步骤”；离线 Markdown 的筛选方式见开发说明。
- 历史默认包含游戏与外部事件，可在筛选中选择来源。这里只显示关联到所查看实体的记录；无实体记录通过 API 或离线报告查看。
- 0.9.0 起正常旅行保留本次会话历史，可查看之前地块已观测的事件；读档、回主菜单、重启游戏或 `co.restart` 开启新会话。历史仍受 FIFO 容量限制。当前状态只读取当前地块实体。
- 资源说明的“基础说明”“条件提示”“静态参考”均有不同证据边界，不能当作当前全部生效的游戏效果。

## 文件与诊断

游戏用户目录下：

| 路径 | 用途 |
| --- | --- |
| `Mods/ContextOverlay/ContextOverlay.ts4script` | 已安装脚本 |
| `ContextOverlay/config.json` | 可选配置，省略项使用默认值 |
| `ContextOverlay/runtime.log` | 模块启动、停止及异常诊断 |
| `ContextOverlay/runs/<session_id>/journal.jsonl` | 本次运行的追加事件与观测日志 |
| `ContextOverlay/runs/<session_id>/context-<request_id>.json` | Context、历史或附近实体导出 |
| `ContextOverlay/install-receipt.json` | 安装版本、哈希、时间及回退位置 |
| `ContextOverlay/install-backups/` | 上一份内容不同的脚本包 |

`co.status` 显示各模块、窗口、入口覆盖及写入状态；`recorder.persistence.pending_exports` 和 `written_exports` 分别为待完成和已完成导出数。导出返回的 `queued` 只表示入队，具体文件须等待返回路径上的完整 JSON 出现。`co.export` 导出当前 Sim 的 Context；`co.history sim active 50 true` 导出近期历史及内部步骤。

没有菜单时先确认脚本 MOD 已启用、安装层级和重复包，再查看 `runtime.log`、`co.status` 中的 `inspector` 与启动错误。记录失败会明确报告，不能把空历史当作游戏没有发生事件。

历史日志可以在游戏退出后删除；不要删除仍在写入的运行目录。删除输出不会清空游戏存档，但对应过去的事件证据将无法继续查询。重新加载存档会建立新运行；普通旅行继续使用原运行目录。

## Overlay 接口手动验收

加载一个可操控 Sim 的地块后暂停，按 `Ctrl+Shift+C` 打开游戏控制台：

1. 输入 `co.api_test`。它通过公共 API 写入两条 `context_overlay.selftest.<唯一标识>` 来源的测试事件，其中一条关联当前 Sim，另一条无实体。只写日志，不修改人物或玩法状态。正常应显示 `passed: true`、`checks: 9`。
2. 稍等一两秒，输入 `co.api_verify`。同一运行内应显示 `check: durable_write`、`passed: true`，确认测试记录已经落盘。
3. 输入 `co.api_inspect`，关闭控制台查看窗口。命令直接打开自检关联的 Sim，自动选择“来源：外部”和本次运行全部时间；无需手动寻找人物。应看到一条 `外部 · context_overlay.selftest.…` 记录，详情应显示“Overlay 接口自检：中文与 JSON 读写正常。”及 `private_state` 数组。切到“来源：游戏”后，这条记录应消失。若正文较长，可以翻段查看。
4. 让 Sim 通过正常旅行去另一个地块，等待加载完成后输入 `co.api_verify`。应显示 `check: travel_history`、`passed: true`。检查包含关联／无实体外部记录、增量 checkpoint、去重以及自检前取样的游戏记录；`zone_visit` 应增加，session 保持相同。
5. 输入 `co.api_inspect`，旅行前那条外部记录应仍然可见。也可返回原地块再次执行步骤 4–5。窗口默认近 24 游戏小时，普通历史若需要更早内容请选择“本次运行全部时间”。
6. 最后可输入 `co.restart`，再输入 `co.api_verify`，应显示 `check: old_session_rejected`、`passed: true`。这只重启记录模块、开启新历史，不修改游戏存档；此后旧测试记录不再通过当前 API 可读。

从旅行前到步骤 6 完成前不要再次运行 `co.api_test`，它会覆盖验收报告中的基准引用。重复运行自检会再增加两条独立测试记录，不删除既有记录。验证报告固定保存为游戏用户目录下 `ContextOverlay/api-self-test.json`；失败时保留该文件和 `runtime.log`。命令不依赖 `development_driver`，无需修改配置。

这组检查验证游戏内 API 链路、去重、来源筛选、分页、增量与运行切换；第三方 MOD 的真实接入仍需开发者试用。视觉检查由玩家操作确认。

## 升级、回退与卸载

升级前退出游戏，重新运行安装器。相同内容已安装时直接退出；新包安装并校验成功后只保留上一份内容不同的旧包，更早的安装器备份自动清理。

回退时退出游戏，按安装回执中的备份路径将旧 `.ts4script` 复制回 `Mods/ContextOverlay/`。备份保留的是旧包本身；不要用新包的 manifest 验证旧包。

卸载时退出游戏，移除 `Mods/ContextOverlay/`。用户目录下的 `ContextOverlay` 包含配置、输出和备份，可按保留需要另行处理。其他 MOD 和存档不属于本模块卸载范围。
