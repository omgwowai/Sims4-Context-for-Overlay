# 开发环境与参考基线

核验日期：2026-09-14。状态：本地路径、版本元数据和源码清单已核对；尚未运行或部署本项目 MOD。

## 1. 来源优先级

| 来源 | 定位 | 使用规则 |
| --- | --- | --- |
| [EA 逆向源码](../../sims4-python/ea-source/EA/) | 游戏本体主要源码参考 | 追踪类、方法、实际调用点、事件发送条件与字段含义；结合反编译状态和目标版本判断 |
| 本地 The Sims 4 | 游戏加载与行为验证 | 在确定的版本、实体、场景和 DLC/MOD 组合内验证实际结果 |
| [Context Atlas](../../Sims4-Context-Atlas/README.md) | 静态分析参考 | 帮助检索定义与关系；结论回到对应源码核验，不直接视为已验证运行能力 |
| [旧 Experience](../../Sims4-Experience-Mod/README.md) | 必要时参考的实现经验 | 用于具体接入问题与已知限制；新模型、旧格式导入和 MOD 依赖均不预设继承 |

`sims4-python/ea-source/My Script Mods/` 下的 Observer 等属于自研 MOD 示例，与 `ea-source/EA/` 的游戏本体源码分开引用。需要其实现经验时明确标为“示例实现”，不把其中的辅助 API 当成游戏自带接口。

## 2. 本地运行环境

| 项目 | 已核对的信息 | 尚需验证 |
| --- | --- | --- |
| 游戏安装根目录 | `D:/Games/The Sims 4`，目录存在 | 首次加载本项目 MOD |
| 游戏可执行文件 | `D:/Games/The Sims 4/Game/Bin/TS4_x64.exe` | 实机进程与加载行为 |
| 目标版本 | 程序 FileVersion、ProductVersion 及 `Game/Bin/Default.ini` 均为 `1.126.73.1030` | 游戏更新后重新核对 |
| 游戏 Python 资源 | `Data/Simulation/Gameplay/` 下存在 `base.zip`、`core.zip`、`simulation.zip` | 实际字节码、编译与脚本包加载兼容性 |
| 用户数据目录候选 | `C:/Users/ZixuanMin/Documents/Electronic Arts/The Sims 4`，目录存在 | 通过运行日志确认游戏实际使用的用户数据目录 |
| DLC、MOD 与测试场景 | 未完成当前运行清单核验 | 首次实测记录启用组合、语言、测试存档与场景 |

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

源码仓库将 EA 字节码标识为 Python 3.7；项目目标解释器和脚本打包流程仍需通过本地加载实验确定。开发机器上可运行的通用 Python 版本，不自动等于游戏可加载的字节码版本。

第一轮应记录编译解释器、字节码兼容性、脚本包结构、安装目标、模块加载证据与日志位置。尚未确定的内容保留为待验证项，不把旧 MOD 的编译命令写成本项目已可用命令。

## 6. 历史资料

[2026-09 调研归档](archive/README.md)保留旧 Atlas/Experience 审计、阶段设计与原型输出。历史样本反映当时的版本和覆盖范围，不能替代新模块的测试。

当前阅读入口：[总体设计](modular-context-provider.md)、[技术接口目录](context-acquisition-interfaces.md)、[首轮实现与验收](implementation-and-validation.md)。
