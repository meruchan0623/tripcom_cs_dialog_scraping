# Trip.com IM 会话归档助手

一个基于 Chrome Manifest V3 的浏览器扩展，用于在 Trip.com 供应商平台批量抓取并归档 IM 会话详情页，导出为本地 HTML 文件，方便审计、复盘和离线留档。

## 核心能力

- 自动遍历客服会话列表并提取可归档会话
- 通过 React Fiber 获取 `session_id`，减少手工点选
- 批量打开会话详情页并导出简化版 HTML
- 支持任务开始、暂停、恢复、取消
- 提供进度显示与运行日志，便于追踪失败项

## 目录结构

```text
tripcom_cs_dialog_scraping/
├─ background.js
├─ content-script.js
├─ detail-page.js
├─ page-bridge.js
├─ popup.html
├─ popup.js
├─ singlefile-runner.js
├─ xlsx-exporter.js
├─ manifest.json
├─ icons/
└─ lib/
```

## 快速开始

1. 打开 Chrome，进入 `chrome://extensions/`
2. 开启右上角「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择当前项目根目录：`tripcom_cs_dialog_scraping`
5. 打开 Trip.com 供应商平台抓取入口页面：`https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience`
6. 刷新页面，确保内容脚本完成注入
7. 点击扩展图标，配置参数并启动归档

## 技术说明

| 模块 | 说明 |
|------|------|
| 架构 | Manifest V3 + Service Worker |
| 页面交互 | Content Script + DOM / React Fiber |
| UI | Popup HTML + JavaScript |
| 导出方式 | 简化版 HTML（Blob 下载） |
| 适配页面 | `vbooking.ctrip.com` / `imvendor.ctrip.com` |

## 输出规则

- 保存位置：默认导出到 `/Users/tsimclaw/Downloads/Ctrip-CS-dialog`
- 文件命名：`IMChatlogExport_{会话创建时间yyyyMMddHHmmss}_{sessionId}_{客服名}.{html|json|md}`
- 目录层级：`Ctrip-CS-dialog/{yyyyMMdd}/{客服名}/导出文件`（创建时间优先取会话列表里的创建时间）

## 已知限制

- 当前为简化归档方案，外链资源不保证完全离线可用
- 页面 DOM 或数据结构变更后，选择器/提取逻辑可能需更新
- 首次使用建议先刷新业务页面，避免注入时机问题

## 后续优化建议

1. 接入完整版 SingleFile，提高离线完整度
2. 增加失败重试与错误分类统计
3. 引入配置持久化与批次任务记录

## 故障排查

### 点击开始后无动作

- 确认当前标签页在目标域名下
- 刷新业务页面后重试
- 在扩展详情页检查 service worker 是否正常运行

### 无法识别会话列表

- 确认已进入 IM 会话详情页面
- 等待表格加载完成再执行
- 若平台改版，需同步更新选择器

### 会话页打开但导出失败

- 检查浏览器下载权限
- 确认登录态仍然有效
- 查看弹窗日志定位具体 `sessionId`

## Python CDP 控制台（imx）

本仓库新增 `imx` 命令：Python 通过 CLI 执行采集/筛选/导出。会话采集默认走“模拟前端请求”模式：复用当前已登录的 `vbooking.ctrip.com` 页面，在页面上下文内直接 `fetch` 携程后台 SOA 接口，不再依赖展开表格的 DOM 点击循环。结构化和 SingleFile 导出同样复用 web-access CDP proxy 的当前登录浏览器，不再依赖 chromedriver 下载。
`imx chrome start` 会自动准备并加载当前仓库扩展（开发者模式 `--load-extension`），无需手工去 `chrome://extensions` 点击“加载已解压扩展”。

### 安装

```bash
pip install -e .
```

依赖包含 `websocket-client`，用于连接 Chrome DevTools Protocol。

如果你在终端看到 `imx: command not found`（或 PowerShell 的 `The term 'imx' is not recognized`），按下面处理：

```bash
# mac / openclaw
bash scripts/openclaw_setup.sh
source .venv/bin/activate
imx --help
```

兜底方式（即使没激活 venv 也可执行）：

```bash
# 方式 1：仓库内直接调用
./imx --help

# 方式 2：模块方式调用
python3 -m im_archive_cli.imx_cli --help
```

Windows 仓库内兜底：

```powershell
.\imx.cmd --help
python -m im_archive_cli.imx_cli --help
```

### 默认运行速度（插件）

插件当前默认节流速度为：
- `时间窗口内页数`：`20`
- `时间窗口(秒)`：`10`

对应含义：每 10 秒允许打开 20 页详情页（SingleFile/结构化导出都按该节流控制）。

### 扩展自动加载机制（开发者模式）

- `imx` 启动 Chrome 时会自动带上：
  - `--load-extension=<extension_dir>`
- 默认 `extension_dir: .`（即仓库根目录，与你手工“加载已解压扩展”一致）
- 如扩展源码放在其他目录，可在 `config.yaml` 配置 `extension_dir`

### openclaw 常用执行步骤（推荐）

你常用的主流程是：
1. 获取会话
2. 选择全部或某位客服
3. 导出结构化对话 JSON

下面给出可直接复制的命令：

```bash
# 1) 启动 Chrome（首次建议有头）
imx chrome start --headed

# 2) 首次登录（只需一次，后续复用 profile）
imx auth login

# 3) 抓取会话（默认 --via cdp：在已登录页面内模拟前端请求）
imx run collect --start-date 2026-06-16 --end-date 2026-06-16 --page-size 100

# 4) 查看客服筛选列表
imx roles list

# 5a) 选择全部客服
imx roles select --all

# 5b) 或只选择某位/多位客服
imx roles select --include "张三,李四"

# 6) 导出结构化对话（默认 JSON，可用 --formats 选择 Markdown）
imx run export --kind structured --formats json,markdown
```

如果你只要 JSON，可直接执行 `imx run export --kind structured`。

### openclaw 一键脚本（推荐）

仓库已提供可直接执行的脚本：

```bash
# 0) 首次环境初始化
bash scripts/openclaw_setup.sh

# 1) 首次登录（手工一次）
bash scripts/openclaw_login.sh

# 2a) 之后日常：全量客服结构化导出
bash scripts/openclaw_structured_export.sh --all

# 2b) 之后日常：仅某位客服结构化导出
bash scripts/openclaw_structured_export.sh --role "张三"

# 2c) 之后日常：多位客服结构化导出
bash scripts/openclaw_structured_export.sh --roles "张三,李四"
```

脚本说明：
- `scripts/openclaw_setup.sh`：创建 `.venv` 并安装 `imx`
- `scripts/openclaw_login.sh`：有头登录并持久化 profile
- `scripts/openclaw_structured_export.sh`：固定流程“获取会话 -> 角色筛选 -> 导出结构化”

### CLI 参数总览（详细）

#### 全局参数
- `--config <path>`：指定配置文件路径（默认 `config.yaml`）

#### `imx chrome start`
- `--headed`：有头启动 Chrome（不带该参数时按默认无头）
- `--debug`：以 CDP 调试模式启动（主要用于旧 `--via browser` 插件采集兜底）
  - 不加 `--debug` 时为“非调试启动”，用于先确认插件能正常加载

示例：
```bash
imx chrome start
imx chrome start --headed
imx chrome start --headed --debug
imx --config /data/im/config.yaml chrome start --headed
```

#### `imx auth login`
- 无额外参数  
- 用于首次人工登录并写入持久 profile

示例：
```bash
imx auth login
```

#### `imx auth status`
- 无额外参数
- 只读检查 `ctrip-cli-sessions` 登录态文件，不访问携程接口，不输出 Cookie 值

默认会优先使用 `ctrip_cookie_header.txt`，不存在或为空时 fallback 到 `ctrip_auth_plain.json` 的 `cookieHeader`。输出只包含文件路径、可用性、Cookie header 长度、Cookie 名称列表、JSON 的 `createdAt/source` 等脱敏信息。

示例：
```bash
imx auth status
```

#### `imx run collect`
- `--page-size <int>`：采集分页大小（建议 `100`）
- `--max-pages <int>`：每位客服最多读取页数
- `--start-date <YYYY-MM-DD>`：历史咨询开始日期，默认昨天
- `--end-date <YYYY-MM-DD>`：历史咨询结束日期，默认同开始日期
- `--include "A,B,C"`：只采集指定客服，可填完整显示名、账号 ID 或昵称
- `--via cdp|http|browser`：
  - `cdp`：默认；在当前已登录携程页面上下文执行真实前端 `fetch`
  - `http`：纯 Python requests，读取 `ctrip-cli-sessions` 中的 Cookie header；部分接口可能被 403 拦截
  - `browser`：旧扩展点击采集路径，作为兼容兜底；该路径无法精确接入请求账本，使用请求预算时请选 `cdp` 或 `http`
- `--request-budget <int>`：本次最多允许发出的携程接口请求数，最大 `30`；达到上限前会停止，不会发出下一次请求
- `--request-ledger <path>`：跨多条命令累计请求数的 JSON 账本；必须配合 `--request-budget 30` 使用，可控制整轮总请求数

示例：
```bash
imx run collect --start-date 2026-06-16 --end-date 2026-06-16 --page-size 100
imx run collect --start-date 2026-06-16 --end-date 2026-06-16 --include "vbk_2538177" --page-size 10 --max-pages 1
imx run collect --via http --start-date 2026-06-16 --end-date 2026-06-16
imx run collect --via http --start-date 2026-06-16 --end-date 2026-06-16 --request-budget 30 --request-ledger .im_archive/ctrip-request-ledger.json
imx run collect --via browser --page-size 100
```

#### `imx roles list`
- 无额外参数
- 从 Python state 输出当前可选客服与已选标记，不依赖浏览器插件 state

#### `imx roles select`
- `--all`：全选当前可选客服
- `--include "A,B,C"`：按逗号指定客服名

示例：
```bash
imx roles select --all
imx roles select --include "张三"
imx roles select --include "张三,李四"
```

#### `imx run export`
- `--kind singlefile|structured|links`
  - `singlefile`：归档 HTML
  - `structured`：结构化导出（JSON/Markdown）
  - `links`：导出链接表 xlsx
- `--formats json,markdown`：仅 `structured` 生效，默认 `json`
- `--output <path>`：仅 `links` 生效，指定 xlsx 输出路径
- `--via cdp|http`：仅 `structured` 生效；`http` 为纯 Python requests 导出详情消息
- `--request-budget <int>`：本次 HTTP 详情导出最多允许发出的携程接口请求数，最大 `30`
- `--request-ledger <path>`：跨多条命令累计请求数的 JSON 账本；必须配合 `--request-budget 30` 使用，建议与发现/采集命令共用同一路径

`links` 导出直接读取 Python state 并写本地 xlsx；`structured` 默认通过 `cdp_proxy_base_url` 打开会话详情页导出，也可用
`--via http` 走纯 requests 详情接口；`singlefile` 仍通过 `cdp_proxy_base_url` 打开详情页归档，不再读取插件内部 `archiveState`。
`--request-budget/--request-ledger` 只支持 `structured --via http`；CDP 页面导出和 SingleFile 页面归档无法逐个请求精确计数，带预算参数会被拒绝。
HTTP 详情导出在发出第一条请求前还会检查账本剩余额度是否至少覆盖当前选中的会话数；如果 `remaining < selected_sessions`，命令会提前停止，避免明知预算不足还先请求一部分会话。

示例：
```bash
imx run export --kind structured
imx run export --kind structured --formats json,markdown
imx run export --kind structured --via http --formats json,markdown --request-budget 30 --request-ledger .im_archive/ctrip-request-ledger.json
imx run export --kind singlefile
imx run export --kind links --output /data/IM_Archive_links.xlsx
```

#### `imx request-budget status`
- `--request-budget <int>`：本轮携程接口请求总预算，最大 `30`
- `--request-ledger <path>`：跨命令累计请求数的 JSON 账本路径

输出包含 `used`、`remaining`、`exceeded`。如果 `exceeded: true`，说明账本已记录超过本轮上限，后续真实携程步骤必须停止。

示例：
```bash
imx request-budget status --request-budget 30 --request-ledger .im_archive/ctrip-request-ledger.json
```

#### `imx preflight`
- `--request-budget <int>`：本轮携程接口请求总预算，最大 `30`
- `--request-ledger <path>`：跨命令累计请求数的 JSON 账本路径
- `--via cdp|proxy`：默认 `proxy`，同时检查当前浏览器是否已有携程管理页/详情页 target
- `--cdp-base-url <url>`：`--via cdp` 时覆盖原生 DevTools HTTP 地址

示例：
```bash
imx preflight --request-budget 30 --request-ledger .im_archive/ctrip-request-ledger.json --via proxy
```

输出中的 `ready` 为 `true` 时再继续执行真实 `detail-xhr` / HTTP 导出；为 `false` 时先处理 `issues`。该命令会同时输出：
- `requestBudget`：跨命令请求账本剩余额度
- `auth`：脱敏后的 `ctrip-cli-sessions` 登录态文件状态，不包含 Cookie 值
- `browser`：当前 CDP/proxy 下的携程后台/详情页 target 状态
当 `requestBudget.exceeded` 为 `true` 时，preflight 会直接 `ready=false`，并提示必须停止目标实现。

#### `imx self-test http-export`
- `--output-dir <path>`：本地自测产物目录，默认 `.im_archive/selftest`
- `--request-budget <int>`：本地 mock 请求预算，最大 `30`，默认 `1`

该命令只启动 `127.0.0.1` mock 详情接口，使用真实 `requests` 客户端、预算计数、结构化导出和文件写入路径做端到端自测；不会访问携程接口，也不会消耗携程请求账本。

示例：
```bash
imx self-test http-export --request-budget 1
```

HTTP 详情导出默认候选接口为：

```yaml
ctrip_im_detail_messages_url: ""
ctrip_im_detail_page_size: 100
ctrip_im_detail_extra_body: null
ctrip_im_detail_verified_source: ""
ctrip_im_detail_verified_at: ""
```

详情消息接口必须来自登录后详情页的真实 XHR。缓存中可见 `/15529/queryIMSessionInfo`，但活体请求只返回
`imSessionInfoList/count/manualCount`，不能作为消息列表接口使用。确认真实消息接口后，可在 `config.yaml` 覆盖
`ctrip_im_detail_messages_url` 或追加 `ctrip_im_detail_extra_body`，无需改代码；也可用
`imx discover apply-config --report <发现报告>` 自动写回。纯 requests 路径仍可能被携程 403 拦截；这种情况下默认
`cdp` 路径仍是可用兜底。
`detail-xhr` 报告如果捕获到同 URL 的 POST JSON 请求体，会把 `pageSize` 和非动态字段提取到推荐配置中；动态字段
如 `sessionId`、`pageNo`、`accountsource` 会由脚本按当前会话重新生成，避免把浏览器里某一条会话 ID 固化进配置。
为避免错误接口请求，`run export --kind structured --via http` 会在发请求前拒绝 `/15529/queryIMSessionInfo` 这类已知非消息详情接口；正确流程是先在浏览器详情页用 `imx discover detail-xhr` 确认真实消息列表接口，再执行纯 requests 模拟请求。
对 `ctrip.com` / `trip.com` 域名的详情接口，纯 requests 导出还要求配置中存在
`ctrip_im_detail_verified_source: browser_detail_xhr`；该字段由 `imx discover apply-config --report <发现报告>` 自动写入。手工填 URL 但没有浏览器发现证明时，命令会在发请求前停止。

#### `imx import links`
- `--file <xlsx>`：要导入的链接表文件
- `--preview`：仅预览，不执行导入
- `--confirm`：直接确认导入（跳过交互确认）

导入结果会写入 Python state，后续 `roles` / `run export` 直接复用。

示例：
```bash
imx import links --file /data/IM_Archive_links.xlsx --preview
imx import links --file /data/IM_Archive_links.xlsx --confirm
```

#### `imx state watch`
- `--interval-sec <float>`：轮询间隔，默认 `1.0`
- `--once`：只打印一次当前状态

该命令查看 Python state 中的采集数、已选角色数和最近导出摘要。

示例：
```bash
imx state watch --once
imx state watch --interval-sec 2
```

#### `imx discover detail-xhr`
- `--session-id <id>`：要打开的 IM 会话 ID
- `--request-budget <int>`：本次发现最多允许发出的携程接口请求数，最大 `30`
- `--request-ledger <path>`：跨多条命令累计请求数的 JSON 账本；必须配合 `--request-budget 30` 使用，发现命令会按账本剩余额度执行
- `--wait-sec <float>`：打开详情页后等待 XHR 的秒数，默认 `8`
- `--output <path>`：将发现报告写入 JSON 文件
- `--via cdp|proxy`：默认 `cdp`，使用原生 CDP `Network` / `Fetch` 事件在请求发出前做预算守卫；`proxy` 是旧的 eval 探针，只作诊断备用
- `--cdp-base-url <url>`：覆盖原生 CDP HTTP 地址，例如 `http://127.0.0.1:9333`

该命令默认连接 `config.yaml` 的 `cdp_port`，新开一个详情页，用 CDP `Fetch.requestPaused` 在请求发出前执行预算守卫：
达到上限后直接阻止下一次匹配的携程接口请求。输出报告包含请求、响应样本、疑似消息接口候选，以及可复制到
`config.yaml` 的 `recommendedConfig`。如果请求账本剩余额度为 `0`，命令会在打开详情页前直接停止。

示例：
```bash
imx chrome start --headed --debug
imx discover cdp-status
imx request-budget status --request-budget 30 --request-ledger .im_archive/ctrip-request-ledger.json
imx preflight --request-budget 30 --request-ledger .im_archive/ctrip-request-ledger.json --via proxy
imx discover detail-xhr --session-id 100001127051842 --request-budget 30 --request-ledger .im_archive/ctrip-request-ledger.json --output .im_archive/detail_xhr_probe.json
imx discover detail-xhr --session-id 100001127051842 --request-budget 10 --cdp-base-url http://127.0.0.1:9333
```

#### `imx discover cdp-status`
- `--cdp-base-url <url>`：检查指定 DevTools HTTP 地址；不打开携程页面，不发携程接口请求
- `--via cdp|proxy`：默认 `cdp` 检查原生 DevTools HTTP；`proxy` 检查 web-access CDP Proxy 的当前浏览器 tabs

输出包含浏览器版本、target 数量、携程管理页/详情页 target 数量，以及是否具备 detail-xhr 发现的基础条件。

#### `imx discover apply-config`
- `--report <path>`：读取 `detail-xhr --output` 生成的 JSON 报告，把其中 `recommendedConfig` 写回当前 `config.yaml`

写入前会校验报告里的 `candidateEndpoints`：必须存在与 `recommendedConfig` 相同 URL 的候选项，且该候选项包含 HTTP `200`、`looksLikeMessages: true` 和响应样本。只有手工拼出的 `recommendedConfig`、没有浏览器响应证据的报告会被拒绝。

示例：
```bash
imx discover apply-config --report .im_archive/detail_xhr_probe.json
imx run export --kind structured --via http --formats json,markdown --request-budget 30 --request-ledger .im_archive/ctrip-request-ledger.json
```

### 其他典型执行剧本

#### 剧本 A：导入他机链接表 -> 筛选某位客服 -> 导出 JSON
```bash
imx chrome start --headed
imx import links --file /data/IM_Archive_links.xlsx --confirm
imx roles select --include "张三"
imx run export --kind structured
```

#### 剧本 B：全量会话归档（HTML）
```bash
imx chrome start
imx run collect --page-size 100
imx roles select --all
imx run export --kind singlefile
```

#### 剧本 C：仅导出链接表供他机复用
```bash
imx chrome start
imx run collect --page-size 100
imx roles select --all
imx run export --kind links
```

### GUI

可直接双击 `gui.pyw`。按钮逻辑与 `imx` 命令一一对应，适合手工值守运行。

### 配置（`config.yaml`）

关键项：
- `profile_dir`：Chrome 持久登录目录
- `cdp_port`：CDP 端口（默认 `9222`）
- `chrome_path`：Chrome 可执行文件（为空自动探测）
- `extension_dir`：扩展源码目录（应包含 `manifest.json`）
- `chrome_state_file`：运行中 Chrome 元信息文件
- `ctrip_cookie_header_file` / `ctrip_auth_json`：纯 HTTP 模式读取的携程 Cookie 来源
- `ctrip_im_detail_messages_url`：纯 HTTP 详情消息候选接口
- `ctrip_im_detail_extra_body`：纯 HTTP 详情接口额外请求体字段
- `cdp_proxy_base_url`：页面上下文请求模式使用的 web-access CDP proxy，默认 `http://localhost:3456`
- `output_prefix`：导出文件前缀
- `output_dir`：默认 `/Users/tsimclaw/Downloads/Ctrip-CS-dialog`
- `concurrency`：默认并发页数（当前默认 `20`）
- `window_sec`：默认时间窗口秒数（当前默认 `10`）
