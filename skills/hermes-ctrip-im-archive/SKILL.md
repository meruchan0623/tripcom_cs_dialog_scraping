---
name: hermes-ctrip-im-archive
description: |
  在 Hermes Agent 主机上自动运行 tripcom_cs_dialog_scraping 的携程 IM 会话采集与归档 CLI。
  当任务涉及定时/无人值守抓取携程 vbooking IMExperience 会话、导出 IMChatlogExport JSON/Markdown/HTML/XLSX、
  检查 web-access CDP proxy、复用已登录浏览器态、处理 403/登录失效/空结果/导出失败、
  或为 Hermes 自动任务编排该项目时触发。
---

# Hermes Ctrip IM Archive Runner

## 目标

在 Hermes Agent 主机上无人值守执行携程 IM 会话归档：

1. 复用已登录浏览器页面或 CDP proxy。
2. 通过 `imx run collect` 模拟前端请求采集会话列表。
3. 通过 Python state 完成角色筛选、链接表导出、结构化 JSON/Markdown 导出、SingleFile HTML 导出。
4. 用命令输出和产物回读确认本次运行是否成功。

Hermes Remote Debugging allocation: `9222` 固定给 Ctrip/Tripcom 主已登录浏览器会话。不要把 KKday、Klook、eSIM 或 Trip SKU cron 迁到这个端口；`ctrip-get-comment-cli` 可读取 `9222` 作为主 Ctrip 登录态，并只在缺认证时使用自己的 `9224` fallback。

## 已验证链路

当前项目在 Hermes/Agent 机器上的已验证链路是：

1. `run collect --via cdp`：
   复用已登录的 `vbooking.ctrip.com` 页面，在页面上下文里发真实前端 `fetch`，抓客服列表和会话列表。
2. `roles select`：
   基于 `.im_archive/state.json` 选择全部或部分客服。
3. `run export --kind links`：
   读取 Python state 并导出链接表 xlsx，便于人工抽检和后续二次处理。
4. `run export --kind structured --formats json,markdown`：
   通过已验证的详情消息接口走纯 HTTP 请求，落盘 `json` / `md`。不传 `--via` 时默认等同 `--via http`。
5. 后处理：
   用导出的 `IMChatlogExport_*.json`、`links xlsx`、会话索引 `csv/xlsx` 做筛选、统计、质检分析。

注意：

- 纯 `requests` 的 `collect --via http` 在当前环境里可能对 `13807` 直接返回 `403`，所以会话列表采集优先使用 `collect --via cdp`。
- `structured` 导出只支持 `--via http`。使用前应先通过 `discover detail-xhr` 验证并写回 `ctrip_im_detail_messages_url`。
- `--via cdp` 仍用于采集列表、请求发现、预检和 SingleFile 页面归档；不要再用 CDP/Selenium DOM 或旧详情页注入脚本解析聊天列表生成结构化 JSON/Markdown。

## 历史咨询筛选请求

Hermes Agent 需要知道 UI 筛选项如何落到接口请求体：

- 客人来源：
  `productChannel`
- 咨询场景：
  `consultationScene`
- 历史咨询日期/自定义日期区间：
  `startDate`、`endDate`
- 业务类型：
  `butype`

已实测映射：

- 客人来源 `Trip` -> `productChannel: "trip"`
- 客人来源 `汇总` -> `productChannel: "aggregate"`
- 咨询场景 `汇总` -> `consultationScene: "aggregate"`
- 自定义日期区间 -> `startDate: "YYYY-MM-DD"`、`endDate: "YYYY-MM-DD"`

推断但未逐项实测的映射：

- 客人来源 `CTrip` -> 通常是 `productChannel: "ctrip"`
- 咨询场景 `售前` -> 通常是 `consultationScene: "presale"`
- 咨询场景 `售后` -> 通常是 `consultationScene: "postsale"`

当前 CLI 已直接暴露日期参数：

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run collect \
  --start-date 2026-06-16 \
  --end-date 2026-06-16 \
  --via cdp
```

当前 CLI 没有单独的 `--product-channel` 或 `--consultation-scene` 参数。要筛选客人来源或咨询场景，写入配置后再运行：

```yaml
ctrip_im_product_channel: trip
ctrip_im_consultation_scene: aggregate
ctrip_im_butype: 品类活动
```

客服列表接口请求体：

```json
{
  "metricList": ["sev_session_count", "avg_work_duration", "avg_efficiency"],
  "searchMap": {},
  "filterType": "fail",
  "orderColumn": "sev_session_count",
  "orderType": "asc",
  "butype": "品类活动",
  "consultationScene": "aggregate",
  "startDate": "2026-06-16",
  "endDate": "2026-06-16",
  "pageNo": 1,
  "pageSize": 100,
  "productChannel": "trip",
  "currencyType": "CNY"
}
```

单客服会话列表接口请求体：

```json
{
  "metricList": [],
  "searchMap": {
    "vendor_account_id": "vbk_2538177",
    "vendor_account_name": "门票活动旅游管家Sara"
  },
  "orderColumn": "session_create_time",
  "orderType": "asc",
  "butype": "品类活动",
  "consultationScene": "aggregate",
  "startDate": "2026-06-16",
  "endDate": "2026-06-16",
  "pageNo": 1,
  "pageSize": 100,
  "productChannel": "trip"
}
```

接口地址：

- 客服列表：`POST https://m.ctrip.com/restapi/soa2/13807/getEmployeeDimMetricDetailsV3`
- 单客服会话列表：`POST https://m.ctrip.com/restapi/soa2/13807/getSessionDimMetricDetailsV3`
- 详情消息：`POST https://m.ctrip.com/restapi/soa2/16037/getMessagesBySession`，请求体通常是 `{"sessionId":"<session_id>"}`

## 项目位置

默认仓库：

```bash
cd /Users/tashima_meru/Develop/tripcom_cs_dialog_scraping
```

如果路径不存在，先定位仓库，不要新建空项目：

```bash
find /Users/tashima_meru/Develop -maxdepth 2 -name pyproject.toml -path '*tripcom_cs_dialog_scraping*'
```

## 前置检查

### 1. Python CLI

```bash
python3 -m im_archive_cli.imx_cli --help
```

只有在维护代码或升级依赖时才跑测试；日常 Hermes 执行不要求先跑 `pytest`。

### 2. web-access CDP proxy

该项目的默认采集与导出依赖 `http://localhost:3456`。

```bash
curl -s http://localhost:3456/targets
```

如果 proxy 不通，按 web-access skill 的前置检查启动：

```bash
node /Users/tashima_meru/.cc-switch/skills/web-access/scripts/check-deps.mjs
```

### 3. 携程登录态

检查当前浏览器是否有 `vbooking.ctrip.com` 页面：

```bash
curl -s http://localhost:3456/targets | python3 -m json.tool | rg 'vbooking\\.ctrip\\.com|IMExperience'
```

若无页面，打开入口页并让人工或持久 profile 完成登录：

```bash
open 'https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience'
```

无人值守任务不得盲目重试登录；出现登录墙、403、空页面时记录失败并告警。

### 4. 输出目录

Hermes 主机上优先使用仓库内可写目录：

```text
.im_archive/output
```

不要依赖历史示例里的 `/Users/tsimclaw/Downloads/Ctrip-CS-dialog`，除非该目录在当前 Hermes 机器上真实存在且可写。

确认当前配置：

```bash
python3 - <<'PY'
from pathlib import Path
from im_archive_cli.config import load_or_create_config
cfg = load_or_create_config(Path('.im_archive/config.yaml'))
print('output_dir=', cfg.output_dir)
print('state_file=', cfg.state_file)
print('proxy=', cfg.cdp_proxy_base_url)
PY
```

## 标准无人值守流程

### 1. 选择日期

默认采集昨天。Hermes 调度时应显式传入日期，避免跨时区歧义：

```bash
RUN_DATE="$(date -v-1d +%F)"
```

Linux 主机可用：

```bash
RUN_DATE="$(date -d yesterday +%F)"
```

### 2. 采集会话

优先使用 `cdp`，它在已登录页面上下文内执行真实前端 `fetch`。

```bash
python3 -m im_archive_cli.imx_cli run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --page-size 100 \
  --max-pages 50 \
  --via cdp
```

只跑指定客服时：

```bash
python3 -m im_archive_cli.imx_cli run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --include 'vbk_2538177' \
  --page-size 100 \
  --max-pages 50 \
  --via cdp
```

`--via http` 只作为诊断模式；携程 `13807` 接口可能对纯 requests 返回 403。

推荐显式传入参数，避免依赖默认值：

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --page-size "${PAGE_SIZE:-100}" \
  --max-pages "${MAX_PAGES:-50}" \
  --via cdp
```

### 3. 选择角色

默认 collect 后会全选所有角色。仍建议显式确认：

```bash
python3 -m im_archive_cli.imx_cli roles list
python3 -m im_archive_cli.imx_cli roles select --all
```

指定客服：

```bash
python3 -m im_archive_cli.imx_cli roles select --include 'vbk_2538177/门票活动旅游管家Sara'
```

### 4. 导出

链接表：

```bash
python3 -m im_archive_cli.imx_cli run export --kind links
```

结构化 JSON：

```bash
python3 -m im_archive_cli.imx_cli run export --kind structured --formats json
```

结构化 JSON + Markdown：

```bash
python3 -m im_archive_cli.imx_cli run export --kind structured --formats json,markdown
```

SingleFile HTML：

```bash
python3 -m im_archive_cli.imx_cli run export --kind singlefile
```

Hermes 正式任务推荐：

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export --kind links
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export \
  --kind structured \
  --formats json,markdown
```

说明：

- `structured`：只支持 `--via http`；不传 `--via` 时默认就是 HTTP。
- CLI 对 `--via cdp` 的明确错误是：`structured export 已剥离 CDP/Selenium DOM 抓取路径，只支持 --via http`。
- `links`：先导出链接表，方便确认 state 里选中的会话和详情 URL。
- `singlefile`：适合保留页面归档，不适合作为主要分析输入。

## 典型 Hermes 命令模板

### 全量抓取某一天

```bash
RUN_DATE="2026-06-16"
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --page-size 100 \
  --max-pages 50 \
  --via cdp

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml roles select --all

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export --kind links
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export \
  --kind structured \
  --formats json,markdown
```

### 只抓某个客服

```bash
RUN_DATE="2026-06-16"
ROLE="vbk_2538177/门票活动旅游管家Sara"

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --include "$ROLE" \
  --page-size 100 \
  --max-pages 50 \
  --via cdp

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml roles select --include "$ROLE"

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export --kind links
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export \
  --kind structured \
  --formats json,markdown
```

### 只导出链接表

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export --kind links
```

### 诊断纯 HTTP 可用性

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --via http
```

如果这里报 `HTTP 403`，不要继续加大重试；回到 `--via cdp`。

## 自动任务推荐脚本

Hermes Agent 可执行以下 shell 片段。失败时应保留 stdout/stderr 和 `.im_archive/state.json`。

```bash
set -euo pipefail

cd /Users/tashima_meru/Develop/tripcom_cs_dialog_scraping

RUN_DATE="${RUN_DATE:-$(date -v-1d +%F)}"

node /Users/tashima_meru/.cc-switch/skills/web-access/scripts/check-deps.mjs

python3 -m im_archive_cli.imx_cli run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --page-size "${PAGE_SIZE:-100}" \
  --max-pages "${MAX_PAGES:-50}" \
  --via cdp

python3 -m im_archive_cli.imx_cli roles select --all
python3 -m im_archive_cli.imx_cli run export --kind links
python3 -m im_archive_cli.imx_cli run export --kind structured --formats "${STRUCTURED_FORMATS:-json,markdown}"
python3 -m im_archive_cli.imx_cli state watch --once
```

按需追加 HTML 归档：

```bash
python3 -m im_archive_cli.imx_cli run export --kind singlefile
```

## 成功判定

一次运行不能只看退出码。至少确认：

```bash
python3 -m im_archive_cli.imx_cli state watch --once
find /Users/tsimclaw/Downloads/Ctrip-CS-dialog -type f -name 'IMChatlogExport_*.json' -mtime -2 | head
find /Users/tsimclaw/Downloads/Ctrip-CS-dialog -type f -name 'IM_Archive_links.xlsx' -o -name '*_links.xlsx' | tail
```

结构化 JSON 回读示例：

```bash
python3 - <<'PY'
import json
from pathlib import Path

root = Path('/Users/tsimclaw/Downloads/Ctrip-CS-dialog')
files = sorted(root.rglob('IMChatlogExport_*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
if not files:
    raise SystemExit('no json exports found')
data = json.loads(files[0].read_text(encoding='utf-8'))
print(files[0])
print('sessionId=', data.get('sessionId'))
print('messages=', len(data.get('messages') or []))
print('detailUrl=', data.get('detailUrl'))
PY
```

判定规则：

- `collected=0` 且当天预期有咨询：失败，需要检查登录态、日期、接口契约。
- JSON 文件存在但 `messages=0`：失败，需要检查 `getMessagesBySession` 请求体、请求头、分页参数和登录态；不要回退到 DOM 抓取。
- `run export` 出现 `failed>0`：不算全成功；读取 `failures_file`。
- `--via http` 403 不代表登录态完全失效；优先验证 `--via cdp`。

## 产物位置

Hermes 机器上应优先关注这些文件：

- `.im_archive/state.json`：
  当前会话池、角色选择、最近一次运行摘要。
- `.im_archive/output/im_sessions_<date>.json`：
  当天抓到的会话列表快照。
- `.im_archive/output/<yyyymmdd>/<客服>/IMChatlogExport_*.json`：
  每条会话的结构化对话明细。
- `.im_archive/output/<yyyymmdd>/<客服>/IMChatlogExport_*.md`：
  便于人工浏览的 Markdown。
- `.im_archive/failures.jsonl`：
  导出失败明细。

如果已经额外生成索引文件，也可直接使用：

- `.im_archive/session_index_<date>.csv`
- `.im_archive/session_index_<date>.xlsx`

这些索引适合做 Excel 筛选、消息量排序、客服维度透视。

## 过滤与分析

这个 skill 负责“采集和导出”。对结果做过滤分析时，优先调用同仓库 skill：

- `skills/ctrip-im-parser/SKILL.md`

适用方式：

1. 先完成结构化导出，保证目录中存在 `IMChatlogExport_*.json`。
2. 再用 parser skill 的 `scan_im.py` 扫描 `.im_archive/output/<yyyymmdd>`。
3. 输出汇总 JSON，供后续 Agent 再做业务判断、分类、质检、统计。

典型分析命令：

按关键词筛消息：

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  -k refund \
  -o .im_archive/analysis_refund_20260616.json
```

只看买家消息：

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  --role buyer \
  -o .im_archive/analysis_buyer_20260616.json
```

抽取订单卡：

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  --extract orders \
  -o .im_archive/analysis_orders_20260616.json
```

看上下文窗口：

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  -k cancel \
  --ctx 3 \
  -o .im_archive/analysis_cancel_ctx_20260616.json
```

看响应时延：

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  --seq-diff \
  -o .im_archive/analysis_gaps_20260616.json
```

分析时的实务建议：

- 做客服质量分析时，先跑 `--ctx`，再由上层 Agent 判断话术是否有效。
- 做退款/改期/投诉类聚类时，先用 `-k` 产生子集，再让上层 Agent 归类。
- 做响应效率统计时，优先分析 `buyer -> seller` 的相邻 gap，而不是所有 gap 混算。
- 订单信息不要只从文本里猜，优先读取 `rawHtml` 里的订单卡抽取结果。

## 常见故障处理

### CDP proxy 不可用

症状：`curl http://localhost:3456/targets` 失败或超时。

处理：

```bash
node /Users/tashima_meru/.cc-switch/skills/web-access/scripts/check-deps.mjs
curl -s http://localhost:3456/targets
curl -s http://127.0.0.1:9222/json/list | python3 -m json.tool | rg 'vbooking\\.ctrip\\.com|IMExperience|webSocketDebuggerUrl'
```

如果 `cdp_proxy_base_url` 不可用但 `cdp_port` DevTools endpoint 可用，`run collect --via cdp` 会自动 fallback 到 `127.0.0.1:<cdp_port>`，并输出：

```text
cdp_proxy_base_url 不可用，已 fallback 到 127.0.0.1:9222 DevTools endpoint
```

SingleFile 归档仍需要兼容 `/new`、`/eval`、`/close` 的 proxy；direct DevTools fallback 只覆盖 `collect --via cdp` 的页面上下文 fetch。

### 没有 vbooking 页面

症状：`CtripImCdpFetchClient` 报未找到 `vbooking.ctrip.com` 页面。

处理：打开 IMExperience 页面并确认登录态。无人值守任务应告警，不应反复打开新页面刷屏。

### 403

症状：`structured --via http` 返回 401/403，或业务错误提示登录态、Token、风控。

处理：不要循环重试，也不要把 structured export 改成 `--via cdp`。先刷新登录态，再执行 `discover detail-xhr` 复核详情消息接口合同和 Cookie 同步状态。`collect --via cdp` 仍可作为列表采集验证，不代表 HTTP 详情导出一定可用。

### 空会话或空消息

检查详情页 URL 是否保留完整 query，尤其是 `&sessionId=`。历史 bug 是 CDP proxy 新建 tab 时未完整 URL 编码，导致 `sessionId` 被截断。

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = sorted(Path('/Users/tsimclaw/Downloads/Ctrip-CS-dialog').rglob('IMChatlogExport_*.json'))[-1]
data = json.loads(p.read_text(encoding='utf-8'))
print(data.get('detailUrl'))
print(len(data.get('messages') or []))
PY
```

### 输出目录权限

默认输出目录是：

```text
/Users/tsimclaw/Downloads/Ctrip-CS-dialog
```

Hermes Agent 主机上如用户不同，先在 `config.yaml` 改 `output_dir` 到 Agent 可写目录，再运行。

### 结果很多，如何快速筛

先看索引：

```bash
python3 -m im_archive_cli.imx_cli state watch --once
```

然后优先使用：

- `session_index_<date>.csv/.xlsx` 做客服、消息量、附件数量筛选。
- `scan_im.py` 的 `-k`、`--role`、`--extract orders`、`--ctx`、`--seq-diff` 做机器侧二次过滤。

## 不要做

- 不要把 Cookie、Authorization、cticket、完整请求头写入日志、README、skill 或 durable memory。
- 不要在 403 后无限重试；最多做一次 `cdp` 模式验证，然后告警。
- 不要用纯 requests 成功与否判断整个携程登录态；以当前已登录浏览器页面为真相面。
- 不要把 `structured` 改回 CDP/Selenium DOM 抓取；结构化 JSON/Markdown 只走已验证详情接口的 HTTP 请求。
- 不要把 `singlefile` 改回 chromedriver 依赖；Hermes 自动主机应走 `cdp_proxy_base_url` 或兼容该接口的 proxy。
