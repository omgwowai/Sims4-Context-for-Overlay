# 安装与使用

当前发行版本为 **ContextOverlay 0.14.1 Preview / API、SDK 2.6.0 / schema 2**，游戏基线为 `1.126.73.1030`。本页覆盖 Windows 发行包、源码安装和游戏内自检；下游 MOD 接入请继续看[快速接入](quickstart.md)。

0.13.0 起，“查看状态与历史”出现在当前已加载区域（zone）内、已实例化且未隐藏的 Sim／物件的普通点击菜单中，包含门外、人行道和公共空间。主控家庭成员在地块外聊天时也可使用，无需等他们走进房屋地块或等待一次采集轮询；游戏区域仍须完成加载。Context、Records 和气泡采集使用相同范围，库存内容和未加载区域不在当前状态读取范围内。对本局已有身份记录但已经离开的人物，仍可用 `co.inspect sim <人物ID>` 查阅保留历史。

## 安装前确认

- 游戏必须已经退出。安装器会检查 TS4、TS4_x64 和 TS4_DX9_x64，不会替你关闭游戏。
- 目标必须是游戏用户目录，即同时包含 Mods 和 Options.ini 的目录，不是游戏程序安装目录。
- 当前源码 checkout 需要先在 dist/ 生成 ContextOverlay.ts4script 和 build-manifest.json。安装器不接受只有源码的目录，也不接受与 manifest 不一致的旧包。

## 使用 Windows 发行包

1. 从 [v0.14.1 Release](https://github.com/omgwowai/Sims4-Context-for-Overlay/releases/tag/v0.14.1) 下载 `ContextOverlay-0.14.1-Windows.zip` 及对应 `.sha256`。`ContextOverlay-SDK-2.6.0.zip` 只有客户端与文档，不含游戏脚本。
2. 核对 ZIP 的 SHA-256，再完整解压到一个普通目录。保留其中的 `dist/`、`scripts/` 和 `Install.cmd`，不要展开 `.ts4script` 文件。
3. 退出游戏后双击 `Install.cmd`。安装器会自动定位或提示选择游戏用户目录；发行包安装不需要另装 Python。
4. 按下文启用脚本模组并完整重启游戏。实际安装版本与哈希可在 `ContextOverlay/install-receipt.json` 核对。

发行包提供安装器、SDK 源码和文档；下面的构建及开发检查需要游戏 MOD 源码与开发工具，须在 Git checkout 中运行。

## 从源码构建并安装

在仓库根目录使用游戏兼容的 CPython 3.7：

    $coPython = "C:\path\to\python37.exe"
    & $coPython -B -X utf8 scripts/build.py --game "D:\Games\The Sims 4"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -UserData "D:\Documents\Electronic Arts\The Sims 4" -NonInteractive

上面的最小构建不嵌入中文文本字典。需要名称与说明解析时，按[开发与调试](development.md#检查与构建)生成 .local/resource-semantics/，再把 `--strings` 和 `--string-sources` 传给 scripts/build.py；已发布的 Windows 包包含构建时的简体中文词表。日常只改文档时不需要重建或安装。

安装器会依次检查：

1. 包文件和 build-manifest.json 是否存在；
2. 包 SHA-256、内嵌构建信息和 CPython 3.7 字节码魔数是否匹配；
3. 默认源码 checkout 的全部 .py / .json 是否和 manifest 一致；
4. 目标 Mods 目录中是否有重复的 ContextOverlay.ts4script；
5. 旧包备份、临时复制和安装后的目标哈希是否正确。

也可以先做完整的非写入预览：

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -UserData "D:\Documents\Electronic Arts\The Sims 4" -WhatIf

现有安装器会把脚本放到 Mods/ContextOverlay/ContextOverlay.ts4script，旧版本如有变化会备份到游戏用户目录的 ContextOverlay/install-backups/，只保留刚替换的那一份。配置、存档和其他 MOD 不会被普通安装修改。

## 游戏内自检

进入一个能操控 Sim 的地块后，在游戏设置中启用“自定义内容与模组”和“允许脚本模组”，按游戏要求重启。打开控制台执行：

1. co.api_test：通过公共 API 写入一条关联当前 Sim、另一条无实体的自检事件，不改变人物行为；应显示 passed: true。
2. co.api_verify：确认自检记录已经持久化；应显示 check: durable_write 和 passed: true。
3. co.api_inspect：打开自检关联人物的历史窗口，检查外部来源记录及其 JSON 内容。
4. 如需验证普通旅行，旅行到另一个地块并等待加载，再执行 co.api_verify；之后用 co.api_inspect 确认原记录仍可见。

具体检查项和输出字段以当前控制台提示为准；更完整的分层接口验收见[分层接口验收](event-views-validation.md)。

## Inspector 与气泡检查

普通生活模式下，点击当前区域内的有效 Sim／物件，选择“查看状态与历史”。人物可以在人行道或门外；仍在加载、已隐藏或没有当前世界实例时不应把无入口视为气泡捕捉失败。人物详情里的气泡栏目查询本次区域访问最近 5 个游戏分钟的发送事件，空窗口不表示此前从未有过气泡。

若选项存在但点击后没有窗口，检查 `runtime.log` 中的 `INSPECTOR OPEN / INSPECTOR NOT OPEN` 和错误诊断，以及 `co.status` 的 Inspector 状态。ContextOverlay 对已加载 DevBridge 的 `_quiet_choice` 有局部兼容处理，状态为 `devbridge_current_inspector_exempt`；不要求关闭全局静默模式，也不保证适配任意版本或其他 MOD 的窗口拦截。

最新一次 0.14.0 实测主要验证采集，用户没有点击 Inspector；现有离线窗口回归不能替代这里的实际点击验证。气泡原始事件、时间窗口与回查判据见[气泡事件说明](balloons.md)，已有证据见[验证摘要](validation.md)。

## 诊断文件

游戏用户目录下的 ContextOverlay/ 通常包含：

| 路径 | 用途 |
| --- | --- |
| runtime.log | 模块启动、停止和异常诊断 |
| runs/<session_id>/journal.jsonl | 当前会话的追加日志 |
| runs/<session_id>/run-status.json | 采集和关闭状态 |
| runs/<session_id>/persistence-status.json | 接收/落盘序号、队列和写盘错误 |
| runs/<session_id>/views/ | 自动生成的分层快照和质量文件 |
| config.json | 可选配置；省略项使用默认值 |
| install-receipt.json | 实际安装包版本、哈希和备份位置 |
| install-backups/ | 上一份被替换的脚本包 |

没有菜单或 API 不可用时，先确认安装层级、重复包和脚本模组设置，再看 runtime.log 与 co.status。recorder_failed、recorder_disabled 和持久化错误都不是“没有新事件”。

不要在日志仍写入时删除运行目录。退出游戏后可以清理不再需要的运行输出，但删除后对应历史证据无法继续查询；源码和当前文档由 Git 追踪，不需要把临时输出另行归档。
