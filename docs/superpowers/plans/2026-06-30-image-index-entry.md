# Conversation Image Index Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为每个结构化客服会话导出一个稳定的正文图片索引入口，让后续 Agent 能直接按会话 JSON/sidecar 索引回读本地图片。

**Architecture:** 保持现有 `messages[].attachments[]` 作为权威源，不改动图片下载目录结构。新增 `im_archive_cli/image_index.py` 负责把会话 JSON 中的正文图片解析成扁平索引记录，并在 HTTP 结构化导出时写出同级 sidecar 文件 `*.image-index.json`；Agent 回读优先消费该 sidecar，其次仍可回退到原始 JSON。

**Tech Stack:** Python 3, pytest, pathlib, json, 现有 `im_archive_cli` 导出链路

---

## File Structure

- Create: `im_archive_cli/image_index.py`
  - 定义正文图片索引记录的生成、路径解析和 sidecar 写入。
- Create: `tests/test_image_index.py`
  - 覆盖索引记录筛选、路径解析优先级、failed 图片处理、sidecar 写入格式。
- Modify: `im_archive_cli/http_export.py`
  - 在会话图片下载和 JSON 落盘阶段旁路写出 `*.image-index.json`。
- Modify: `tests/test_http_export.py`
  - 验证默认 JSON-only 导出也会生成 sidecar，且 sidecar 能正确指向 `_assets/` 图片。
- Modify: `docs/AGENT_IMAGE_REFERENCE_GUIDE.md`
  - 把 `*.image-index.json` 升级为 Agent 首选入口，保留 JSON 兜底顺序。
- Modify: `README.md`
  - 补充索引文件输出规则和回读方式。

### Task 1: 建立正文图片索引模块

**Files:**
- Create: `im_archive_cli/image_index.py`
- Test: `tests/test_image_index.py`

- [ ] **Step 1: 写失败测试，定义索引记录合同**

```python
from __future__ import annotations

import json
from pathlib import Path

from im_archive_cli.image_index import build_conversation_image_index, write_conversation_image_index


def test_build_conversation_image_index_prefers_local_and_relative_paths(tmp_path: Path) -> None:
    conversation_dir = tmp_path / "conversation"
    conversation_dir.mkdir()
    asset_dir = conversation_dir / "IMChatlogExport_20260616090000_s1_Alice_assets"
    asset_dir.mkdir()
    image_path = asset_dir / "seq0001_abc123.jpg"
    image_path.write_bytes(b"fake")

    conversation = {
        "sessionId": "s1",
        "messages": [
            {
                "sequence": 1,
                "messageType": "image",
                "attachments": [
                    {
                        "source": "messageBody",
                        "src": "https://cdn.example.com/a.jpg",
                        "localPath": str(image_path.resolve()),
                        "relativePath": "IMChatlogExport_20260616090000_s1_Alice_assets/seq0001_abc123.jpg",
                        "downloadStatus": "downloaded",
                    }
                ],
            }
        ],
    }

    records = build_conversation_image_index(
        conversation,
        json_path=conversation_dir / "IMChatlogExport_20260616090000_s1_Alice.json",
    )

    assert records == [
        {
            "sessionId": "s1",
            "sequence": 1,
            "messageType": "image",
            "source": "messageBody",
            "downloadStatus": "downloaded",
            "src": "https://cdn.example.com/a.jpg",
            "localPath": str(image_path.resolve()),
            "relativePath": "IMChatlogExport_20260616090000_s1_Alice_assets/seq0001_abc123.jpg",
            "resolvedPath": str(image_path.resolve()),
        }
    ]


def test_build_conversation_image_index_skips_non_message_body_attachments(tmp_path: Path) -> None:
    json_path = tmp_path / "conversation.json"
    conversation = {
        "sessionId": "s2",
        "messages": [
            {
                "sequence": 2,
                "messageType": "image",
                "attachments": [
                    {"source": "card", "src": "https://cdn.example.com/card.jpg"},
                    {"source": "messageBody", "thumbSrc": "https://cdn.example.com/thumb.jpg"},
                ],
            }
        ],
    }

    assert build_conversation_image_index(conversation, json_path=json_path) == []


def test_write_conversation_image_index_writes_sidecar_json(tmp_path: Path) -> None:
    json_path = tmp_path / "IMChatlogExport_20260616090000_s1_Alice.json"
    json_path.write_text("{}", encoding="utf-8")
    conversation = {
        "sessionId": "s1",
        "messages": [
            {
                "sequence": 3,
                "messageType": "image",
                "attachments": [
                    {
                        "source": "messageBody",
                        "src": "https://cdn.example.com/missing.jpg",
                        "downloadStatus": "failed",
                    }
                ],
            }
        ],
    }

    index_path = write_conversation_image_index(conversation, json_path)

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert index_path.name == "IMChatlogExport_20260616090000_s1_Alice.image-index.json"
    assert payload["sessionId"] == "s1"
    assert payload["images"][0]["downloadStatus"] == "failed"
    assert payload["images"][0]["resolvedPath"] == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_image_index.py -q`

Expected: FAIL，报 `ModuleNotFoundError: No module named 'im_archive_cli.image_index'` 或 `cannot import name 'build_conversation_image_index'`

- [ ] **Step 3: 写最小实现**

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _resolve_attachment_path(json_path: Path, attachment: dict[str, Any]) -> str:
    local_path = str(attachment.get("localPath") or "").strip()
    if local_path:
        return str(Path(local_path).expanduser().resolve())

    relative_path = str(attachment.get("relativePath") or "").strip()
    if relative_path:
        candidate = json_path.parent / relative_path
        if candidate.exists():
            return str(candidate.resolve())

    return ""


def build_conversation_image_index(conversation: dict[str, Any], json_path: Path) -> list[dict[str, Any]]:
    session_id = str(conversation.get("sessionId") or "")
    records: list[dict[str, Any]] = []
    messages = conversation.get("messages") or []
    if not isinstance(messages, list):
        return records

    for message in messages:
        if not isinstance(message, dict):
            continue
        attachments = message.get("attachments") or []
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if attachment.get("source") != "messageBody":
                continue
            if not attachment.get("src"):
                continue
            records.append(
                {
                    "sessionId": session_id,
                    "sequence": int(message.get("sequence") or 0),
                    "messageType": str(message.get("messageType") or ""),
                    "source": "messageBody",
                    "downloadStatus": str(attachment.get("downloadStatus") or ""),
                    "src": str(attachment.get("src") or ""),
                    "localPath": str(attachment.get("localPath") or ""),
                    "relativePath": str(attachment.get("relativePath") or ""),
                    "resolvedPath": _resolve_attachment_path(json_path, attachment),
                }
            )
    return records


def write_conversation_image_index(conversation: dict[str, Any], json_path: Path) -> Path:
    payload = {
        "sessionId": str(conversation.get("sessionId") or ""),
        "jsonPath": str(json_path.resolve()),
        "images": build_conversation_image_index(conversation, json_path),
    }
    index_path = json_path.with_name(f"{json_path.stem}.image-index.json")
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_path
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_image_index.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add im_archive_cli/image_index.py tests/test_image_index.py
git commit -m "feat: add conversation image index builder"
```

### Task 2: 接入结构化导出 sidecar

**Files:**
- Modify: `im_archive_cli/http_export.py`
- Modify: `tests/test_http_export.py`
- Test: `tests/test_http_export.py`

- [ ] **Step 1: 先写导出集成失败测试**

```python
def test_export_structured_via_http_writes_image_index_sidecar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig(
        output_dir=str(tmp_path / "out"),
        failures_file=str(tmp_path / "failures.jsonl"),
        window_sec=0,
        download_images=True,
        image_max_workers=1,
        image_request_interval_sec=0,
    )
    session = SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 09:00:00").normalized()

    class ImageMessageClient:
        def fetch_conversation(self, session: SessionRecord) -> dict:
            return {
                "sessionId": session.session_id,
                "csName": session.cs_name,
                "detailUrl": session.detail_url,
                "messages": [
                    {
                        "sequence": 1,
                        "timestampText": "2026-06-16 09:00:00",
                        "senderRole": "buyer",
                        "senderName": "Guest",
                        "messageType": "image",
                        "text": "",
                        "rawHtml": "",
                        "attachments": [
                            {
                                "src": "https://example.com/placeholder.png",
                                "thumbSrc": "https://example.com/placeholder-thumb.png",
                                "source": "messageBody",
                            }
                        ],
                    }
                ],
            }

    def fake_download_conversation_images(data: dict, conversation_dir: str | Path, base_name: str, _config: AppConfig, _log) -> None:
        asset_dir = Path(conversation_dir) / f"{base_name}_assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / "seq0001_test.jpg"
        asset_path.write_bytes(b"fake")
        attachment = data["messages"][0]["attachments"][0]
        attachment["localPath"] = str(asset_path.resolve())
        attachment["relativePath"] = f"{base_name}_assets/seq0001_test.jpg"
        attachment["downloadStatus"] = "downloaded"

    monkeypatch.setattr(
        "im_archive_cli.http_export.download_conversation_images",
        fake_download_conversation_images,
    )

    success, failed = export_structured_via_http(ImageMessageClient(), cfg, [session], ["json"], lambda _msg: None)

    assert (success, failed) == (1, 0)
    index_files = list((tmp_path / "out").rglob("*.image-index.json"))
    assert len(index_files) == 1
    payload = json.loads(index_files[0].read_text(encoding="utf-8"))
    assert payload["sessionId"] == "s1"
    assert payload["images"][0]["relativePath"].endswith("_assets/seq0001_test.jpg")
    assert payload["images"][0]["resolvedPath"].endswith("_assets/seq0001_test.jpg")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_http_export.py::test_export_structured_via_http_writes_image_index_sidecar -q`

Expected: FAIL，断言 `len(index_files) == 1` 失败，因为当前尚未写 sidecar

- [ ] **Step 3: 在导出链路接入 sidecar 写入**

```python
from .image_index import write_conversation_image_index


def _fetch_and_write_session(
    client: CtripImDetailHttpClient,
    config: AppConfig,
    item: tuple[int, SessionRecord, Path, Path],
    formats: list[str],
    log: Callable[[str], None],
    total: int,
) -> None:
    i, sess, json_path, md_path = item
    log(f"[{i}/{total}] 结构化(HTTP): {sess.session_id}")
    worker_client: CtripImDetailHttpClient = _build_session_client(client, log) if isinstance(client, CtripImDetailHttpClient) else client
    data = worker_client.fetch_conversation(sess)
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        raise RuntimeError("提取失败：返回数据缺少 messages")
    if not messages:
        raise RuntimeError("提取失败：messages 为空")
    download_conversation_images(data, json_path.parent, json_path.stem, config, log)
    if "json" in formats:
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        write_conversation_image_index(data, json_path)
    if "markdown" in formats:
        md_path.write_text(create_markdown(sess, messages), encoding="utf-8")
```

- [ ] **Step 4: 跑目标测试和回归测试**

Run: `python3 -m pytest tests/test_http_export.py::test_export_structured_via_http_writes_image_index_sidecar tests/test_http_export.py::test_cmd_run_export_defaults_to_json_only -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add im_archive_cli/http_export.py tests/test_http_export.py
git commit -m "feat: write image index sidecar during structured export"
```

### Task 3: 文档化 Agent 回读入口

**Files:**
- Modify: `docs/AGENT_IMAGE_REFERENCE_GUIDE.md`
- Modify: `README.md`

- [ ] **Step 1: 先更新 Agent 图片参考手册**

```markdown
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
```

- [ ] **Step 2: 在 README 补充输出规则**

```markdown
### 正文图片索引 sidecar

每次 `imx run export --kind structured` 在写出会话 JSON 时，还会额外写出同级索引文件：

```text
IMChatlogExport_20260616090000_s1_Alice.json
IMChatlogExport_20260616090000_s1_Alice.image-index.json
IMChatlogExport_20260616090000_s1_Alice_assets/
```

用途：
- 给后续 Agent/脚本提供稳定图片入口
- 避免直接扫描 `_assets/` 目录时丢失 `sessionId`、`sequence`、`downloadStatus`
- 避免把 Markdown 当成权威数据源
```

- [ ] **Step 3: 手工检查文档关键字**

Run: `rg -n "image-index|Agent 首选入口|正文图片索引 sidecar" README.md docs/AGENT_IMAGE_REFERENCE_GUIDE.md`

Expected: 输出同时命中 `README.md` 和 `docs/AGENT_IMAGE_REFERENCE_GUIDE.md`

- [ ] **Step 4: 跑完整相关测试**

Run: `python3 -m pytest tests/test_image_index.py tests/test_http_export.py tests/test_media.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add README.md docs/AGENT_IMAGE_REFERENCE_GUIDE.md
git commit -m "docs: document image index entry for agents"
```

## Self-Review

### Spec coverage

- “实现图片索引入口” 已覆盖在 Task 1 和 Task 2：新增索引模块 + 导出 sidecar。
- “让 Agent 回读时能正确索引图片” 已覆盖在 Task 1 和 Task 3：`resolvedPath` 合同 + 文档中的首选读取顺序。
- “保持现有路径结构不被破坏” 已通过架构和 Task 2 保证：仍沿用现有 `<base_name>_assets/`。

### Placeholder scan

- 计划中没有 `TODO`、`TBD`、`implement later`。
- 每个代码步骤都给出了实际代码或文档内容。
- 每个验证步骤都给出了具体命令和期望结果。

### Type consistency

- 统一使用 `build_conversation_image_index(...)` 和 `write_conversation_image_index(...)` 两个名字。
- Sidecar 文件名统一为 `f"{json_path.stem}.image-index.json"`。
- 记录字段统一为 `sessionId`、`sequence`、`messageType`、`source`、`downloadStatus`、`src`、`localPath`、`relativePath`、`resolvedPath`。
