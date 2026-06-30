---
name: ctrip-im-parser
description: |
  Use when analyzing Trip.com/Ctrip IMChatlogExport JSON conversations,客服聊天记录解析、会话质量评估、客户问题分类、订单卡提取、响应时延统计、正文图片引用回读，或任何涉及 IMChatlogExport_*.json / *.image-index.json 的处理任务。
---

# Ctrip IM Parser

## 核心原则

只做数据读取和结构化，不在脚本里写业务判断。业务分类、质检规则、话术合规判断由调用 Agent 在 prompt 层完成。

当前存储范式以会话 JSON 为主入口，图片 sidecar 为辅助入口：

```text
<output_dir>/<yyyyMMdd>/<客服名>/
  IMChatlogExport_<yyyyMMddHHmmss>_<sessionId>_<客服名>.json
  IMChatlogExport_<yyyyMMddHHmmss>_<sessionId>_<客服名>.image-index.json
  IMChatlogExport_<yyyyMMddHHmmss>_<sessionId>_<客服名>.md
  IMChatlogExport_<yyyyMMddHHmmss>_<sessionId>_<客服名>_assets/
```

## 读取顺序

1. 读取 `IMChatlogExport_*.json` 作为会话、消息、角色、订单卡和正文文本的权威来源。
2. 需要正文图片时，优先读取同名 `*.image-index.json` 定位图片；也可从会话 JSON 的 `messages[].attachments[]` 回退。
3. Markdown 仅用于展示或兜底，不作为程序分析主入口。
4. 不要直接扫描 `_assets/` 目录推断图片语义；目录文件缺少 `sessionId`、`sequence`、`source`、`downloadStatus`。
5. 扫描 JSON 时跳过 `*.image-index.json`，它不是会话文件。

正文图片只认 `messages[].attachments[]` 中 `source == "messageBody"` 且存在 `src` 的附件。头像、商品卡片、订单卡片和其他非 `messageBody` 附件不能算作客人发送的正文图片。

## 会话 JSON 结构

每个 `IMChatlogExport_*.json` 是一个会话：

```json
{
  "sessionId": "100001083736038",
  "csName": "门票活动旅游管家Alice",
  "detailUrl": "https://imvendor.ctrip.com/queryMessages?...",
  "exportedAt": "2026-06-16T04:19:41.006Z",
  "title": "供应商客服工作台",
  "messages": [
    {
      "sequence": 1,
      "timestampText": "2026-06-16 09:00:00",
      "senderRole": "buyer",
      "senderName": "_tisg******tbgrw8",
      "messageType": "text",
      "text": "纯文本正文",
      "rawHtml": "原始 HTML，可能含订单卡、翻译、引用",
      "attachments": []
    }
  ]
}
```

图片消息附件示例：

```json
{
  "sequence": 3,
  "messageType": "image",
  "text": "[图片]",
  "attachments": [
    {
      "source": "messageBody",
      "src": "https://cdn.example.com/a.jpg",
      "thumbSrc": "https://cdn.example.com/thumb.jpg",
      "localPath": "/absolute/path/IMChatlogExport_..._assets/seq0003_a.jpg",
      "relativePath": "IMChatlogExport_..._assets/seq0003_a.jpg",
      "downloadStatus": "downloaded"
    }
  ]
}
```

## 脚本入口

```bash
python skills/ctrip-im-parser/scripts/scan_im.py <导出目录> [参数...]
```

常用参数：

| 参数 | 用途 |
| --- | --- |
| `-o FILE` | 输出结构化 JSON |
| `--role buyer/seller/system` | 按发言角色过滤 |
| `-k TEXT` / `--keyword TEXT` | 在正文和订单卡字段中搜索 |
| `--after YYYY-MM-DD` | 只保留该日及之后消息 |
| `--before YYYY-MM-DD` | 只保留该日及之前消息 |
| `--extract orders` | 从 `rawHtml` 提取订单卡 |
| `--ctx N` | 输出匹配消息前后 N 条上下文 |
| `--seq-diff` | 计算相邻消息时间差 |

脚本会递归读取会话 JSON，并跳过 `*.image-index.json`。输出消息会包含：

```json
{
  "sender_role": "buyer",
  "msg_type": "image",
  "text": "[图片]",
  "sequence": 3,
  "timestamp": "2026-06-16 09:01:00",
  "has_attachments": true,
  "inline_images": [
    {
      "source": "messageBody",
      "src": "https://cdn.example.com/a.jpg",
      "local_path": "",
      "relative_path": "IMChatlogExport_..._assets/seq0003_a.jpg",
      "download_status": "downloaded"
    }
  ],
  "order_card": null
}
```

会话级输出包含 `inline_image_count` 和 `failed_inline_image_count`，用于快速识别图片覆盖和失败项。

## 分析范式

### 关键词上下文

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  -k refund \
  --ctx 3 \
  -o .im_archive/analysis_refund_ctx_20260616.json
```

Agent 再读取 `context_matches` 做会话级判断，不要只看命中的单句。

### 只看买家消息

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  --role buyer \
  -o .im_archive/analysis_buyer_20260616.json
```

### 订单卡提取

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  --extract orders \
  -o .im_archive/analysis_orders_20260616.json
```

订单分析优先读 `rawHtml` 中的订单卡，不要只靠正文猜订单号、产品名或金额。

### 响应时延

```bash
python skills/ctrip-im-parser/scripts/scan_im.py \
  .im_archive/output/20260616 \
  --seq-diff \
  -o .im_archive/analysis_gaps_20260616.json
```

统计响应效率时只统计 `buyer -> seller` 的相邻消息间隔，不要把 system 消息混入平均值。

## 常见误读

- 把 `*.image-index.json` 当会话 JSON：错误。它只保存图片索引。
- 扫 `_assets/` 判断“客人发了哪些图”：错误。必须回到 `sessionId + sequence + source`。
- 把 `thumbSrc` 当正文图片主入口：错误。它只做预览兜底。
- 把商品卡/订单卡/头像图片算作正文图片：错误。正文图片必须是 `source == "messageBody"`。
- 只读 Markdown：不推荐。Markdown 可能不存在，且字段信息少于 JSON。

## 字段参考

详见 `skills/ctrip-im-parser/references/data_schema.md`。
