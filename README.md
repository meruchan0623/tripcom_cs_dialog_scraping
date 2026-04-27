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

- 保存位置：Chrome 默认下载目录
- 文件命名：`{前缀}_{客服名}_{sessionId}_{序号}.html`
- 默认前缀：`IM_Archive`

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

本仓库新增 `imx` 命令：Python 通过 CDP 驱动 Chrome 扩展执行任务，不再直接驱动业务页面 DOM。
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

插件默认节流速度已调整为：
- `时间窗口内页数`：`20`
- `时间窗口(秒)`：`30`

对应含义：每 30 秒允许打开 20 页详情页（SingleFile/结构化导出都按该节流控制）。

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

# 3) 抓取会话
imx run collect --page-size 100

# 4) 查看客服筛选列表
imx roles list

# 5a) 选择全部客服
imx roles select --all

# 5b) 或只选择某位/多位客服
imx roles select --include "张三,李四"

# 6) 导出结构化对话（JSON + Markdown）
imx run export --kind structured
```

如果你只要 JSON，可在插件侧把格式仅选 `JSON` 后再执行第 6 步。

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
- `--debug`：以 CDP 调试模式启动（需要执行 `run/roles/import/state` 自动化命令时使用）
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

#### `imx run collect`
- `--page-size <int>`：采集分页大小（建议 `100`）
- `--max-pages <int>`：当前仅记录提示，不直接覆盖插件常量 `MAX_PAGES`

示例：
```bash
imx run collect --page-size 100
imx run collect --page-size 50 --max-pages 30
```

#### `imx roles list`
- 无额外参数
- 输出当前可选客服与已选标记

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

示例：
```bash
imx run export --kind structured
imx run export --kind singlefile
imx run export --kind links
```

#### `imx import links`
- `--file <xlsx>`：要导入的链接表文件
- `--preview`：仅预览，不执行导入
- `--confirm`：直接确认导入（跳过交互确认）

示例：
```bash
imx import links --file /data/IM_Archive_links.xlsx --preview
imx import links --file /data/IM_Archive_links.xlsx --confirm
```

#### `imx state watch`
- `--interval-sec <float>`：轮询间隔，默认 `1.0`
- `--once`：只打印一次当前状态

示例：
```bash
imx state watch --once
imx state watch --interval-sec 2
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
- `output_prefix`：导出文件前缀
- `concurrency`：默认并发页数（建议 `20`）
- `window_sec`：默认时间窗口秒数（建议 `30`）
