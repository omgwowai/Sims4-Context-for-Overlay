# 开发环境与参考基线

核验日期：2026-09-14。状态：首轮构建、加载和限定场景验证完成，原游戏环境已恢复，见[验证记录](validation/2026-09-14-first-round.md)。

## 1. 来源优先级

| 来源 | 定位 | 使用规则 |
| --- | --- | --- |
| [EA 逆向源码](../../sims4-python/ea-source/EA/) | 游戏本体主要源码参考 | 追踪类、方法、实际调用点、事件发送条件与字段含义；结合反编译状态和目标版本判断 |
| 本地 The Sims 4 | 游戏加载与行为验证 | 在确定的版本、实体、场景和 DLC/MOD 组合内验证实际结果 |
| [Context Atlas](../../Sims4-Context-Atlas/README.md) | 静态分析参考 | 帮助检索定义与关系；结论回到对应源码核验，不直接视为已验证运行能力 |
| [旧 Experience](../../Sims4-Experience-Mod/README.md) | 必要时参考的实现经验 | 用于具体接入问题与已知限制；新模型、旧格式导入和 MOD 依赖均不预设继承 |

`sims4-python/ea-source/My Script Mods/` 下的 Observer 等属于自研 MOD 示例，与 `ea-source/EA/` 的游戏本体源码分开引用。需要其实现经验时明确标为“示例实现”，不把其中的辅助 API 当成游戏自带接口。

## 2. 本地运行环境

| 项目 | 已核对的信息 | 验证边界 |
| --- | --- | --- |
| 游戏安装根目录 | `D:/Games/The Sims 4` | 已加载本项目 MOD 并运行测试场景 |
| 游戏可执行文件 | `D:/Games/The Sims 4/Game/Bin/TS4_x64.exe` | 已核验实际进程与脚本加载日志 |
| 目标版本 | 程序 FileVersion、ProductVersion 及 `Game/Bin/Default.ini` 均为 `1.126.73.1030` | 游戏更新后重新核对 |
| 游戏 Python 资源 | `Data/Simulation/Gameplay/` 下的 `base.zip`、`core.zip`、`simulation.zip`；魔数 `420d0d0a` | 实际解释器 Python 3.7.0；CPython 3.7.9 编译包已加载 |
| 实际用户数据目录 | `C:/Users/ZixuanMin/Documents/Electronic Arts/The Sims 4` | 由已加载包路径、日志和输出确认 |
| DLC、MOD 与测试场景 | 简体中文；`Slot_00000008.save` 副本；测试时仅本 MOD；运行输出附可用资料片列表 | 住宅做饭/吃饭与公园旅行；不代表所有资料片玩法或其他 MOD 组合通过 |

安装目录与用户数据目录分别管理。Mods、日志和存档通常位于游戏用户数据目录；正式部署前确认实际路径，不把脚本包默认写入安装目录。

目标游戏版本与源码仓库声明的研究版本之一一致，这只建立版本号层面的对应关系，不表示每个反编译文件或接口已经验证可用。

## 3. 源码与静态分析版本

| 资料 | 核验基线 |
| --- | --- |
| `C:/sources/sims4-python` 当前提交 | `12718ed96470fc2edffbc7875d10cf537b1f0e57` |
| 源码仓库声明的游戏研究版本 | `1.126.73.1030 / 1.126.78.1220` |
| 主要源码目录 | `ea-source/EA/core/`、`ea-source/EA/simulation/`，按需参阅其他子目录 |
| 文件来源与解析状态 | [PROVENANCE.tsv](../../sims4-python/ea-source/EA/PROVENANCE.tsv) |
| Atlas 本地形式 | `C:/sources/Sims4-Context-Atlas`，无 Git 元数据的导出目录 |
| Atlas 导出 | `site_version=25`，`source_commit=f5117cee19d55f2efb537978e9762a0a8e48bdd4` |
| Atlas 分析所用 Python 源码提交 | `1003b2507e5e68dd59eb10135140187bb5ed7750` |
| Atlas 关系账本提交 | `4dec7ebaaf3ecba38f462827dbbbf4fee212d88c` |

Atlas 版本来自 [export-manifest.json](../../Sims4-Context-Atlas/export-manifest.json) 和 [dashboard manifest](../../Sims4-Context-Atlas/dist/dashboard/manifest.json)。其源码输入与当前 `sims4-python` 提交不同，静态分析结果不可自动套用为当前接口事实。

旧 Experience 在本机的实际目录名为 `Sims4-Experience-Mod`。其 2026-09-11 版本和缺陷研究见[历史参考基线](archive/2026-09-research/reference-baseline.md)；当前开发不依赖该仓库安装或运行。

## 4. 引用与验证方法

每个被采用的接口至少记录：

- 源码仓库提交、相对文件路径、类/方法或资源身份；行号只作导航。
- 反编译文件的来源与解析状态，必要时与原字节码、调用点或同版本资源交叉核对。
- 调用/注册时机、参数、返回/回调字段、实体范围、默认值与副作用。
- 事件发送条件、重复报告与解除订阅方式。
- 当前验证状态：设计候选、已定位源码、已实现、已游戏验证；附版本、场景、输出证据和未覆盖项。

源码中存在枚举、方法或字段，不等于当前运行场景会发送通知或提供数据。优先从调用点确认行为，再在限定场景中对照游戏直接查询与实际操作。

## 5. Python 与构建基线

源码仓库将 EA 字节码标识为 Python 3.7。已使用独立 CPython 3.7.9 构建首版，编译魔数与本地 `simulation.zip` 匹配；游戏日志确认实际解释器为 Python 3.7.0，脚本包已加载并通过首轮场景验证。开发机器默认 Python 3.14 不用于脚本包编译。

独立解释器位于 `%LOCALAPPDATA%/Sims4ContextDev/python37/`，来自 [Python 官方 embedded distribution](https://www.python.org/ftp/python/3.7.9/python-3.7.9-embed-amd64.zip)，下载包 SHA-256 为 `18627a097adf47829a847053febac5532376075243e233bd9ec61d6ea09dee1f`。构建脚本与本地资源输入见[运行说明](runtime-usage.md)。

构建清单、运行来源和实际输出可相互核对；本次没有引用旧 Experience 实现，也不使用其历史样本作为新模块的通过证据。

## 6. 历史资料

[2026-09 调研归档](archive/README.md)保留旧 Atlas/Experience 审计、阶段设计与原型输出。历史样本反映当时的版本和覆盖范围，不能替代新模块的测试。

当前阅读入口：[总体设计](modular-context-provider.md)、[技术接口目录](context-acquisition-interfaces.md)、[首轮实现与验收](implementation-and-validation.md)。
