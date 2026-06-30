# Ctrip IM JSON Data Schema Reference

## 存储布局

```text
<output_dir>/<yyyyMMdd>/<客服名>/
  IMChatlogExport_<yyyyMMddHHmmss>_<sessionId>_<客服名>.json
  IMChatlogExport_<yyyyMMddHHmmss>_<sessionId>_<客服名>.image-index.json
  IMChatlogExport_<yyyyMMddHHmmss>_<sessionId>_<客服名>.md
  IMChatlogExport_<yyyyMMddHHmmss>_<sessionId>_<客服名>_assets/
```

`IMChatlogExport_*.json` 是主会话文件。`*.image-index.json` 是正文图片索引 sidecar，不是会话文件。

## Session 顶层字段

| Field | Type | Notes |
| --- | --- | --- |
| `sessionId` | string | 会话唯一 ID |
| `csName` | string | 客服身份标识 |
| `detailUrl` | string | 携程 IM 详情链接 |
| `exportedAt` | string | ISO8601 导出时间 |
| `title` | string | 通常为 `供应商客服工作台` |
| `messages` | array | 消息数组，按 `sequence` 表示顺序 |

## Message 字段

| Field | Type | Notes |
| --- | --- | --- |
| `sequence` | int | 从 1 开始递增 |
| `timestampText` | string | `YYYY-MM-DD HH:mm:ss` |
| `senderRole` | string | `buyer` / `seller` / `system` |
| `senderName` | string | system 消息可能为空 |
| `messageType` | string | `text` / `image` / `unknown` |
| `text` | string | 纯文本。图片通常为 `[图片]` |
| `rawHtml` | string | 原始 HTML，可能含订单卡、翻译、引用 |
| `attachments` | array | 图片或其他附件。正文图片见下节 |

## 正文图片附件

正文图片必须来自：

```text
messages[].attachments[] where source == "messageBody" and src exists
```

附件字段：

| Field | Type | Notes |
| --- | --- | --- |
| `source` | string | 正文图片固定为 `messageBody` |
| `src` | string | 原始远端图片 URL |
| `thumbSrc` | string | 预览兜底，不能作为主入口 |
| `localPath` | string | 本地绝对路径，存在时优先使用 |
| `relativePath` | string | 相对会话 JSON 所在目录的路径 |
| `downloadStatus` | string | `downloaded` / `failed` / 空 |

路径读取顺序：

1. `localPath`
2. `json_file.parent / relativePath`
3. `src` 远端 URL

`downloadStatus == "failed"` 是图片级失败，不应直接判定会话导出失败。报告时记录 `sessionId + sequence + src`。

## Image Index Sidecar

`*.image-index.json` 结构：

```json
{
  "sessionId": "s1",
  "jsonPath": "/absolute/path/to/IMChatlogExport_20260616090000_s1_Alice.json",
  "images": [
    {
      "sessionId": "s1",
      "sequence": 1,
      "messageType": "image",
      "source": "messageBody",
      "downloadStatus": "downloaded",
      "src": "https://cdn.example.com/a.jpg",
      "localPath": "/absolute/path/to/file.jpg",
      "relativePath": "IMChatlogExport_..._assets/seq0001_a.jpg",
      "resolvedPath": "/absolute/path/to/file.jpg"
    }
  ]
}
```

用途：让 Agent 快速定位正文图片。它不包含完整聊天文本，不能替代会话 JSON。

## 非正文图片

以下内容不要当作客人发送的正文图片：

- 头像，例如 `infos[].avatar`
- 商品卡片图
- 订单卡片图
- 任何 `source != "messageBody"` 的附件
- 只有 `thumbSrc` 但没有 `src` 的附件

## Order Card HTML

订单卡通常在 `rawHtml` 中：

```html
<div class="order-detail">
  <dd>来源渠道：</span><span>App</span></dd>
  <dd>订单ID：</span><span>1578946023985969</span></dd>
  <dd>产品名称：</span><span>香港 5G eSIM | ...</span></dd>
  <dd>使用日期：</span><span>2026/03/11</span></dd>
  <dd>订单总额：</span><span>599.97</span></dd>
</div>
```

可提取字段：

- `channel`
- `order_id`
- `product_name`
- `use_date`
- `amount`

## Translation Block

客户消息可能在 `rawHtml` 中附带 Google 翻译：

```html
<div class="tran-group">
  <div class="chat-text">{translated_text}</div>
</div>
<div class="tran-con">
  <div class="tran-con-title">来自google翻译</div>
</div>
```

`text` 字段存储客户原始语言。翻译内容仅在 `rawHtml` 中。

## Citation Block

客服回复可能引用客户原文：

```html
<div class="cite-card-container">
  <div class="cite-content-container">
    <div class="cite-nickname-container">{quoted_sender_name}</div>
    <div class="cite-text-content">{quoted_original_text}</div>
  </div>
</div>
<p class="chat-text">{reply_text}</p>
```

分析客服回复时，不要把引用块误判成客服新写的正文。

## System Messages

`senderRole == "system"` 的常见特征：

- `senderName` 为空
- `text` 可能为空
- 主要内容在 `rawHtml`

响应时延统计通常应排除 system 消息。
