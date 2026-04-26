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
5. 打开 Trip.com 供应商平台 IM 会话详情页面
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
