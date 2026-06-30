# Agent 正文图片参考手册（正文图片优先）

本文档定义给后续 Agent 的“正文图片”处理边界，避免将头像/卡片图误判为客人发送图片，并提供可直接复用的读取示例。

## 文件布局约定

导出文件要求 JSON/Markdown 与图片目录同级，图片目录命名为 `<base_name>_assets/`，示例：

```text
IMChatlogExport_20260629_120000_12345.json
IMChatlogExport_20260629_120000_12345.image-index.json
IMChatlogExport_20260629_120000_12345.md
IMChatlogExport_20260629_120000_assets/
IMChatlogExport_20260629_120000.html
IMChatlogExport_20260629_120000_images/
```

JSON/Markdown 中的引用与落盘均优先指向该会话级 assets 目录。

## Agent 首选入口

后续 Agent 回读正文图片时，首选读取与会话 JSON 同级的 `*.image-index.json`，而不是直接扫描 `_assets/` 目录或优先解析 Markdown。

索引文件结构：

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
      "localPath": "/absolute/path/to/IMChatlogExport_20260616090000_s1_Alice_assets/seq0001_abc123.jpg",
      "relativePath": "IMChatlogExport_20260616090000_s1_Alice_assets/seq0001_abc123.jpg",
      "resolvedPath": "/absolute/path/to/IMChatlogExport_20260616090000_s1_Alice_assets/seq0001_abc123.jpg"
    }
  ]
}
```

读取顺序：

1. `*.image-index.json`
2. 原始会话 `*.json`
3. `*.md` 中的 `![图片](...)` 仅作展示面兜底

## JSON 正文图片读取规则

1. 只认 `messages[].attachments[]` 且 `source == "messageBody"` 的正文图片。
2. 解析顺序固定为：
   1. `localPath` 存在时直接使用；
   2. 否则尝试 `json_file.parent / relativePath`；
   3. 都没有再尝试 `src` 远程 URL。
3. `thumbSrc` 只做图片预览兜底，不能作为正文正文图的主入口。
4. 明确忽略：
   - `infos[].avatar`（头像）
   - 商品卡片图（通常在非 `messageBody` 的附件来源中出现）
   - 订单卡片图（通常在非 `messageBody` 的附件来源中出现）

### `downloadStatus=failed` 处理

- `downloadStatus == "failed"` 时记为图片级失败项，不应直接让会话判定失败。
- 会话仍需继续产出正文摘要；仅将该图片输出为“缺失/下载失败”。
- 建议记录 `sessionId + message sequence + 原始 src`，便于后续人工复核。

### Python 示例：`iter_message_images(json_path: Path)`

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, Tuple, Union


def iter_message_images(json_path: Path) -> Iterator[Tuple[dict, Union[Path, str]]]:
    """遍历正文图片定位结果。

    返回 (message, path_or_url)
    - path_or_url 为 Path：本地可读路径
    - path_or_url 为 str：仅有远端 src（未本地化）
    """
    data = json.loads(json_path.read_text(encoding="utf-8"))
    messages = data.get("messages", [])
    base_dir = Path(json_path).parent

    for msg in messages:
        for attachment in msg.get("attachments", []) or []:
            if attachment.get("source") != "messageBody":
                continue

            local_path = attachment.get("localPath")
            if local_path:
                yield msg, Path(local_path)
                continue

            relative_path = attachment.get("relativePath")
            if relative_path:
                p = base_dir / relative_path
                if p.exists():
                    yield msg, p
                    continue

            src = attachment.get("src")
            if src:
                # 仅有远端 URL 时返回原始 URL，不构造本地 assets 路径
                yield msg, src
                continue

            # 无本地路径且无 src 时跳过
```

> 说明：示例中的 `path_or_url` 仅表示定位结果；项目内若已标准化到 `<base_name>_assets/`，请按该结构重写最终落盘路径映射。

## Markdown 正文图片读取规则

1. 解析 `![图片](...)` 语法。
2. 相对路径按 `md_file.parent / path` 解析。
3. 只要是 Markdown 中的 inline 语法，按文档内语义保留顺序记录；与 `![alt](url)` 中的 `url` 一致。

### Python 示例：`iter_markdown_images(md_path: Path)`

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator


IMG_RE = re.compile(r"!\[[^\]]*\]\(([^)\n]+)\)")


def iter_markdown_images(md_path: Path) -> Iterator[tuple[int, str, Path]]:
    text = md_path.read_text(encoding="utf-8")
    for i, m in enumerate(IMG_RE.finditer(text), start=1):
        target = m.group(1).strip()
        p = Path(target)
        if not p.is_absolute():
            p = md_path.parent / p
        yield i, target, p
```

## Agent 输出要求

在产出图片引用时，需同时写明：

- `sessionId`
- `sequence`（对应消息序号）
- 图片本地路径（按最终落盘路径）

不要将头像/商品卡片/订单卡片图描述为“客人发送图片”或“正文图片”；必须归类为 `非正文图片`/`上下文图片`并说明忽略原因。
