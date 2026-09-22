# 文档入口

当前源码版本为 **ContextOverlay 0.10.10**，公共 API / SDK 版本为 **2.2.0**，数据 schema 为 **2**。版本的唯一来源是 src/context_overlay/__init__.py、src/context_overlay/api.py 和 sdk/context_overlay_client.py；文档中的版本号只用于说明适用范围。

## 按目标查找

### 使用与接入

| 目标 | 文档 |
| --- | --- |
| 安装源码构建或已有构建包、运行自检 | [安装与使用](install.md) |
| 第一次接入自己的游戏内 MOD | [快速接入](quickstart.md) |
| SDK 文件、示例和合成数据 | [SDK 说明](../sdk/README.md) |

### API 与运行数据

| 目标 | 文档 |
| --- | --- |
| 不确定该选哪个读取接口或筛选条件 | [读取指南与能力矩阵](reading-guide.md) |
| 完整参数、返回结构和错误码 | [API 参考](public-api-v2.md) |
| records / events / organized / recap 四层查询 | [分层事件查询](event-views.md) |
| 手动验收分层接口、分页和证据回查 | [分层接口验收](event-views-validation.md) |
| 正常结束后的 journal、快照和质量文件 | [写盘与自动分层文件](run-output.md) |
| 活动组织、去向和质量对账 | [事件整理与对账](event-quality.md) |

### 维护项目本身

| 目标 | 文档 |
| --- | --- |
| 理解采集范围、事件事实和文本证据 | [架构与采集语义](architecture.md) |
| 测试、资源构建、编译、安装和离线工具 | [开发与调试](development.md) |
| 已验证范围、证据和已知限制 | [验证摘要](validation.md) |
| 尚未完成的工作 | [未来方向](future.md) |

## 约定

- src/、sdk/、scripts/、tests/ 和 docs/ 是 Git 跟踪的项目内容。
- dist/、tmp/ 和 .local/ 是本机生成目录。构建包、验证输出、资源提取和临时文件不要提交；需要保留的行为、契约和结论应写入源码、测试或文档。
- 日常修改只做相称的检查，不自动执行 scripts/package.py。只有明确需要分发包时才打 ZIP。
- 本地安装必须先确认游戏已退出；安装器不会强制关闭游戏，会校验源码与 manifest、旧版备份和目标哈希。
