# 附近实体查询实现与离线验证

日期：2026-09-15。MOD 0.6.0；API／SDK 1.1.0；schema 1。已实现、构建并更新本机安装，未启动游戏。本报告不能替代空间结果、原生房间返回值或真实下游 MOD 的实机验收。[机器可读证据](2026-09-15-nearby-entities.json)记录源码／构建／安装摘要。

## 已交付

- `get_nearby_entities`：以 Sim 为中心，支持 Sim／物件、水平／三维距离、楼层／房间约束、自身选项、稳定排序和最多 64 个结果。
- `nearby.collect`：查询一次枚举，最多考察 10,000 个管理器条目，只在调用期间保留有限结果引用。分别标记已知匹配数、返回截断、候选读取缺口与枚举不完整。
- EAAdapter：读取世界坐标、游戏 level、routing_surface 和房间；排除库存实体及其子对象。没有添加位置轮询、寻路或视线推断。
- SDK 1.1.0：新增同名方法和能力检查，原方法继续兼容 API 1.0；旧提供方调用新接口返回 capability_unavailable。
- `co.nearby`：将公开查询结果显式导出为 JSON。API 查询本身不写盘、不产生事件、没有游标资源。
- [接口文档](../nearby-entities.md)、[消费示例](../../sdk/examples/nearby_entities.py)、[合成输入](../../sdk/examples/nearby-packet.json)，同步更新 Context、安装与 SDK 说明。

## 验证

CPython 3.7.9 下运行 `python -m unittest discover -s tests -q`，**157 项测试通过**。其中 19 项为附近查询回归，使用真实 API、Collector 和 EAAdapter，替换游戏服务及原生 room 调用；覆盖：

- 半径包含边界、零半径、自身、距离指标改变筛选与排序、精确大整数 ID、Sim／物件混合和物件定义 ID；
- 同楼层与不同高度／路由表面、同房间与不同 zone、只按房间和条件交集；
- 库存及库存父对象、隐藏、场外、未实例化、父链异常；
- 原生房间未知或抛错、缺位置／楼层、名称失败时保留 ID，完整性与截断分别表达；
- 完整扫描后取最近 N 个、房间信息延迟到最终结果读取、去重、扫描预算、枚举器失败；
- 参数、线程、会话、Collector 开关、返回数据隔离、不产生事件和历史查询资源；
- SDK 旧提供方兼容、实际 Journal 导出内容与没有历史记录写入。

在本机游戏 `simulation.zip` 的字节码中核对 **8 个方法符号**：枚举器、世界坐标、物件与 Sim 楼层／路由表面、地块归属、库存判断；另确认 `build_buy.get_room_id` 原生别名存在。EA Python 自身在 `objects/object_state_utils.py` 中使用 zone、position、level 进行房间物件筛选。此检查没有执行游戏原生模块。

`nearby-packet.json` 由离线替身场景生成，provenance 标明 synthetic；只能作为下游格式输入。

## 构建与安装

使用匹配游戏字节码的 CPython 3.7 构建，保留当前中文 STBL。脚本 SHA-256 为 `3433be4e7374c1b02f12e4b63e931ad73ef6f80a1246aecb64fd9ef8020d4afb`。

已通过项目安装器更新本机 `Documents/Electronic Arts/The Sims 4/Mods/ContextOverlay/ContextOverlay.ts4script`；旧包备份到 `ContextOverlay/install-backups/20260915T0928380729706Z/ContextOverlay.ts4script`。旧包摘要为 `6713b39e11b6b4d1c9922e8df377cc7714ddaa21e602256d2515787dc8248034`。安装前游戏未运行，安装文件与源包摘要核对。

Windows 包为 `ContextOverlay-0.6.0-Windows.zip`，SDK 包为 `ContextOverlay-SDK-1.1.0.zip`。未更新飞书附件或执行 Git 发布。

## 待实机验证

使用 `co.nearby active 8 all`、`co.nearby active room sim`，对照同层不同房间、楼上楼下、室外／泳池、桌面／库存物件等场景。检查完整 JSON 的 coverage 和字段状态，而不是只看数量。

None、0、负房间 ID 作为未核验的原生返回保留；房间未知不被猜成同房间。正房间 ID 的实际分区含义仍需场景核对。空间查询没有测量帧耗时，暂不建议每帧调用。视线、路径、角色感知、全量邻居 Context 展开与游戏窗口入口不属于本次交付。
