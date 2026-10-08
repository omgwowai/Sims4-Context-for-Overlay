# 文档入口

当前文档对应 **ContextOverlay 0.14.1 Preview / API、SDK 2.6.0 / schema 2**，游戏基线为 `1.126.73.1030`。发行文件和更新说明见 [v0.14.1 Release](https://github.com/omgwowai/Sims4-Context-for-Overlay/releases/tag/v0.14.1)。

初次安装从[安装与使用](install.md)开始；接入自己的 MOD 按[读取指南](reading-guide.md)选择能力，再用[快速接入](quickstart.md)完成读写闭环。示例、准确参数和验证记录分别放在对应页面。

## 按目标查找

### 使用与接入

| 目标 | 文档 |
| --- | --- |
| 下载 Windows 包、从源码安装、运行自检 | [安装与使用](install.md) |
| 第一次接入自己的游戏内 MOD | [快速接入](quickstart.md) |
| 读取气泡事件窗口、发送历史与整理结果 | [气泡事件与时间窗口](balloons.md) |
| SDK 文件、示例和合成数据 | [SDK 说明](../sdk/README.md) |

### API 与运行数据

| 目标 | 文档 |
| --- | --- |
| 不确定该选哪个读取接口或筛选条件 | [读取指南与能力矩阵](reading-guide.md) |
| 用真实条数、分类和证据追踪理解四层 | [四层事件实例：Nova 的一局游戏](event-layers-example.md) |
| 完整参数、返回结构和错误码 | [API 参考](public-api-v2.md) |
| 摄像机位置、近似视锥与全部实体摘要 | [视锥查询](camera-view.md) |
| records / events / organized / recap 四层查询 | [分层事件查询](event-views.md) |
| 正常结束后的 journal、快照和质量文件 | [写盘与自动分层文件](run-output.md) |
| 活动组织、去向和质量对账 | [事件整理与对账](event-quality.md) |

### 开发与验收

| 目标 | 文档 |
| --- | --- |
| 理解采集范围、事件事实和文本证据 | [架构与采集语义](architecture.md) |
| 测试、资源构建、编译、安装和离线工具 | [开发与调试](development.md) |
| 已验证范围、证据和已知限制 | [验证摘要](validation.md) |
| 手动验收分层接口、事件窗口和证据回查 | [分层接口验收](event-views-validation.md) |
| 视锥查询复测、实机样本和未验证场景 | [视锥查询验收](camera-view-validation.md) |
| 尚未完成的工作 | [未来方向](future.md) |

### 历史证据

| 材料 | 适用范围 |
| --- | --- |
| [历次验收明细](validation-history.md) | 各轮采集、修复、安装哈希与性能对照；以各节标注的版本和日期为准 |
| [2026-10-08 两次会话对比](session-comparison-2026-10-08.md) | 0.12.1 与 0.13.0；包含当时仍存在的最近气泡缓存 |
| [Nova 的四层实例](event-layers-example.md) | 0.10.10 实际输出；用于解释四层，不作为当前版本重新实测 |

## 约定

- 当前契约以接口文档及源码为准；版本定义位于 src/context_overlay/__init__.py、src/context_overlay/api.py 和 sdk/context_overlay_client.py。最新结论集中在验证摘要，历史记录不替代当前验收。
- src/、sdk/、scripts/、tests/ 和 docs/ 是 Git 跟踪的项目内容。
- dist/、tmp/ 和 .local/ 是本机生成目录。构建包、验证输出、资源提取和临时文件不要提交；需要保留的行为、契约和结论应写入源码、测试或文档。
- 日常修改只做相称的检查，不自动执行 scripts/package.py。只有明确需要分发包时才打 ZIP。
- 纯文档修改不重建或重新安装游戏脚本；新增文档需同步分发清单并检查相对链接。
- 本地安装必须先确认游戏已退出；安装器不会强制关闭游戏，会校验源码与 manifest、旧版备份和目标哈希。
