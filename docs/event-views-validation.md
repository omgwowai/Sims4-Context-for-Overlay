# 分层接口验收步骤

适用 **MOD 0.10.2 / API、SDK 2.2.0 / schema 2 / event_views_v1**。本页给出可重复执行的验收步骤，实际通过范围和限制见[验证记录](validation.md)。可以由下游 MOD 开发者执行，也可以由测试者手动加载游戏后从终端发送请求，不需要 Computer Use。退出后还应检查[完整结束与自动分层文件](run-output.md)。

## 接口入口

生产接入通过游戏内 Python MOD 调用 [SDK](../sdk/context_overlay_client.py)。在游戏线程检查 `client.get_api_info()` 的 `event_views.query` / `event_views.explain` 能力，以及 `client.get_status()` 的 `ready`、`session_id`。每个游戏回调最多轮询一次状态或读取一页，不在游戏线程循环等待；可参考[逐回调读取示例](../sdk/examples/event_views.py)。

| 用途 | 公共 API / SDK 方法 | 开发驱动 operation |
| --- | --- | --- |
| 版本与能力 | `get_api_info()` | `api_info` |
| 开始 records/events/organized/recap 查询 | `query_event_view(...)` | `api_view` |
| 等待后台构建 | `get_event_view_status(request_id, ...)` | `api_view_status` |
| 读取一页 | `get_event_view_page(cursor, ...)` | `api_view_page` |
| 解释来源、规则、名称、修订等 | `explain_event_view(snapshot_id, item_id, ...)` | `api_view_explain` |
| 取消或释放请求 | `close_event_view(request_id, ...)` | `api_view_close` |

这些分层方法都要求 `expected_session_id`。驱动把 `params` 原样转给对应方法；普通 `status` 返回 Runtime 状态及 session，不等同于公共 `get_status()`。完整签名、范围和预算见[接口契约](event-views.md)。

## 准备手动游戏测试

1. 从当前源码构建并安装 0.10.2，步骤见[开发说明](development.md#检查与构建)。已发布的 0.9.0 ZIP 没有新接口。安装前退出游戏。
2. 使用 MOD 自己的回调验收时不必打开开发驱动。要使用下方终端命令，在游戏用户目录 `ContextOverlay/config.json` 的现有对象中设 `"development_driver": true`，保留其他配置。先备份配置，测试后还原。使用复制存档隔离测试时也可采用[现有测试环境工具](development.md#实机测试环境与请求)。
3. 手动启动游戏、加载可操控 Sim 的地块，正常运行一小段，确保产生了已落盘记录。以下命令在源码仓库根目录的 PowerShell 运行；SDK/Windows 分发包不包含开发请求脚本。

先定义终端辅助函数。这里的等待发生在游戏外 PowerShell；不要将它移植成游戏线程里的等待循环。一次只运行一个请求终端，开发驱动的请求文件不支持多个客户端并发写入。

```powershell
$coPython = 'python' # 可换成自己的 Python 解释器路径
$coUserData = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Electronic Arts/The Sims 4'
$coEvidence = Join-Path (Get-Location) ('.local/event-views-check-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $coEvidence -Force | Out-Null

function Invoke-Co([string]$Operation, [hashtable]$Params = @{}) {
    $tag = [guid]::NewGuid().ToString('N')
    $requestFile = Join-Path $coEvidence "$Operation-$tag.request.json"
    $responseFile = Join-Path $coEvidence "$Operation-$tag.response.json"
    @{ operation = $Operation; params = $Params } | ConvertTo-Json -Depth 100 |
        Set-Content -LiteralPath $requestFile -Encoding UTF8
    & $coPython -B -X utf8 scripts/game_request.py $requestFile --user-data $coUserData --output $responseFile
    if ($LASTEXITCODE -ne 0) { throw "请求失败或超时；检查 $responseFile 和终端输出" }
    $answer = Get-Content -LiteralPath $responseFile -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $answer.ok) { throw $answer.error }
    return $answer.result
}

function Wait-CoView($Request) {
    $deadline = [DateTime]::UtcNow.AddSeconds(150)
    while ($Request.state -eq 'building' -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 500
        $Request = Invoke-Co api_view_status @{ request_id = $Request.request_id; expected_session_id = $coSession }
    }
    if ($Request.state -ne 'ready') { throw ($Request | ConvertTo-Json -Depth 20) }
    return $Request
}

$coInfo = Invoke-Co api_info
$coInfo | Select-Object module_version, api_version, schema_version, capabilities
$coSession = (Invoke-Co status).session_id
```

`game_request.py` 响应外层是传输回执，内含 `ok/result/session_id/execution_ms`。查询的 `request_id` 位于 **result**，与外层请求编号不同；辅助函数已取出 result。同步参数／session 错误使外层 `ok=false`，后台构建错误则是 `ok=true` 且 `result.state=failed`，两种都要检查。

## 同一来源截点读取四层

先固定当前选中人物及来源，再打开其余层。第一张页面的 `scope.entity_key` 固定人物，即使随后切换选中 Sim，后面的查询仍指向原人物。

```powershell
$coRecords = Wait-CoView (Invoke-Co api_view @{
    view = 'records'; kind = 'sim'; identifier = 'active'; page_size = 20
    expected_session_id = $coSession
})
$coFirst = Invoke-Co api_view_page @{ cursor = $coRecords.cursor; expected_session_id = $coSession }
$coSimId = $coFirst.scope.entity_key.Substring(4)
$coSource = $coRecords.source_snapshot_id
$coViews = @{ records = $coRecords }
foreach ($view in @('events', 'organized', 'recap')) {
    $coViews[$view] = Wait-CoView (Invoke-Co api_view @{
        view = $view; kind = 'sim'; identifier = $coSimId; page_size = 20
        source_snapshot_id = $coSource; expected_session_id = $coSession
    })
}
$coViews.GetEnumerator() | ForEach-Object {
    [pscustomobject]@{ view = $_.Key; total = $_.Value.total_matches; source = $_.Value.source_snapshot_id }
}
```

四个请求的 `source_snapshot_id` 必须相同；对应页面的 `scope.as_of_sequence/source_sha256/source_byte_offset` 也必须相同。不要靠连续发四次不带 source 的请求来假定同源。请求在闲置 300 秒后会过期，做下一步前及时读取；需要长时间停留时由 MOD 回调续读状态。

完整遍历可用下面的终端循环。页面逐份保存到 `$coEvidence`，只保留当前页在内存中；最终项数必须等于各自 `total_matches`。`next_cursor=null` 表示遍历完成，不代表当前 session 的全部后续事件已经出现。

```powershell
$coLastKeepAlive = [DateTime]::UtcNow
foreach ($view in @('records', 'events', 'organized', 'recap')) {
    $cursor = $coViews[$view].cursor
    $count = 0
    do {
        if (([DateTime]::UtcNow - $coLastKeepAlive).TotalSeconds -ge 30) {
            foreach ($held in $coViews.Values) {
                $null = Invoke-Co api_view_status @{ request_id = $held.request_id; expected_session_id = $coSession }
            }
            $coLastKeepAlive = [DateTime]::UtcNow
        }
        $page = Invoke-Co api_view_page @{ cursor = $cursor; expected_session_id = $coSession }
        if ($page.source_snapshot_id -ne $coSource) { throw '来源不一致' }
        $count += @($page.items).Count
        $cursor = $page.next_cursor
    } while ($null -ne $cursor)
    if ($count -ne $coViews[$view].total_matches) { throw "$view 分页数量不一致" }
    Write-Output "$view : $count 项"
}
```

完整四层遍历主要用于验收；实际 UI 默认请求 recap，按需展开详情。此循环每 30 秒续读各请求状态；使用 MOD 接入时也应由回调维持仍需保留的请求，避免超过闲置 TTL。

## 从阅读项回查依据

```powershell
$coRecapPage = Invoke-Co api_view_page @{ cursor = $coViews.recap.cursor; expected_session_id = $coSession }
$coExplain = Wait-CoView (Invoke-Co api_view_explain @{
    snapshot_id = $coRecapPage.snapshot_id; item_id = $coRecapPage.items[0].item_id
    facet = 'revisions'; page_size = 20; expected_session_id = $coSession
})
$coEvidencePage = Invoke-Co api_view_page @{ cursor = $coExplain.cursor; expected_session_id = $coSession }
$coEvidencePage.items
# 继续按 next_cursor 读完，之后释放解释请求。
Invoke-Co api_view_close @{ request_id = $coExplain.request_id; expected_session_id = $coSession }
```

如果 recap 没有条目，从 events 或 records 中选实际存在的条目解释；辅助 observation 的 revisions 返回其原记录，没有逻辑事件的其他 facet 返回空项。将 facet 换成 `lineage/policy/labels/events/units` 可分别核对来源归属、阅读去向、名称解析、完整最新事件及组织单元。使用页面**顶层** snapshot_id；`recap.snapshot_id` 是离线 bundle 身份，不能代替公共 API 的 snapshot_id。

Eddie 固定历史样例中的 `r54` 可代替 item_id，回查交互实例 5941 的 5 个修订，区分 **19:25:29 入队**与 **19:33:50 开始**。这仅适用于那份 recap 快照，新的游戏会话不能假设 r54 仍指向同一件事。

## 验收判据与证据

| 检查 | 操作与通过标准 |
| --- | --- |
| 四层数据守恒 | records 中每个关联事件的修订连续；events 是同一截点的最后修订。organized 每个来源均有 unit 或 standalone；被 recap 省略的内容能从 policy/lineage/events 回查，不能因正文变短而失去原始数据 |
| 名称与行动者 | 同名同时间的不同人物／交互实例保持独立；未知名称保留解析状态和依据。入队、首次观测、执行开始分别核对，未见开始不能填成已开始 |
| 持续采集与旧页 | 保留一张旧页，在游戏里正常做一个动作并等其落盘，再用同一 cursor 重读；旧页 items、snapshot_id、截点相同。不传 source 新开查询，才应看到推进后的截点 |
| 正常旅行 | 记录 session 和旧页，在 TTL 内手动旅行；载入中可以临时报 not_ready。加载完成后 session 相同，旧页仍一致；新动作落盘后新查询包含新增记录 |
| 读档／重启隔离 | 手动读档或 co.restart 后，携带旧 expected_session_id 的读请求报 session_changed；重新获取 session 再查询，不复用旧游标 |
| 取消、释放、过期 | building 时 close 后，旧请求／游标不可继续读；关闭一个共享源请求不影响另一个。闲置超过 TTL 后报 view_expired，旧 source 无其他请求保留时不可重用 |
| 错误必须明确 | page_size=101 或未知 view/profile 报 invalid_request；错层 item 报 item_not_in_view；超预算／损坏来源失败，不返回伪装成功的空历史。损坏日志与预算边界已由自动化覆盖，不需要为手动验收修改实际游戏日志 |

Eddie 的 **3,843 records → 1,707 events → 1,166 organized → 118 recap** 是固定的 2026-09-17 历史源基线，不是新开游戏的预期计数。对应源 SHA-256 为 `c57e7c636dd3b594e2f1664b3df45502be9662b6ee0654301654da6be3b2c0fc`，截至 sequence 14954。新游戏数据按上述结构与同源关系验收；不同层计数单位不同，不能用相减结果当“删除事件数”。

负载验收在相同地块和速度下分别采样空闲、首次构建与缓存后分页。以下请求参数在顶层，不经过 API params：

```powershell
& $coPython -B -X utf8 scripts/game_request.py frame_probe seconds=20 --user-data $coUserData --output (Join-Path $coEvidence 'probe-start.json')
# 20 秒内从 MOD 回调或另一次终端请求触发待测操作；到期后读取。
& $coPython -B -X utf8 scripts/game_request.py frame_probe --user-data $coUserData --output (Join-Path $coEvidence 'probe-result.json')
```

记录每次 API 回执的 `execution_ms`、ready 状态的 `build_ms`，以及 probe 的 samples、p50/p95/p99/max；采样必须无 error、有样本且 state=complete。probe 是 `Zone.update` 的墙钟间隔，不是渲染 FPS。后台仍共享 GIL／垃圾回收，实机是否出现明显停顿由测量与操作体验共同判定，目前没有已验收的帧时间阈值。

完成后对四层请求逐一 `api_view_close`，关闭其他解释／测试请求；退出游戏并恢复 development_driver 配置。若用了测试环境工具，执行其 restore。反馈时提供版本、session、来源截点、触发步骤、预期与实际结果，以及 `$coEvidence` 中相关请求／响应；负载反馈另外包含日志大小和采样结果。请求响应可能包含存档事件，按需要选择反馈文件。

## 不启动游戏的回归检查

在源码目录用 Python 3.7 运行，测试使用临时数据及 EA 服务替身：

```powershell
python -B -X utf8 -m unittest discover -s tests -p test_event_views.py -v
python -B -X utf8 -m unittest discover -s tests -p test_trial_bundle.py -v
python -B -X utf8 -m unittest discover -s tests -v
```

这些检查覆盖分层完整性、回查、SDK 和文档分发关系；测试包只生成于临时目录，不生成项目分发 ZIP。它们不能代替本页的游戏内调用、旅行及负载验收。
