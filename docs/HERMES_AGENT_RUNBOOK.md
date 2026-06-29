# Hermes Agent Runbook for Ctrip IM Archive

本文档给 Hermes Agent 运行机器使用，说明如何在本项目中采集携程客服历史咨询会话、导出对话详情、传入筛选参数、理解接口请求体，以及对结果做过滤分析。

## 1. 总览

项目路径：

```bash
cd /Users/tashima_meru/Develop/tripcom_cs_dialog_scraping
```

核心 CLI：

```bash
python3 -m im_archive_cli.imx_cli --help
```

当前已验证的正式链路：

```text
已登录 vbooking 页面 / CDP Proxy
  -> run collect --via cdp
  -> state.json 会话池
  -> roles select
  -> run export --kind structured --via http
  -> IMChatlogExport_*.json / *.md
  -> scan_im.py / CSV / XLSX 二次分析
```

重要结论：

- `run export --kind structured` 只支持 `--via http`，通过已验证的 `16037/getMessagesBySession` 请求合同导出 JSON/Markdown。
- `--via cdp` 仍用于采集列表、请求发现、预检和 SingleFile 页面归档；不要再用 CDP/Selenium DOM 或旧详情页注入脚本解析聊天列表生成结构化 JSON/Markdown。
- 日期区间可用 CLI 参数传入；客人来源和咨询场景当前通过配置字段传入。

## 2. 前置条件

检查 CLI：

```bash
python3 -m im_archive_cli.imx_cli --help
```

检查 web-access CDP Proxy：

```bash
curl -s http://localhost:3456/targets
```

检查是否已有携程后台页面：

```bash
curl -s http://localhost:3456/targets | python3 -m json.tool | rg 'vbooking\\.ctrip\\.com|IMExperience'
```

如果没有页面，打开入口页并完成登录：

```bash
open 'https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience'
```

检查配置：

```bash
python3 - <<'PY'
from pathlib import Path
from im_archive_cli.config import load_or_create_config
cfg = load_or_create_config(Path('.im_archive/config.yaml'))
print('output_dir=', cfg.output_dir)
print('state_file=', cfg.state_file)
print('cdp_proxy_base_url=', cfg.cdp_proxy_base_url)
print('productChannel=', cfg.ctrip_im_product_channel)
print('consultationScene=', cfg.ctrip_im_consultation_scene)
PY
```

Hermes 机器优先使用仓库内输出目录：

```text
.im_archive/output
```

不要默认写入 `/Users/tsimclaw/Downloads/Ctrip-CS-dialog`，除非该目录在当前机器上存在且可写。

## 3. 筛选项与请求字段

历史咨询页面的核心筛选项与接口字段对应如下：

| UI 筛选项 | 请求字段 | 说明 |
| --- | --- | --- |
| 客人来源 | `productChannel` | 已实测 Trip = `trip`，汇总 = `aggregate` |
| 咨询场景 | `consultationScene` | 已实测汇总 = `aggregate` |
| 历史咨询日期 | `startDate` / `endDate` | 自定义日期区间使用两个 `YYYY-MM-DD` |
| 业务类型 | `butype` | 当前为 `品类活动` |
| 分页 | `pageNo` / `pageSize` | 从 1 开始分页 |
| 排序 | `orderColumn` / `orderType` | 会话列表按 `session_create_time asc` |

已确认映射：

```text
客人来源 Trip       -> productChannel = trip
客人来源 汇总       -> productChannel = aggregate
咨询场景 汇总       -> consultationScene = aggregate
自定义日期 6/16-6/16 -> startDate = 2026-06-16, endDate = 2026-06-16
```

推断但未逐项实测的映射：

```text
客人来源 CTrip -> productChannel = ctrip
咨询场景 售前  -> consultationScene = presale
咨询场景 售后  -> consultationScene = postsale
```

当前 CLI 参数支持：

```bash
--start-date YYYY-MM-DD
--end-date YYYY-MM-DD
--page-size 1000
--max-pages 50
--include <客服账号/客服名/显示名>
--via cdp|http|browser
```

当前 CLI 未直接暴露：

```text
--product-channel
--consultation-scene
```

要改变客人来源或咨询场景，修改 `.im_archive/config.yaml`：

```yaml
ctrip_im_product_channel: trip
ctrip_im_consultation_scene: aggregate
ctrip_im_butype: 品类活动
ctrip_im_currency_type: CNY
```

也可以准备不同配置文件，并通过 `--config <path>` 指定：

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.trip.yaml run collect \
  --start-date 2026-06-16 \
  --end-date 2026-06-16 \
  --via cdp
```

注意：`--config` 是全局参数，必须放在 `run collect` 之前。

## 4. 接口请求格式

### 4.1 客服列表接口

用途：获取符合筛选条件的客服账号列表，以及每个客服的会话指标。

接口：

```text
POST https://m.ctrip.com/restapi/soa2/13807/getEmployeeDimMetricDetailsV3
```

请求体示例：

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

响应中重点字段：

```text
tableDataItemList[].dimMap.vendor_account_id
tableDataItemList[].dimMap.vendor_account_name
tableDataItemList[].metricMap.sev_session_count
totalNum
```

### 4.2 单客服会话列表接口

用途：对某个客服账号展开历史咨询会话，得到 `session_id` 与会话创建时间。

接口：

```text
POST https://m.ctrip.com/restapi/soa2/13807/getSessionDimMetricDetailsV3
```

请求体示例：

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

响应中重点字段：

```text
totalNum
tableDataItemList[].dimMap.session_id
tableDataItemList[].dimMap.session_create_time
```

详情页 URL 由 `session_id` 生成：

```text
https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=<session_id>
```

### 4.3 对话详情消息接口

已通过页面资源与 bundle 证据确认：

```text
POST https://m.ctrip.com/restapi/soa2/16037/getMessagesBySession
```

请求体：

```json
{
  "sessionId": "300001148712239"
}
```

结构化 JSON/Markdown 导出只允许使用 HTTP 详情接口：

```bash
imx run export --kind structured --via http --formats json,markdown
```

如果 HTTP 详情导出返回 `401/403` 或 `messages` 为空，不要回退到 CDP/Selenium DOM 聊天记录抓取。应先执行 `discover detail-xhr` 复核 `getMessagesBySession` 请求体、请求头和登录态，再修正配置。

## 5. 典型执行链路

### 图片导出验收

对结构化导出的正文图片，运行一次检查并确认：

```bash
python3 - <<'PY'
from pathlib import Path
import json

base_dir = Path('.im_archive/output')
inline_images = downloaded = failed = 0

for json_file in base_dir.rglob('*_*.json'):
    data = json.loads(json_file.read_text(encoding='utf-8'))
    for m in data.get('messages', []):
        for a in m.get('attachments', []) or []:
            if a.get('source') != 'messageBody':
                continue
            inline_images += 1
            if a.get('downloadStatus') == 'failed':
                failed += 1
            elif a.get('localPath') or a.get('relativePath') or a.get('src'):
                downloaded += 1

print(f'inline_images={inline_images}')
print(f'downloaded={downloaded}')
print(f'failed={failed}')
PY
```

验收标准：

- `inline_images` = `downloaded + failed`（可由脚本外加一条审计一致性检查）
- `failed` 允许非零，但需记录对应 `sessionId`、`sequence` 与文件名，且该失败不应标记为会话导出失败
- `downloadStatus` 为 `failed` 的图片不计入系统错误，只计入补跑/回写清单
- 对图片引用的报告必须带 `sessionId` + `sequence` + 本地路径；禁止将头像/卡片图记为客人正文图片

### 5.1 全量导出某一天

```bash
cd /Users/tashima_meru/Develop/tripcom_cs_dialog_scraping

RUN_DATE="2026-06-16"

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --page-size 1000 \
  --max-pages 50 \
  --via cdp

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml roles select --all

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export \
  --kind structured \
  --formats json,markdown \
  --via cdp
```

### 5.2 自定义日期区间

```bash
START_DATE="2026-06-01"
END_DATE="2026-06-16"

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run collect \
  --start-date "$START_DATE" \
  --end-date "$END_DATE" \
  --page-size 1000 \
  --max-pages 50 \
  --via cdp
```

### 5.3 只导出某个客服

```bash
RUN_DATE="2026-06-16"
ROLE="vbk_2538177/门票活动旅游管家Sara"

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --include "$ROLE" \
  --page-size 1000 \
  --max-pages 50 \
  --via cdp

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml roles select --include "$ROLE"

python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export \
  --kind structured \
  --formats json,markdown \
  --via cdp
```

### 5.4 只导出链接表

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export --kind links
```

### 5.5 诊断 HTTP 结构化导出

结构化 JSON/Markdown 导出只支持 `--via http`。使用前先确认：

- `imx auth status` 能找到可用 cookie header 或 auth JSON。
- 1 请求预算 probe 不返回空体 `HTTP 403`。
- 详情接口配置为 `https://m.ctrip.com/restapi/soa2/16037/getMessagesBySession`，且 `ctrip_im_detail_verified_source: browser_detail_xhr`。

本次浏览器抓包确认的请求头合同：

- `13807/*` 列表接口来自 `vbooking.ctrip.com`，显式 header 为 `Accept: application/json, text/plain, */*`、`Content-Type: application/json;charset=UTF-8`、`appname: vbkbusiness`，浏览器自动补 `Origin: https://vbooking.ctrip.com`、`Referer: https://vbooking.ctrip.com/` 和 `.ctrip.com` 域 cookie。
- `16037/getMessagesBySession` 详情接口来自 `imvendor.ctrip.com`，显式 header 为 `Content-Type: application/json`、`cookieOrigin: https://imvendor.ctrip.com`，浏览器自动补 `Origin: https://imvendor.ctrip.com`、`Referer: https://imvendor.ctrip.com/` 和 `.ctrip.com` 域 cookie。
- 登录态不通过显式 `Authorization` 传输。不要在日志、文档、提交或工件中写入真实 cookie 值。

先用小预算 probe：

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run collect \
  --start-date "$RUN_DATE" \
  --end-date "$RUN_DATE" \
  --page-size 10 \
  --max-pages 1 \
  --via http \
  --request-budget 1 \
  --request-ledger ".im_archive/ctrip-request-ledger-${RUN_DATE}-http-probe.json"
```

如果返回 `HTTP 401/403` 或业务错误如 `Token为空`，不要循环重试，也不要回退到 DOM 聊天记录抓取。记录为登录态新鲜度、浏览器 cookie 同步或风控层阻碍，刷新登录态后重新执行 `discover detail-xhr` 并对照 `getMessagesBySession` 请求体和 header 合同。`messages` 为空不视为成功导出，必须记录失败样本。

## 6. 输出文件

主要输出：

```text
.im_archive/state.json
.im_archive/output/im_sessions_<YYYY-MM-DD>.json
.im_archive/output/<yyyymmdd>/<客服>/IMChatlogExport_<timestamp>_<sessionId>_<客服>.json
.im_archive/output/<yyyymmdd>/<客服>/IMChatlogExport_<timestamp>_<sessionId>_<客服>.md
.im_archive/failures.jsonl
```

可选索引：

```text
.im_archive/session_index_<YYYY-MM-DD>.csv
.im_archive/session_index_<YYYY-MM-DD>.xlsx
```

一次成功导出的 JSON 结构：

```json
{
  "sessionId": "300001146864764",
  "csName": "vbk_2446186/门票活动旅游管家Fiona",
  "detailUrl": "https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=300001146864764",
  "title": "供应商客服工作台",
  "createTime": "2026-06-16 11:05:48",
  "exportedAt": "2026-06-18T...",
  "messages": [
    {
      "sequence": 1,
      "timestampText": "2026-06-16 11:06:23",
      "senderRole": "buyer",
      "senderName": "...",
      "messageType": "text",
      "text": "你好",
      "rawHtml": "...",
      "attachments": []
    }
  ]
}
```

## 7. 成功判定

看状态：

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml state watch --once
```

检查导出文件数：

```bash
find .im_archive/output -name 'IMChatlogExport_*.json' | wc -l
find .im_archive/output -name 'IMChatlogExport_*.md' | wc -l
```

抽样检查消息数：

```bash
python3 - <<'PY'
import json
from pathlib import Path
files = sorted(Path('.im_archive/output').rglob('IMChatlogExport_*.json'))
print('files=', len(files))
for p in files[:3]:
    data = json.loads(p.read_text(encoding='utf-8'))
    print(p, data.get('sessionId'), len(data.get('messages') or []))
PY
```

判定规则：

- `collected=0` 且预期有咨询：检查登录态、日期、`productChannel`、`consultationScene`。
- 导出 JSON 存在但 `messages=0`：检查 `getMessagesBySession` 请求体、请求头、分页参数和登录态，不要回退到 DOM 抓取。
- `failures.jsonl` 非空：读取失败原因后按 session 重试。
- `--via http` 返回 403：不等于账号失效，先执行 `discover detail-xhr` 复核接口合同和登录态。

## 8. 结果过滤与分析

推荐使用同仓库 skill 的脚本：

```text
skills/ctrip-im-parser/scripts/scan_im.py
```

### 8.1 关键词筛选

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  -k refund \
  --ctx 3 \
  -o .im_archive/analysis_refund_ctx_20260616.json
```

### 8.2 只看买家消息

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  --role buyer \
  -o .im_archive/analysis_buyer_20260616.json
```

### 8.3 提取订单卡

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  --extract orders \
  -o .im_archive/analysis_orders_20260616.json
```

### 8.4 响应时延

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  --seq-diff \
  -o .im_archive/analysis_gaps_20260616.json
```

后续 Agent 分析建议：

- 质量检查：先用关键词或 `--ctx` 缩小范围，再由 Agent 判断话术是否符合规则。
- 退款/投诉/取消类问题：先用关键词抽样，再做会话级归类。
- 响应效率：只统计 `buyer -> seller` 的相邻消息间隔，不要把系统消息混入平均值。
- 订单分析：优先使用 `--extract orders` 读取 `rawHtml` 中订单卡，不要只靠正文猜测。

## 9. 故障处理

### 9.1 CDP Proxy 不可用

```bash
node /Users/tashima_meru/.cc-switch/skills/web-access/scripts/check-deps.mjs
curl -s http://localhost:3456/targets
```

### 9.2 找不到 vbooking 页面

打开入口并登录：

```bash
open 'https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience'
```

Hermes 自动任务遇到登录墙时应停止并告警，不应无限重试。

### 9.3 输出目录无权限

把 `.im_archive/config.yaml` 中的 `output_dir` 改为当前用户可写目录，例如：

```yaml
output_dir: .im_archive/output
```

### 9.4 日期区间没有数据

检查：

```text
startDate/endDate 是否正确
productChannel 是否过窄
consultationScene 是否过窄
是否选择了错误客服 include
页面登录态是否仍有效
```

### 9.5 请求节奏

结构化导出的节奏由配置控制：

```yaml
window_sec: 2
concurrency: 4
```

HTTP 结构化导出按批次节流：

```text
每批最多 concurrency 个请求，批次间隔约 window_sec / concurrency 秒
```

例如 `2 / 4 = 0.5` 秒，即每 0.5 秒最多并发发出 4 个详情请求。需要更慢时提高 `window_sec` 或降低 `concurrency`。

## 10. 不要做

- 不要把 Cookie、Authorization、cticket、完整请求头写入 README、skill、日志或长期记忆。
- 不要在 `HTTP 403` 后无限重试。
- 不要把 `--via http` 的失败当作整个账号失效。
- 不要在没有用户要求时对真实携程接口做高频探测。
- 不要把详情接口 URL 手填到配置后直接正式跑；纯 HTTP 详情导出应先经过 `discover detail-xhr` 验证。
