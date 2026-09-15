# ContextOverlay：Windows 安装与试用

内部已发布版为 0.3.2，在 Windows、The Sims 4 `1.126.73.1030` 上完成首轮功能与界面检查；下载入口见[内部飞书文档](https://omgwowai.feishu.cn/wiki/AAvPw03vJiR1NSkdDtmcxP04ng6)。当前源码及本地候选构建为 0.5.0，新增公共 API v1，包含 0.4.0 的名称解析改进；仅完成离线验证，尚未安装、实机验收或更新飞书附件。公共 API / SDK 需要提供方 0.5.0 或兼容后续版本。安装步骤适用于这些版本；其他游戏版本需要另行核验。

## 安装

1. 取得 `ContextOverlay-版本-Windows.zip`，**完整解压到普通文件夹**。飞书附件当前仍为 `ContextOverlay-0.3.2-Windows.zip`。
2. 退出 The Sims 4，双击解压目录中的 **`Install.cmd`**。不需要安装 Python，也不需要管理员权限。
3. 安装器查找 Windows 的“文档”目录和常见 OneDrive 文档目录。只有一个有效游戏用户目录时自动选择；有多个或找不到时，会要求粘贴实际目录。这个目录应包含 `Mods` 和 `Options.ini`，一般为“文档/Electronic Arts/The Sims 4”，**不是游戏程序安装目录**。
4. 看到 `Installation complete` 即完成。若显示 `This exact package is already installed`，说明已安装同一份文件，无需重复操作。
5. 启动游戏，在“游戏选项 → 其他”启用“自定义内容与模组”和“脚本模组”，应用后重启游戏。
6. 读取存档并进入生活模式。普通点击当前地块的 Sim 或物件，选择 **“查看状态与历史”**。

安装结果为：

```text
游戏用户目录/
  Mods/
    ContextOverlay/
      ContextOverlay.ts4script
```

`.ts4script` 本身保持完整，**不要再次解压**，也不要放入更深的子目录。脚本只复制本项目的一个 MOD 文件；不会启动游戏、改动存档、其他 MOD、游戏选项或已有配置。

如果不能运行脚本，可手动将解压目录 `dist/ContextOverlay.ts4script` 复制到上述位置。升级前将旧文件移到 Mods 之外，确保只加载一份。游戏版本更新后，可能需要重新开启脚本模组。

## 使用与界面

- 首页：实体信息、状态摘要及最近 5 条主要事件；按钮顺序为 **当前状态 → 历史事件 → 刷新 → 关闭**。
- 当前状态：按分类查看需求、Buff、关系、当前交互或物件状态；列表为横向文字行，点击继续展开。
- 历史事件：默认近 24 游戏小时，每页 15 条；可切换时间范围、事件类型和内部步骤，点击事件查看参与者、来源、结果等详情。
- 刷新：重新读取当前实体状态。窗口为游戏原生模态窗口，打开时暂停，关闭后恢复之前的游戏速度。

当前只观察当前地块已实例化的角色和物件。新运行开始后历史逐步积累；默认不记录连续需求变化，但“当前状态”仍可查看需求。数据为空、未支持或范围外会分别表示。

详细操作见[窗口说明](inspector-manual-test.md)，供其他 MOD 使用的数据与调用示例见[开发接入说明](mod-integration.md)。

## 升级、回退与卸载

升级时退出游戏，运行新包的 `Install.cmd`。安装器校验 SHA-256、包内版本与 Python 字节码，发现重复的同名脚本包会停止。已有旧包先备份到 **游戏用户目录/ContextOverlay/install-backups/时间戳/**，校验后原子替换。回执为 `ContextOverlay/install-receipt.json`。

回退时退出游戏，把所需备份复制回 `Mods/ContextOverlay/ContextOverlay.ts4script`，覆盖当前文件；不要把整个备份目录放进 Mods。卸载时只移除该脚本包。用户目录下的 `ContextOverlay/` 保存日志和导出数据，默认保留，可在游戏退出后自行归档。

历史日志不会自动清理。单次运行的日志与导出共用 2 GiB 上限；多个运行会持续占用磁盘，试用时可定期整理旧运行目录。

## 命令行与开发者打包

指定实际用户目录，或先预览安装目标：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Profile "D:\My Documents\Electronic Arts\The Sims 4"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Profile "D:\My Documents\Electronic Arts\The Sims 4" -WhatIf
```

这里的执行策略参数只作用于本次 PowerShell 进程。安装器支持 Windows PowerShell 5.1；自动化使用可加 `-NonInteractive`，目录不明确时直接报错。

GitHub 的源码 ZIP 不含已编译 MOD。开发者先按[运行与调试](runtime-usage.md)构建，再运行：

```powershell
python scripts/package_trial.py
```

打包器核对源码与已构建包的一致性，生成 `dist/ContextOverlay-版本-Windows.zip` 及 `.zip.sha256`，包含安装入口、已编译包、构建清单和接入说明。游戏中文词表为本地构建输入，`dist/` 不提交 Git。生成本地包不代表已更新飞书附件或完成该版本的游戏验收。
