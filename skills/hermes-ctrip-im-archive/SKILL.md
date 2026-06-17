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
python3 -m pytest
```

测试失败时不要继续跑正式归档；先修复本地代码或环境。

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
python3 -m im_archive_cli.imx_cli run export --kind structured --formats "${STRUCTURED_FORMATS:-json}"
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
- JSON 文件存在但 `messages=0`：失败，需要检查详情页是否登录、`sessionId` 是否被截断、`detail-page.js` 选择器是否失效。
- `run export` 出现 `failed>0`：不算全成功；读取 `failures_file`。
- `--via http` 403 不代表登录态完全失效；优先验证 `--via cdp`。

## 常见故障处理

### CDP proxy 不可用

症状：`curl http://localhost:3456/targets` 失败。

处理：

```bash
node /Users/tashima_meru/.cc-switch/skills/web-access/scripts/check-deps.mjs
curl -s http://localhost:3456/targets
```

### 没有 vbooking 页面

症状：`CtripImCdpFetchClient` 报未找到 `vbooking.ctrip.com` 页面。

处理：打开 IMExperience 页面并确认登录态。无人值守任务应告警，不应反复打开新页面刷屏。

### 403

症状：`--via http` 返回 403。

处理：改用默认 `--via cdp`。如果 `--via cdp` 也失败，检查页面是否登录、是否出现风控/登录墙。

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

## 不要做

- 不要把 Cookie、Authorization、cticket、完整请求头写入日志、README、skill 或 durable memory。
- 不要在 403 后无限重试；最多做一次 `cdp` 模式验证，然后告警。
- 不要用纯 requests 成功与否判断整个携程登录态；以当前已登录浏览器页面为真相面。
- 不要把 `structured` / `singlefile` 改回 chromedriver 依赖；Hermes 自动主机应走 `cdp_proxy_base_url`。

