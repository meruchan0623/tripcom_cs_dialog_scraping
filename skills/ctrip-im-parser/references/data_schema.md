# Ctrip IM JSON Data Schema Reference

## Complete Field Reference

### Session (Top-Level)

```json
{
  "csName": "string",        // 客服身份标识
  "detailUrl": "string",     // 携程IM原始链接
  "exportedAt": "string",    // ISO8601导出时间
  "sessionId": "string",     // 会话唯一ID（15位数字）
  "title": "string",         // 固定值："供应商客服工作台"
  "messages": []             // 消息数组
}
```

### Message Object — Full Schema

| Field | Type | Always Present | Notes |
|-------|------|---------------|-------|
| `attachments` | array | Yes | 图片消息时含 `[{alt, src}]` |
| `csName` | string | Yes | 同顶层 |
| `detailUrl` | string | Yes | 同顶层 |
| `messageType` | string | Yes | `text` / `image` / `unknown` |
| `rawHtml` | string | Yes | 原始HTML，含订单卡/翻译/引用 |
| `senderName` | string | **No** | system消息可能为空 |
| `senderRole` | string | Yes | `buyer` / `seller` / `system` |
| `sequence` | int | Yes | 从1开始递增 |
| `sessionId` | string | Yes | 同顶层 |
| `text` | string | Yes | 纯文本，可能为空字符串 |
| `timestampText` | string | Yes | 格式：`YYYY-MM-DD HH:mm:ss` |

### Order Card HTML Structure

```
messageType = "unknown"
rawHtml contains: <div class="order-list">

Fields extracted from <div class="order-detail">:
  - 来源渠道 (channel): App / H5 / 小程序
  - 订单ID (order_id): 16位数字
  - 产品名称 (product_name): eSIM产品描述
  - 使用日期 (use_date): YYYY/MM/DD
  - 订单总额 (amount): 数字（可能含小数）
```

### Product Name Parsing Rules

产品名称字段格式化较复杂，典型结构：

```
{地区} {网络} eSIM | {运营商}覆盖 | {特性描述} | QR Code-QR code-{天数}-{网络类型}-{计费方式}-{流量}
```

可提取的维度：
- **地区**: 香港 / 中港澳 / 中国大陆 / ...
- **天数**: 1-30天范围内的具体数字
- **网络类型**: 5G / 4G / ...
- **流量方案**: 每日-XGB / 总量+每日 / 无限流量 / ...

### Translation Block in rawHtml

客户消息自动包含 Google 翻译：

```html
<div class="tran-group">
  <div class="chat-text">{translated_text}</div>
</div>
<div class="tran-con">
  <div class="tran-con-title">来自google翻译</div>
</div>
```

注意：`text` 字段存储的是**客户原始语言**，翻译内容仅在 `rawHtml` 中。

### Citation/Quote Block in rawHtml

客服回复引用客户原文：

```html
<div class="cite-card-container">
  <div class="cite-content-container">
    <div class="cite-nickname-container">{quoted_sender_name}</div>
    <div class="cite-text-content">{quoted_original_text}</div>
  </div>
</div>
<p class="chat-text">{reply_text}</p>
```

### System Messages

`senderRole=system` 的消息特征：
- `senderName` 通常为空字符串
- `text` 为空
- 内容在 `rawHtml` 中，格式如：
  ```
  <span>{timestamp}</span>温馨提示：对于海外旅游供应商...
  ```
