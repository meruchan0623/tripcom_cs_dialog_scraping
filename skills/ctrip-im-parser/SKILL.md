---
name: ctrip-im-parser
description: |
  解析携程IM客服对话JSON记录。当用户需要分析携程/ Trip平台导出的IM聊天记录、
  客服对话质量评估、客户问题分类统计、会话时间分析、订单信息提取、
  客服响应效率分析、或任何涉及IM_Archive_*.json文件的处理任务时触发此skill。
  触发词：携程IM、IM Archive、客服对话、聊天记录解析、csName、senderRole、
  携程导出对话、vbk_json、商家顾问对话、旅游管家对话、消息序列分析
---

# Ctrip IM Parser — 数据提取引擎

## 概述

从携程/Trip平台导出的 **IMChatlogExport_*.json**（兼容历史 IM_Archive_*.json）文件中批量提取结构化数据。

> **设计原则：纯数据提取，零业务逻辑。**
> 
> 脚本只负责"把数据取出来并结构化"，所有分析判断逻辑由调用者（AI Agent）在 prompt 层动态决定。

## 数据格式

### 文件命名
```
IMChatlogExport_{会话创建时间yyyyMMddHHmmss}_{sessionId}_{客服名}.json
```

### JSON 结构（每个文件 = 1个会话）
```json
{
  "csName": "vbk_2547307/门票活动旅游管家Jeffery Zhu",
  "detailUrl": "https://imvendor.ctrip.com/queryMessages?...",
  "exportedAt": "2026-04-27T04:19:41.006Z",
  "sessionId": "100001083736038",
  "title": "供应商客服工作台",
  "messages": [
    {
      "senderRole":    "buyer | seller | system",
      "senderName":   "_tisg******tbgrw8 (脱敏)",
      "messageType":  "text | image | unknown",
      "text":         "纯文本正文",
      "rawHtml":      "原始HTML（含订单卡/翻译/引用）",
      "sequence":     1,
      "timestampText":"2026-04-24 08:09:47",
      "attachments":  [{"src": "...", "alt": "..."}]
    }
  ]
}
```

### 嵌入 rawHtml 的订单卡
```html
<div class="order-detail">
  <dd>来源渠道：</span><span>App</span></dd>
  <dd>订单ID：</span><span>1578946023985969</span></dd>
  <dd>产品名称：</span><span>香港 5G eSIM | ...</span></dd>
  <dd>使用日期：</span><span>2026/03/11</span></dd>
  <dd>订单总额：</span><span>599.97</span></dd>
</div>
```

## 脚本使用

### 位置
```
{SKILL_DIR}/scripts/scan_im.py
```

运行方式：
```bash
python {SKILL_DIR}/scripts/scan_im.py <目录路径> [参数...]
```

### 核心参数

| 参数 | 说明 |
|------|------|
| `<directory>` | 导出根目录路径（必填）。支持递归扫描，建议传入 `IMChatlogExport/` 根目录 |
| `-o FILE` / `--output FILE` | 输出为结构化 JSON 文件（否则输出摘要到 stdout） |
| `--role ROLE` | 按 senderRole 过滤: `buyer` / `seller` / `system` |
| `--keyword TEXT` / `-k TEXT` | 在消息文本 + 订单卡中做大小写不敏感子串搜索 |
| `--after DATE` | 只保留 >= YYYY-MM-DD 的消息 |
| `--before DATE` | 只保留 <= YYYY-MM-DD 的消息 |
| `--extract orders` | 从 rawHtml 中提取嵌入的订单卡信息 |
| `--ctx N` | 为每条匹配消息生成前后各 N 条消息的上下文窗口 |
| `--seq-diff` | 计算相邻消息之间的时间间隔 |

### 输出格式（-o 模式）

```json
{
  "statistics": {
    "sessions": 462,
    "total_messages": 6726,
    "by_role": {"buyer": 2340, "seller": 3320, "system": 1066},
    "by_type": {"text": 5100, "image": 200, "unknown": 1426},
    "time_earliest": "2026-04-07 10:16:33",
    "time_latest": "2026-04-25 19:40:43",
    "avg_message_length_chars": 85.3,
    "language_distribution": {"en": 2100, "zh": 1500, "th": 800, ...},
    "sessions_with_order_cards": 380,
    "total_order_amount": 125430.50
  },
  "sessions": [ ... ]  // 完整会话数据数组
}
```

### 特殊输出模式

#### --ctx N（上下文窗口模式）
每条匹配消息附带前后 N 条邻居消息：
```json
{
  "context_matches": [
    {
      "session_id": "100001083736038",
      "matched_index": 5,
      "context_before": [...N条前文...],
      "target_message": { "sender_role": "seller", "text": "...", ... },
      "context_after": [...N条后文...]
    }
  ]
}
```

#### --seq-diff（时间间隔模式）
计算每对相邻消息的时间差：
```json
{
  "timing_gaps": [
    {
      "session_id": "100001083736038",
      "gaps": [
        {"from_seq":1, "to_seq":2, "from_role":"buyer", "to_role":"system", "gap_seconds":2},
        {"from_seq":4, "to_seq":5, "from_role":"buyer", "to_role":"seller", "gap_seconds":109},
        ...
      ]
    }
  ]
}
```

## 典型分析工作流（AI调用示例）

脚本本身不做业务判断。以下是 AI 如何组合基础能力完成各种分析的思路：

### 示例A：检测"感谢后仍回复"
```bash
# Step 1: 提取所有包含感谢关键词的客户消息 + 上下文
python scan_im.py ./logs -k thanks --ctx 3 -o step1.json

# Step 2: AI 在 prompt 中定义判断逻辑，分析 step1.json 中的 context_matches
# → 判断 context_after 中是否包含 seller 的非结束语业务消息
```

### 示例B：统计退款类咨询占比
```bash
# Step 1: 提取含 refund/退款 关键词的消息
python scan_im.py ./logs -k refund -o refunds.json

# Step 2: AI 统计 refunds.json 中的 session 分布、角色分布
```

### 示例C：客服响应效率分析
```bash
# Step 1: 获取全部时间间隔数据
python scan_im.py ./logs --seq-diff -o gaps.json

# Step 2: AI 过滤 buyer→seller 方向的 gap，统计分布、找出异常值
```

### 示例D：订单信息汇总
```bash
# Step 1: 提取所有订单卡
python scan_im.py ./logs --extract orders -o orders.json

# Step 2: AI 按产品类型、金额区间分组统计
```

## 字段参考

详见 `{SKILL_DIR}/references/data_schema.md`


## 新目录与命名约定（2026-04）

### 目录层级
```
IMChatlogExport/{yyyyMMdd}/{客服名}/IMChatlogExport_{yyyyMMddHHmmss}_{sessionId}_{客服名}.json
```

### 解析策略（供读取产物时使用）
1. **优先按标准模式解析**：
   - 文件名正则：`^IMChatlogExport_(\d{14})_([^_]+)_(.+)\.json$`
   - 路径末尾两级：`{yyyyMMdd}/{客服名}/`
2. **若文件名不匹配**：
   - 回退读取 JSON 字段：`sessionId`、`csName`、`createTime`、`exportedAt`
   - `createTime` 缺失时再用 `exportedAt` 或文件 mtime 推断
3. **若发现新模式**：
   - 记录新模式样例（至少3个）
   - 提取稳定分隔符与字段位置（前缀、时间戳、sessionId、客服名）
   - 在脚本中新增并行匹配规则，保留对旧模式兼容

### 示例
- `IMChatlogExport_20260428113045_100001089130749_门票活动旅游管家Nay.json`
  - create_time: `2026-04-28 11:30:45`
  - session_id: `100001089130749`
  - cs_name: `门票活动旅游管家Nay`
- `IMChatlogExport_20260427101403_200001090447125_vbk_2560483_门票活动旅游管家Susie.json`
  - create_time: `2026-04-27 10:14:03`
  - session_id: `200001090447125`
  - cs_name: `vbk_2560483_门票活动旅游管家Susie`
- 路径：`IMChatlogExport/20260427/门票活动旅游管家Sara/IMChatlogExport_20260427180122_600001087328645_门票活动旅游管家Sara.json`
  - date_dir: `20260427`（可用于快速分区）
