# 安装与使用

## 安装

1. 获取已编译的 Windows 包并完整解压。GitHub 源码 ZIP 没有游戏脚本包，源码用户先按[开发说明](development.md)构建。
2. 退出《模拟人生 4》，双击 `Install.cmd`。安装不要求 Python 或管理员权限。
3. 安装器选择含 `Mods` 和 `Options.ini` 的游戏用户目录，包括常见 OneDrive 文档目录；这不是游戏程序安装目录。无法唯一确定时会提示选择。
4. 游戏选项启用“自定义内容与模组”和“允许脚本模组”，按游戏要求重启。

安装器将完整的 `ContextOverlay.ts4script` 放入 `Mods/ContextOverlay/`，不要解压这个文件，也不要在其他 Mods 子目录保留同名副本。

安装前检查游戏进程、包哈希、内嵌构建信息和 Python 3.7 字节码。源码目录使用默认构建时，还比较全部运行时源文件与 manifest；源码有新增、删除或变动时必须重新构建。配置、存档及其他 MOD 不随普通安装修改。

命令行安装与预览：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Profile "D:\My Documents\Electronic Arts\The Sims 4" -NonInteractive
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Profile "D:\My Documents\Electronic Arts\The Sims 4" -WhatIf
```

`Install.cmd` 与 `scripts/install.ps1` 是统一入口；原 `scripts/install.py` 已删除。

## 查看状态与历史

进入地块后，点击 Sim 或物件的“查看状态与历史”。备用入口为控制台 `co.inspect`，默认查看当前操控 Sim；`co.inspect object 物件实例ID` 可指定物件。

首页按钮依次为“当前状态 → 历史事件 → 刷新 → 关闭”。状态和历史采用横向文字行，长详情放在正文中；详情包括来源、缺失说明和已取得的资源描述。历史可以筛选类型和内部步骤、翻页，Autonomy 详情按层展示候选、原概率及评分。

- 需求数字是游戏内部单位，不是百分比；多个同时运行的交互可以并存。
- 历史只包含开始记录以后观察到的事实；退出、取消、失败和未知结果分别显示。
- 刷新会创建新查询；历史分页冻结原查询的修订，翻页不会自动看到新事件。关闭窗口时释放分页资源。
- 游戏窗口默认隐藏内部层。聊天等内部 Autonomy 决策需开启“含内部步骤”；离线 Markdown 的筛选方式见开发说明。
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

历史日志可以在游戏退出后删除；不要删除仍在写入的运行目录。删除输出不会清空游戏存档，但对应过去的事件证据将无法继续查询。下次进入地块会建立新运行。

## 升级、回退与卸载

升级前退出游戏，重新运行安装器。相同内容已安装时直接退出；新包安装并校验成功后只保留上一份内容不同的旧包，更早的安装器备份自动清理。

回退时退出游戏，按安装回执中的备份路径将旧 `.ts4script` 复制回 `Mods/ContextOverlay/`。备份保留的是旧包本身；不要用新包的 manifest 验证旧包。

卸载时退出游戏，移除 `Mods/ContextOverlay/`。用户目录下的 `ContextOverlay` 包含配置、输出和备份，可按保留需要另行处理。其他 MOD 和存档不属于本模块卸载范围。
