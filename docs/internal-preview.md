# Context for Overlay：0.3.2 内部试用

更新于 2026-09-14。现在可以在《模拟人生 4》中点击角色或物件，查看它的当前状态和已记录经历，也可以把这些数据交给自己的 MOD 或 Overlay 玩法使用。

本次交付为一个 `ContextOverlay.ts4script`，其中包含 **Context 采集、事件记录、数据语义化**三个模块。已完成首轮游戏场景验证，并提供可点击的游戏原生查看窗口。本版适合内部试用、核对数据和开始接入玩法。

[项目源码](https://github.com/omgwowai/Sims4-Context-for-Overlay) · [详细安装说明](https://github.com/omgwowai/Sims4-Context-for-Overlay/blob/main/docs/install.md) · [开发接入说明](https://github.com/omgwowai/Sims4-Context-for-Overlay/blob/main/docs/mod-integration.md)

## 1. MOD 安装、使用与界面效果

### 下载与安装

从飞书页面此处的文件附件下载 **ContextOverlay-0.3.2-Windows.zip**。完整解压，退出游戏，双击 **Install.cmd** 即可安装；不需要安装 Python，也不需要管理员权限。

<!-- INSTALLER_ATTACHMENT -->

安装器会查找实际游戏用户目录，包括常见 OneDrive 文档路径。找不到或发现多个目录时，按提示粘贴包含 `Mods` 和 `Options.ini` 的文件夹路径。默认通常在“文档/Electronic Arts/The Sims 4”，不要选择存放游戏程序的安装目录。

安装成功后，在“游戏选项 → 其他”启用“自定义内容与模组”和“脚本模组”，应用并重启游戏。脚本只安装本项目的一个文件，保留存档、其他 MOD、游戏选项和已有配置；升级时自动将旧包备份到 Mods 之外。

手动安装也可以：把解压目录中的 `dist/ContextOverlay.ts4script` 放到 **游戏用户目录/Mods/ContextOverlay/**。保持 `.ts4script` 完整，不要再次解压，确保 Mods 中只有一份本项目脚本包。源码仓库的 ZIP 不包含编译后的 MOD，请使用本文附件。

### 打开窗口

进入存档的生活模式后，**普通点击当前地块上的 Sim 或物件 → 查看状态与历史**。不需要 Shift 点击，也不需要先输入控制台命令。

> 截图占位 1：普通点击 Sim 或物件，展示“查看状态与历史”菜单入口。

首页展示当前实体、状态摘要和最近 5 条主要事件。按钮从上到下是 **当前状态 → 历史事件 → 刷新 → 关闭**。近期事件逐行显示，便于快速了解刚发生的事情。

> 截图占位 2：首页概览，包含近期事件正文和四个导航按钮。

### 能看到什么

| 页面 | 当前可以查看的内容 |
| --- | --- |
| 当前状态 | Sim 的需求、Buff、范围内关系、当前交互和位置；物件的已接入常用状态，例如品质、新鲜度、清洁或损坏 |
| 状态分类与字段 | 横向文字列表，点击展开；长说明用正文显示，可以分段阅读 |
| 历史事件 | 与该实体关联的交互和状态变化；默认近 24 游戏小时，每页 15 条，可按时间、事件类型和内部步骤筛选 |
| 事件详情 | 参与者、目标、时间、触发来源、阶段与可观测结果，同时保留原始身份与证据 |
| 刷新 | 重新读取当前实体的状态和近期事件 |

> 截图占位 3：当前状态 → 需求或 Buff，展示横向文字列表。

> 截图占位 4：历史事件列表及一条事件详情，可补一张分页或筛选效果。

窗口是游戏原生模态窗口，打开时游戏暂停，关闭后恢复原先速度。这里展示的是上下文查看器；自己的对白、旁白或气泡 Overlay 由各玩法另行实现。

可以先用做饭与吃饭体验：让角色做饭、用餐，再查看角色与食物物件的状态和历史，对照行动阶段、目标、食物品质等实际记录。需求值默认只在查询当前状态时读取，**连续需求变化历史默认关闭**，避免近期事件被数值变化淹没。

目前只观察当前地块已实例化的 Sim 和物件。新运行的历史从开始采集后积累；重启、读档或换地块会开启新的记录范围，不会自动把旧存档经历接续进来。未知来源或结果会保留未知，未支持的字段会明确标注。

已验证环境为 Windows、The Sims 4 `1.126.73.1030`。核心功能完成了首轮场景验证，0.3.2 界面已实际查看并确认布局；完整分页组合及其他游戏版本、分辨率和 MOD 组合仍欢迎试用反馈。

### 升级与卸载

升级：退出游戏，再运行新包安装器。旧包在“游戏用户目录/ContextOverlay/install-backups/时间戳/”下；回退时只将所需脚本包复制回原安装位置。

卸载：退出游戏，移除 `Mods/ContextOverlay/ContextOverlay.ts4script`。`ContextOverlay/` 中的日志和导出数据默认保留，旧运行可自行归档或清理。

## 2. 后端数据形式与调用 MOD

### 三个模块如何协作

| 模块 | 已实现的作用 |
| --- | --- |
| 事件记录 | 持续记录交互阶段和已接入的状态变化。同一个事件保存一份，通过实体索引关联参与者；可按实体、时间和类型查询、分页 |
| Context 采集 | 按需读取某个 Sim 或物件的所选当前字段，可组合其关联历史，返回统一数据包 |
| 数据语义化 | 通过资源字典和规则模板生成逐项、逐条中文解释，保留对应原始字段、事件 ID 与修订；这一步不调用大语言模型 |

消费流程是：**自己的 MOD 选择实体和字段 → 取得 Context 与历史 → 组织自己的 Prompt → 调用模型 → 展示自己的 Overlay。** 本 MOD 提供前三种数据能力，模型连接、文案风格和展示时机由各自玩法决定。

### 文件是什么样、放在哪里

所有运行数据位于 **游戏用户目录/ContextOverlay/**：

| 路径 | 内容 |
| --- | --- |
| `runtime.log` | MOD 加载、运行启停和错误信息 |
| `runs/<session_id>/journal.jsonl` | 追加日志，每行一个 JSON，记录观测或某个事件的新修订 |
| `runs/<session_id>/context-<request_id>.json` | 一次 Context 或历史查询的完整 JSON 数据包 |
| `config.json` | 可选模块开关和采集配置 |

数据包将原始状态、历史与中文解释放在一起。下面是结构节选，使用示例值，省略了范围、时间、来源和其他字段：

```json
{
  "schema_version": "1",
  "module_version": "0.3.2",
  "kind": "context",
  "session_id": "example-run",
  "target": {"kind": "sim", "id": "10001", "key": "sim:10001", "name": "示例角色"},
  "snapshot": {
    "identity": {
      "status": "available",
      "value": {"kind": "sim", "id": "10001", "key": "sim:10001", "name": "示例角色"}
    }
  },
  "history": {
    "events": [
      {"event_id": "example-event", "revision": 2, "event_type": "interaction", "stage": "ended", "outcome": "completed"}
    ]
  },
  "rendered": {
    "history": [
      {"event_id": "example-event", "revision": 2, "text": "示例角色的吃饭交互已自然结束。"}
    ]
  }
}
```

使用时保留 `session_id`、字段 `status`、事件 ID 和修订号。所有实体及资源 ID 都是字符串，避免数值精度损失；同一事件的多次修订不是多件不同的事。中文解释可以直接阅读，原始数据适合进一步筛选、核验和构建 Prompt。

### 方式 A：先导出文件做玩法原型

在游戏中按 `Ctrl + Shift + C` 打开控制台，输入：

```text
co.status
co.export sim active 15 false both true identity,time,needs,buffs,relationships,interactions
```

这会导出当前操控角色的选定状态、最近 15 条主要事件及中文解释。命令返回请求 ID 和文件路径；`queued` 表示已入队，等待 `co.status` 中对应请求变为 `written` 且完整文件出现后，再由自己的程序读取 JSON。

这是从游戏内主动发起导出的路径，适合策划原型和离线调试；外部程序目前不能通过 HTTP 自动请求本 MOD。

### 方式 B：自己的 MOD 在运行时直接取数据

运行时直接调用已可行，当前游戏内查看器就是这样取数据的，不需要先通过控制台写文件。0.3.2 暂时开放的是**可参考的内部调用方式**，尚未封装为稳定的公共 SDK。完整示例与异常处理见[开发接入说明](https://github.com/omgwowai/Sims4-Context-for-Overlay/blob/main/docs/mod-integration.md)和[消费 MOD 示例](https://github.com/omgwowai/Sims4-Context-for-Overlay/blob/main/examples/mod_consumer.py)。

```python
from context_overlay import game_runtime

runtime = game_runtime._runtime
if runtime is None or runtime.closed or game_runtime._startup_error:
    raise RuntimeError("请等待地块加载完成")

packet = runtime.collector.collect(
    "sim", "active",
    fields=("identity", "needs", "buffs", "interactions"),
    include_history=True, history_limit=15,
    include_internal=False, representation="both")
```

请在游戏地块加载完成后、自己的游戏线程回调中采集。返回的是可序列化字典，之后可以交给自己的后台服务调用模型；不要在游戏线程等待网络，也不要在后台线程直接读取游戏对象。旅行或读档后重新获取 runtime，不能一直缓存旧运行。

历史使用 `runtime.recorder.query_history(...)` 开始查询、`history_page(next_cursor)` 翻页、`close_query(cursor)` 释放。支持实体、时间和类型过滤，固定查询创建时的事件版本，默认 120 秒现实时间后过期。按需取一部分历史即可，不建议一次把全部事件送给模型。

当前事件数量上限为 200,000，并设置内存、查询和磁盘预算；达到限制会明确报错。单次运行日志与导出共用 2 GiB 限额，旧运行日志不会自动清理。采集范围、查询缺口与失败状态保留在数据中，消费方应一并处理。

## 3. 后续开发方向

**完善当前的 MOD，欢迎其他人提出更多需求。**
