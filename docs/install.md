# 安装与使用

当前源码包为 **ContextOverlay 0.11.0 / API 2.3.0 / schema 2**，SDK 为 **2.3.0**。本页覆盖源码构建包的本地安装和游戏内基础自检；下游 MOD 接入请继续看[快速接入](quickstart.md)。

## 安装前确认

- 游戏必须已经退出。安装器会检查 TS4、TS4_x64 和 TS4_DX9_x64，不会替你关闭游戏。
- 目标必须是游戏用户目录，即同时包含 Mods 和 Options.ini 的目录，不是游戏程序安装目录。
- 当前源码 checkout 需要先在 dist/ 生成 ContextOverlay.ts4script 和 build-manifest.json。安装器不接受只有源码的目录，也不接受与 manifest 不一致的旧包。

## 从源码构建并安装

在仓库根目录使用游戏兼容的 CPython 3.7：

    $coPython = "C:\path\to\python37.exe"
    & $coPython -B -X utf8 scripts/build.py --game "D:\Games\The Sims 4"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -UserData "D:\Documents\Electronic Arts\The Sims 4" -NonInteractive

如果需要更新官方简体中文文本资源，先按[开发与调试](development.md#检查与构建)生成 .local/resource-semantics/，再把 --strings 和 --string-sources 传给 scripts/build.py。日常只改文档时不需要重建或安装。

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
