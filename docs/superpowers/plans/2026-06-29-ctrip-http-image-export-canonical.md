# Ctrip HTTP Image Export Canonical Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已验证的携程客服 HTTP 请求合同基础上，完成正文图片识别、下载、本地引用、Agent 读取指南和端到端验收，使 JSON/Markdown 客服对话记录可离线查看图片，并剥离旧 CDP/Selenium DOM 聊天记录抓取路径。

**Architecture:** 先把当前请求合同、分页/控速默认值和导出基线固化为可回归核验面，再在详情消息归一化和 HTTP 结构化导出层加入图片能力。正文图片解析集中在 `im_archive_cli/media.py`，下载集中在 `im_archive_cli/media_download.py`，Markdown 渲染迁移到独立模块供 `http_export.py` 使用。结构化 JSON/Markdown 聊天记录最终只允许走 `getMessagesBySession` HTTP 合同；CDP 仅保留登录态/请求发现/SingleFile 页面归档，不再通过 DOM 解析聊天列表。

**Tech Stack:** Python 3.13、`requests`、`pytest`、现有 `im_archive_cli` CLI、web-access CDP Proxy（仅用于登录态/请求发现/SingleFile）、携程 `13807/*` 列表接口、携程 `16037/getMessagesBySession` 详情接口。

---

## 当前事实基线

- `13807/*` 列表接口：页面来源为 `https://vbooking.ctrip.com/`，显式请求头包含 `appname: vbkbusiness`，登录态由浏览器自动发送 `.ctrip.com` 域 Cookie。
- `16037/getMessagesBySession` 详情接口：页面来源为 `https://imvendor.ctrip.com/`，显式请求头包含 `cookieOrigin: https://imvendor.ctrip.com`，登录态仍来自 `.ctrip.com` 域 Cookie。
- 纯 `requests` 路径曾遇到 `HTTP 403`；如果再次出现，应通过登录态刷新、请求头合同或 `discover detail-xhr` 修正，而不是回退到 DOM 聊天列表解析。
- 目标终态：`run export --kind structured` 只支持 `--via http`；CDP/Selenium 不再生成结构化 JSON/Markdown 聊天记录。
- 2026-06-27 到 2026-06-28 已抓取样本：`391` 个会话，`21817` 条消息，`391` 个 JSON 和 `391` 个 Markdown，输出目录为 `.im_archive/output_api_20260627_20260628`。
- 分页和控速基线：`page_size=1000`，`concurrency=4`，`window_sec=2`，即每批最多 4 个请求，批次间隔 0.5 秒。
- 详情接口真实返回顶层 `messages`，正文在 `messageBody` 字段；当前基础解析器已需要支持 `messages/messageBody`。
- 正文图片在 `messageBody` JSON 字符串中，典型字段为 `url`、`thumbUrl`、`width`、`height`、`btype`、`ext`，部分消息还有 `imagePath`、`thumbPath`、`originImageUrl`。
- 2026-06-27 到 2026-06-28 样本中正文图片约 `726` 条，分布在 `161` 个会话，观测到 `btype=1`。
- `infos[].avatar`、订单卡片、商品卡片里的图片不是客人/客服正文图片，不作为本计划的下载对象。
- 样本正文图片 CDN 可公开下载；对 `https://dimg04.tripcdn.com/images/1mq6g324x99roklym96B8.jpg` 执行 `curl -I -L` 返回 `HTTP/2 200` 和 `content-type: image/jpeg`。

## 文件结构

- Modify: `im_archive_cli/config.py`
  - 保持 HTTP/分页/控速默认值；增加图片下载配置。
- Modify: `im_archive_cli/ctrip_http.py`
  - 保持 vbooking/imvendor 请求头合同；归一化 `messages/messageBody`；识别正文图片附件。
- Create: `im_archive_cli/media.py`
  - 解析 `messageBody` JSON，识别正文图片，生成稳定附件文件名。
- Create: `im_archive_cli/media_download.py`
  - 下载公开图片，写入会话同级 assets 目录，回写 `localPath`、`relativePath`、`downloadStatus`。
- Modify: `im_archive_cli/export_structured.py`
  - 先承接旧 Selenium 结构化路径的图片下载接入；后续剥离任务会删除该模块并把 Markdown 渲染迁移出去。
- Modify: `im_archive_cli/http_export.py`
  - HTTP 结构化导出写 JSON/Markdown 前调用图片下载。
- Modify: `im_archive_cli/cdp_proxy_export.py`
  - 先承接旧 CDP Proxy 结构化路径的图片下载接入；后续剥离任务会删除 `export_structured_via_cdp_proxy()`，只保留 `CdpProxyClient` 和 SingleFile 归档。
- Create: `im_archive_cli/markdown_export.py`
  - 在剥离任务中承接 `_create_markdown()`，避免 HTTP 导出依赖 Selenium 结构化模块。
- Delete: `im_archive_cli/export_structured.py`
  - 在剥离任务中删除旧 Selenium DOM 结构化聊天记录抓取。
- Delete: `detail-page.js`
  - 在剥离任务中删除旧 CDP DOM 聊天记录提取脚本。
- Delete: `tests/detail_page_dom_filter_test.js`
  - 在剥离任务中删除旧 DOM 提取器测试。
- Modify: `im_archive_cli/imx_cli.py`
  - 在剥离任务中让 `run export --kind structured` 只允许 `--via http`。
- Create: `docs/AGENT_IMAGE_REFERENCE_GUIDE.md`
  - 给后续 Agent 读取 JSON/Markdown 客服对话记录时使用，说明如何引用和读取本地图片。
- Modify: `README.md`
  - 记录图片字段、落盘结构、禁用下载方式。
- Modify: `docs/HERMES_AGENT_RUNBOOK.md`
  - 记录 HTTP/CDP 请求边界、图片导出验收命令。
- Test: `tests/test_ctrip_http.py`
  - 覆盖请求头合同、`messages/messageBody`、正文图片归一化。
- Test: `tests/test_http_export.py`
  - 覆盖批量控速、结构化导出写图片和 Markdown 引用。
- Test: `tests/test_media.py`
  - 覆盖图片字段识别、噪音排除、文件名稳定性、下载落盘。

## 数据合同

图片消息继续使用现有 `messages[].attachments`，不引入数据库或全局索引。

```json
{
  "sequence": 31,
  "messageType": "image",
  "text": "[图片]",
  "attachments": [
    {
      "src": "https://dimg04.tripcdn.com/images/1mq6g324x99roklym96B8.jpg",
      "thumbSrc": "https://dimg04.tripcdn.com/images/1mq6g324x99roklym96B8_Z_0_540.jpg",
      "width": "83",
      "height": "180",
      "source": "messageBody",
      "btype": "1",
      "imagePath": "",
      "thumbPath": "",
      "localPath": "/absolute/output/20260627/cs/IMChatlogExport_x_assets/seq0031_abc123def456.jpg",
      "relativePath": "IMChatlogExport_x_assets/seq0031_abc123def456.jpg",
      "downloadStatus": "downloaded"
    }
  ]
}
```

## Task 0: Baseline Contract Verification

**Files:**
- Inspect: `im_archive_cli/config.py`
- Inspect: `im_archive_cli/ctrip_http.py`
- Inspect: `im_archive_cli/http_export.py`
- Inspect: `tests/test_ctrip_http.py`
- Inspect: `tests/test_http_export.py`

- [ ] **Step 1: Verify request/header functions exist**

Run:

```bash
rg -n "def build_vbooking_headers|def build_imvendor_headers|cookieorigin|cookieOrigin|getMessagesBySession|messageBody" im_archive_cli tests
```

Expected:

```text
im_archive_cli/ctrip_http.py:...
tests/test_ctrip_http.py:...
```

- [ ] **Step 2: Verify max probed defaults**

Run:

```bash
python3 - <<'PY'
from im_archive_cli.config import AppConfig
cfg = AppConfig()
print(cfg.page_size, cfg.concurrency, cfg.window_sec)
PY
```

Expected:

```text
1000 4 2
```

- [ ] **Step 3: Verify targeted baseline tests**

Run:

```bash
python3 -m pytest tests/test_ctrip_http.py tests/test_http_export.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 4: Stop on baseline regression**

If Step 3 fails, inspect only the failing test surface. Do not start image work until these contracts pass:

```text
build_vbooking_headers -> vbooking origin/referer/appname
build_imvendor_headers -> imvendor origin/referer/cookieorigin
build_detail_body -> sessionId + head.extension
normalize_detail_messages -> top-level messages + messageBody
export_structured_via_http -> 4-request batch + 0.5s interval
```

## Task 1: Media Parser

**Files:**
- Create: `im_archive_cli/media.py`
- Test: `tests/test_media.py`

- [ ] **Step 1: Write parser tests**

Create `tests/test_media.py` with:

```python
from __future__ import annotations

from im_archive_cli.media import (
    attachment_filename,
    extract_inline_image_attachment,
    iter_inline_image_attachments,
)


def test_extract_inline_image_attachment_from_message_body_json() -> None:
    body = (
        '{"btype":1,"url":"https://dimg04.tripcdn.com/images/original.jpg",'
        '"thumbUrl":"https://dimg04.tripcdn.com/images/thumb.jpg",'
        '"width":720,"height":1440,'
        '"imagePath":"/storage/emulated/0/DCIM/image.jpg",'
        '"ext":{"channel":"im_customer","scene":"to-operation"}}'
    )

    attachment = extract_inline_image_attachment(body)

    assert attachment == {
        "src": "https://dimg04.tripcdn.com/images/original.jpg",
        "thumbSrc": "https://dimg04.tripcdn.com/images/thumb.jpg",
        "width": "720",
        "height": "1440",
        "source": "messageBody",
        "btype": "1",
        "imagePath": "/storage/emulated/0/DCIM/image.jpg",
        "thumbPath": "",
    }


def test_extract_inline_image_attachment_prefers_origin_image_url_when_present() -> None:
    body = (
        '{"btype":1,"url":"https://dimg04.tripcdn.com/images/compressed.jpg",'
        '"originImageUrl":"https://dimg04.tripcdn.com/images/origin.jpg",'
        '"thumbUrl":"https://dimg04.tripcdn.com/images/thumb.jpg"}'
    )

    attachment = extract_inline_image_attachment(body)

    assert attachment["src"] == "https://dimg04.tripcdn.com/images/origin.jpg"
    assert attachment["thumbSrc"] == "https://dimg04.tripcdn.com/images/thumb.jpg"


def test_extract_inline_image_attachment_ignores_system_avatar_json() -> None:
    body = (
        '{"gtype":"1513","operator":"system","infos":[{"avatar":'
        '"https://dimg04.c-ctrip.com/images/avatar.jpg"}]}'
    )

    assert extract_inline_image_attachment(body) is None


def test_extract_inline_image_attachment_requires_image_like_url() -> None:
    body = '{"btype":1,"url":"https://m.ctrip.com/webapp/order/detail","thumbUrl":"https://m.ctrip.com/webapp/order/detail"}'

    assert extract_inline_image_attachment(body) is None


def test_iter_inline_image_attachments_reads_message_attachments() -> None:
    messages = [
        {"sequence": 1, "attachments": []},
        {
            "sequence": 2,
            "attachments": [
                {
                    "src": "https://dimg04.tripcdn.com/images/original.jpg",
                    "thumbSrc": "https://dimg04.tripcdn.com/images/thumb.jpg",
                    "source": "messageBody",
                }
            ],
        },
    ]

    rows = list(iter_inline_image_attachments(messages))

    assert rows == [
        (
            2,
            {
                "src": "https://dimg04.tripcdn.com/images/original.jpg",
                "thumbSrc": "https://dimg04.tripcdn.com/images/thumb.jpg",
                "source": "messageBody",
            },
        )
    ]


def test_attachment_filename_is_stable_and_keeps_extension() -> None:
    first = attachment_filename(31, "https://dimg04.tripcdn.com/images/original.jpg")
    second = attachment_filename(31, "https://dimg04.tripcdn.com/images/original.jpg")

    assert first == second
    assert first.startswith("seq0031_")
    assert first.endswith(".jpg")
```

- [ ] **Step 2: Run parser tests before implementation**

Run:

```bash
python3 -m pytest tests/test_media.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'im_archive_cli.media'
```

- [ ] **Step 3: Implement `im_archive_cli/media.py`**

Create `im_archive_cli/media.py`:

```python
from __future__ import annotations

import hashlib
import json
import re
import urllib.parse
from typing import Any, Iterator


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def extract_inline_image_attachment(message_body: str) -> dict[str, str] | None:
    body = _parse_json_object(message_body)
    if body is None:
        return None
    src = _first_non_empty(body, ("originImageUrl", "url"))
    thumb = _first_non_empty(body, ("thumbUrl", "url"))
    if not src or not thumb:
        return None
    if not _looks_like_image_url(src) and not _looks_like_image_url(thumb):
        return None
    return {
        "src": src,
        "thumbSrc": thumb,
        "width": _str_value(body.get("width")),
        "height": _str_value(body.get("height")),
        "source": "messageBody",
        "btype": _str_value(body.get("btype")),
        "imagePath": _str_value(body.get("imagePath")),
        "thumbPath": _str_value(body.get("thumbPath")),
    }


def iter_inline_image_attachments(messages: list[dict[str, Any]]) -> Iterator[tuple[int, dict[str, Any]]]:
    for message in messages:
        sequence = int(message.get("sequence") or 0)
        attachments = message.get("attachments") or []
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if isinstance(attachment, dict) and attachment.get("source") == "messageBody" and attachment.get("src"):
                yield sequence, attachment


def attachment_filename(sequence: int, url: str, content_type: str = "") -> str:
    digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:12]
    ext = _extension_from_url(url) or _extension_from_content_type(content_type) or ".bin"
    return f"seq{int(sequence):04d}_{digest}{ext}"


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _first_non_empty(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _str_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _looks_like_image_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme not in {"http", "https"}:
        return False
    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in IMAGE_EXTENSIONS):
        return True
    return bool(re.search(r"/images?/", path))


def _extension_from_url(url: str) -> str:
    path = urllib.parse.urlparse(str(url)).path.lower()
    for ext in IMAGE_EXTENSIONS:
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    return ""


def _extension_from_content_type(content_type: str) -> str:
    value = str(content_type or "").split(";", 1)[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }.get(value, "")
```

- [ ] **Step 4: Run parser tests**

Run:

```bash
python3 -m pytest tests/test_media.py -q
```

Expected:

```text
6 passed
```

## Task 2: Normalize Detail Messages As Images

**Files:**
- Modify: `im_archive_cli/ctrip_http.py`
- Test: `tests/test_ctrip_http.py`

- [ ] **Step 1: Add normalizer test**

Append to `tests/test_ctrip_http.py`:

```python
def test_normalize_detail_messages_extracts_inline_image_attachment() -> None:
    session = SessionRecord(session_id="s1", cs_name="Alice")
    payload = {
        "ResponseStatus": {"Ack": "Success"},
        "messages": [
            {
                "messageBody": (
                    '{"btype":1,"url":"https://dimg04.tripcdn.com/images/original.jpg",'
                    '"thumbUrl":"https://dimg04.tripcdn.com/images/thumb.jpg",'
                    '"width":720,"height":1440}'
                ),
                "createTime": "2026-06-27 09:00:00",
                "msgtype": "image",
            }
        ],
    }

    messages = normalize_detail_messages(payload, session)

    assert len(messages) == 1
    assert messages[0]["messageType"] == "image"
    assert messages[0]["text"] == "[图片]"
    assert messages[0]["attachments"] == [
        {
            "src": "https://dimg04.tripcdn.com/images/original.jpg",
            "thumbSrc": "https://dimg04.tripcdn.com/images/thumb.jpg",
            "width": "720",
            "height": "1440",
            "source": "messageBody",
            "btype": "1",
            "imagePath": "",
            "thumbPath": "",
        }
    ]
```

- [ ] **Step 2: Run normalizer test before implementation**

Run:

```bash
python3 -m pytest tests/test_ctrip_http.py::test_normalize_detail_messages_extracts_inline_image_attachment -q
```

Expected:

```text
FAILED with assertion showing messageType/text/attachments are not image-normalized yet
```

- [ ] **Step 3: Wire media parser into `normalize_detail_messages()`**

Modify imports in `im_archive_cli/ctrip_http.py`:

```python
from .media import extract_inline_image_attachment
```

Modify the body of `normalize_detail_messages()` where `text`、`attachments`、`message_type` are computed:

```python
        inline_image = extract_inline_image_attachment(text)
        attachments = _extract_attachments(row)
        if inline_image:
            attachments.append(inline_image)
            text = "[图片]"
        message_type = _normalize_message_type(
            _first_str(row, ("messageType", "msgType", "msgtype", "type")),
            text,
            attachments,
        )
```

Modify `_normalize_message_type()`:

```python
def _normalize_message_type(value: str, text: str, attachments: list[dict[str, str]]) -> str:
    lowered = value.lower()
    if attachments:
        return "image"
    if "image" in lowered or "img" in lowered or "pic" in lowered:
        return "image"
    if "card" in lowered or "order" in lowered:
        return "card"
    if text:
        return "text"
    return value or "unknown"
```

- [ ] **Step 4: Run detail normalization tests**

Run:

```bash
python3 -m pytest tests/test_ctrip_http.py::test_normalize_detail_messages_extracts_inline_image_attachment tests/test_ctrip_http.py::test_normalize_detail_messages_accepts_imvendor_messages_shape -q
```

Expected:

```text
2 passed
```

## Task 3: Image Downloader

**Files:**
- Create: `im_archive_cli/media_download.py`
- Modify: `im_archive_cli/config.py`
- Test: `tests/test_media.py`

- [ ] **Step 1: Add downloader test**

Append to `tests/test_media.py`:

```python
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from im_archive_cli.config import AppConfig
from im_archive_cli.media_download import download_conversation_images


def test_download_conversation_images_writes_local_file_and_relative_path(tmp_path: Path) -> None:
    class ImageHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            raw = b"fake-image-bytes"
            self.send_response(200)
            self.send_header("content-type", "image/jpeg")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), ImageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        image_url = f"http://127.0.0.1:{server.server_port}/image.jpg"
        conversation = {
            "messages": [
                {
                    "sequence": 1,
                    "attachments": [
                        {"src": image_url, "thumbSrc": image_url, "source": "messageBody"}
                    ],
                }
            ]
        }
        conversation_dir = tmp_path / "out" / "20260627" / "Alice"
        base_name = "IMChatlogExport_20260627090000_s1_Alice"
        cfg = AppConfig(download_images=True, image_max_workers=1, image_request_interval_sec=0)

        download_conversation_images(conversation, conversation_dir, base_name, cfg, lambda _msg: None)

        attachment = conversation["messages"][0]["attachments"][0]
        assert attachment["downloadStatus"] == "downloaded"
        assert attachment["relativePath"].startswith(f"{base_name}_assets/")
        assert Path(attachment["localPath"]).read_bytes() == b"fake-image-bytes"
        assert (conversation_dir / attachment["relativePath"]).exists()
    finally:
        server.shutdown()
        server.server_close()
```

- [ ] **Step 2: Run downloader test before implementation**

Run:

```bash
python3 -m pytest tests/test_media.py::test_download_conversation_images_writes_local_file_and_relative_path -q
```

Expected:

```text
ModuleNotFoundError: No module named 'im_archive_cli.media_download'
```

- [ ] **Step 3: Add image config fields**

Modify `im_archive_cli/config.py` `AppConfig`:

```python
    download_images: bool = True
    image_max_workers: int = 4
    image_request_interval_sec: float = 0.5
    image_timeout_sec: int = 30
    image_max_bytes: int = 20971520
```

- [ ] **Step 4: Implement `im_archive_cli/media_download.py`**

Create `im_archive_cli/media_download.py`:

```python
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import requests

from .config import AppConfig
from .media import attachment_filename, iter_inline_image_attachments


def download_conversation_images(
    conversation: dict[str, Any],
    conversation_dir: Path,
    base_name: str,
    config: AppConfig,
    log: Callable[[str], None],
) -> None:
    if not bool(getattr(config, "download_images", True)):
        return
    messages = conversation.get("messages") or []
    if not isinstance(messages, list):
        return
    rows = list(iter_inline_image_attachments(messages))
    if not rows:
        return
    assets_dir = conversation_dir / f"{base_name}_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    workers = max(1, int(getattr(config, "image_max_workers", 4)))
    interval = max(0.0, float(getattr(config, "image_request_interval_sec", 0.5)))
    for offset in range(0, len(rows), workers):
        if offset > 0 and interval:
            time.sleep(interval)
        batch = rows[offset : offset + workers]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_download_one, sequence, attachment, assets_dir, config): attachment
                for sequence, attachment in batch
            }
            for future in as_completed(futures):
                attachment = futures[future]
                try:
                    local_path = future.result()
                    attachment["localPath"] = str(local_path)
                    attachment["relativePath"] = str(local_path.relative_to(conversation_dir))
                    attachment["downloadStatus"] = "downloaded"
                except Exception as exc:  # noqa: BLE001
                    attachment["downloadStatus"] = "failed"
                    attachment["downloadError"] = str(exc)[:300]
                    log(f"图片下载失败: {attachment.get('src')} - {exc}")


def _download_one(sequence: int, attachment: dict[str, Any], assets_dir: Path, config: AppConfig) -> Path:
    url = str(attachment.get("src") or "").strip()
    if not url:
        raise RuntimeError("图片附件缺少 src")
    timeout = int(getattr(config, "image_timeout_sec", 30))
    max_bytes = int(getattr(config, "image_max_bytes", 20971520))
    with requests.get(url, stream=True, timeout=timeout) as response:
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}")
        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("image/"):
            raise RuntimeError(f"非图片响应: {content_type}")
        filename = attachment_filename(sequence, url, content_type)
        target = assets_dir / filename
        if target.exists() and target.stat().st_size > 0:
            return target
        tmp = target.with_suffix(target.suffix + ".part")
        total = 0
        with tmp.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise RuntimeError(f"图片超过大小限制: {max_bytes} bytes")
                fh.write(chunk)
        tmp.replace(target)
        return target
```

- [ ] **Step 5: Run media tests**

Run:

```bash
python3 -m pytest tests/test_media.py -q
```

Expected:

```text
7 passed
```

## Task 4: Export Integration And Markdown Rendering

**Files:**
- Modify: `im_archive_cli/export_structured.py`
- Modify: `im_archive_cli/http_export.py`
- Modify: `im_archive_cli/cdp_proxy_export.py`
- Test: `tests/test_http_export.py`

- [ ] **Step 1: Add export integration test**

Append to `tests/test_http_export.py`:

```python
def test_export_structured_via_http_downloads_images_and_renders_markdown(tmp_path: Path) -> None:
    cfg = AppConfig(
        output_dir=str(tmp_path / "out"),
        failures_file=str(tmp_path / "failures.jsonl"),
        window_sec=0,
        download_images=True,
        image_max_workers=1,
        image_request_interval_sec=0,
    )
    session = SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-27 09:00:00").normalized()

    class ImageClient:
        def fetch_conversation(self, session: SessionRecord) -> dict:
            return {
                "sessionId": session.session_id,
                "csName": session.cs_name,
                "detailUrl": session.detail_url,
                "createTime": session.create_time,
                "messages": [
                    {
                        "sequence": 1,
                        "timestampText": "2026-06-27 09:00:00",
                        "senderRole": "buyer",
                        "senderName": "Guest",
                        "messageType": "image",
                        "text": "[图片]",
                        "rawHtml": "",
                        "attachments": [
                            {
                                "src": "https://dimg04.tripcdn.com/images/original.jpg",
                                "thumbSrc": "https://dimg04.tripcdn.com/images/thumb.jpg",
                                "source": "messageBody",
                            }
                        ],
                    }
                ],
            }

    def fake_download(conversation, conversation_dir, base_name, config, log):
        attachment = conversation["messages"][0]["attachments"][0]
        asset_dir = conversation_dir / f"{base_name}_assets"
        asset_dir.mkdir(parents=True)
        asset_path = asset_dir / "seq0001_test.jpg"
        asset_path.write_bytes(b"fake")
        attachment["localPath"] = str(asset_path)
        attachment["relativePath"] = str(asset_path.relative_to(conversation_dir))
        attachment["downloadStatus"] = "downloaded"

    import im_archive_cli.http_export as http_export

    original = http_export.download_conversation_images
    http_export.download_conversation_images = fake_download
    try:
        success, failed = export_structured_via_http(ImageClient(), cfg, [session], ["json", "markdown"], lambda _msg: None)
    finally:
        http_export.download_conversation_images = original

    assert (success, failed) == (1, 0)
    json_file = next((tmp_path / "out").rglob("*.json"))
    md_file = next((tmp_path / "out").rglob("*.md"))
    data = json.loads(json_file.read_text(encoding="utf-8"))
    attachment = data["messages"][0]["attachments"][0]
    assert attachment["relativePath"].endswith("seq0001_test.jpg")
    assert "![图片](IMChatlogExport_20260627090000_s1_Alice_assets/seq0001_test.jpg)" in md_file.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run export integration test before implementation**

Run:

```bash
python3 -m pytest tests/test_http_export.py::test_export_structured_via_http_downloads_images_and_renders_markdown -q
```

Expected:

```text
FAILED with AttributeError showing http_export.download_conversation_images is missing
```

- [ ] **Step 3: Update Markdown rendering**

Modify `_create_markdown()` in `im_archive_cli/export_structured.py` attachment rendering:

```python
        attachments = message.get("attachments") or []
        for attachment in attachments:
            src = attachment.get("relativePath") or attachment.get("src") or ""
            if not src:
                continue
            alt = "图片" if message.get("messageType") == "image" else "附件"
            if message.get("messageType") == "image":
                lines.append(f"![{alt}]({src})")
            else:
                lines.append(f"- 附件: {src}")
```

- [ ] **Step 4: Call downloader before HTTP export writes**

Modify `im_archive_cli/http_export.py` imports:

```python
from .media_download import download_conversation_images
```

Modify `_fetch_and_write_session()` after `messages` validation and before file writes:

```python
    conversation_dir = json_path.parent
    base_name = json_path.stem
    download_conversation_images(data, conversation_dir, base_name, config, log)
```

- [ ] **Step 5: Call downloader before CDP Proxy export writes**

Modify `im_archive_cli/cdp_proxy_export.py` imports:

```python
from .media_download import download_conversation_images
```

Modify `export_structured_via_cdp_proxy()` after message validation and before file writes:

```python
            download_conversation_images(data, json_path.parent, json_path.stem, config, log)
```

- [ ] **Step 6: Call downloader before Selenium export writes**

Modify `im_archive_cli/export_structured.py` imports:

```python
from .media_download import download_conversation_images
```

Modify `export_structured()` after `messages = data.get("messages", [])` and before file writes:

```python
            download_conversation_images(data, json_path.parent, json_path.stem, config, log)
```

- [ ] **Step 7: Run export tests**

Run:

```bash
python3 -m pytest tests/test_http_export.py -q
```

Expected:

```text
all selected tests pass
```

## Task 5: Agent Image Reference Documentation

**Files:**
- Create: `docs/AGENT_IMAGE_REFERENCE_GUIDE.md`
- Modify: `README.md`
- Modify: `docs/HERMES_AGENT_RUNBOOK.md`

- [ ] **Step 1: Create Agent guide**

Create `docs/AGENT_IMAGE_REFERENCE_GUIDE.md`:

````markdown
# Agent Image Reference Guide

本文档给后续 Agent 使用，目标是从已经导出的携程客服对话 JSON 或 Markdown 中稳定定位、读取正文图片。

## 文件布局

结构化导出后，每个会话至少有一个 JSON 文件，可选一个 Markdown 文件。若该会话含正文图片，图片文件保存在同级 assets 目录。

```text
.im_archive/output/
  20260627/
    vbk_2538173_门票活动旅游管家Nay/
      IMChatlogExport_20260627002824_300001161305505_vbk_2538173_门票活动旅游管家Nay.json
      IMChatlogExport_20260627002824_300001161305505_vbk_2538173_门票活动旅游管家Nay.md
      IMChatlogExport_20260627002824_300001161305505_vbk_2538173_门票活动旅游管家Nay_assets/
        seq0031_abc123def456.jpg
```

## JSON 读取规则

JSON 是首选读取面。图片只认 `messages[].attachments[]` 中 `source == "messageBody"` 的附件。

```json
{
  "sequence": 31,
  "messageType": "image",
  "text": "[图片]",
  "attachments": [
    {
      "source": "messageBody",
      "src": "https://dimg04.tripcdn.com/images/original.jpg",
      "thumbSrc": "https://dimg04.tripcdn.com/images/thumb.jpg",
      "localPath": "/absolute/path/to/seq0031_abc123def456.jpg",
      "relativePath": "IMChatlogExport_x_assets/seq0031_abc123def456.jpg",
      "downloadStatus": "downloaded"
    }
  ]
}
```

读取顺序：

1. 若 `downloadStatus == "downloaded"` 且 `localPath` 存在，读取 `localPath`。
2. 若 `localPath` 为空但 `relativePath` 存在，用 `json_file.parent / relativePath` 得到图片路径。
3. 若本地文件不存在，才使用 `src` 作为远程原图 URL。
4. `thumbSrc` 只用于预览兜底，不作为正文图片的首选证据。
5. 忽略 `infos[].avatar`、订单卡片、商品卡片中的图片 URL，它们不是客人/客服正文图片。

Python 读取示例：

```python
import json
from pathlib import Path


def iter_message_images(json_path: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    for message in data.get("messages", []):
        for attachment in message.get("attachments", []) or []:
            if attachment.get("source") != "messageBody":
                continue
            local_path = Path(str(attachment.get("localPath") or ""))
            if local_path.exists():
                yield message, local_path
                continue
            relative_path = str(attachment.get("relativePath") or "")
            if relative_path:
                candidate = json_path.parent / relative_path
                if candidate.exists():
                    yield message, candidate
                    continue
            yield message, str(attachment.get("src") or "")
```

## Markdown 读取规则

Markdown 中图片以相对路径引用：

```markdown
![图片](IMChatlogExport_20260627002824_300001161305505_vbk_2538173_门票活动旅游管家Nay_assets/seq0031_abc123def456.jpg)
```

解析方式：

1. 读取 Markdown 文件所在目录 `md_file.parent`。
2. 提取 `![图片](...)` 中的路径。
3. 如果路径不是 `http://` 或 `https://`，用 `md_file.parent / path` 转为本地路径。
4. 如果路径是远程 URL，按远程图片处理。

Python 读取示例：

```python
import re
from pathlib import Path


IMAGE_PATTERN = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def iter_markdown_images(md_path: Path):
    text = md_path.read_text(encoding="utf-8")
    for match in IMAGE_PATTERN.finditer(text):
        value = match.group(1).strip()
        if value.startswith(("http://", "https://")):
            yield value
        else:
            yield md_path.parent / value
```

## 失败状态

当 `downloadStatus == "failed"`：

- 先读取 `downloadError` 判断是 HTTP 状态、非图片响应、还是大小限制。
- 如果任务只需要理解文字上下文，可以保留 `[图片]` 占位并继续。
- 如果任务必须读图，优先重新下载 `src`；如果 `src` 不可用，再尝试 `thumbSrc`。
- 不要把图片下载失败等同于会话导出失败，文本和消息顺序仍然有效。

## Agent 输出要求

当 Agent 汇总会话时：

- 引用图片时写明 `sessionId`、`sequence` 和图片本地路径。
- 不要把本地绝对路径改写成不存在的相对路径。
- 不要把头像、商品卡片图、订单卡片图描述为客人发送的图片。
- 如果只看到 `src/thumbSrc` 远程 URL 而没有本地文件，明确说明该图片尚未本地化。
````

- [ ] **Step 2: Add README section**

Append under the structured export section in `README.md`:

```markdown
### 正文图片导出

`getMessagesBySession` 返回的正文图片位于 `messages[].messageBody` JSON 字符串中，常见字段为 `url`、`thumbUrl`、`width`、`height`、`btype`、`ext`。导出器只把这种正文图片写入 `attachments`，不会下载 `infos[].avatar`、订单卡片、商品卡片中的装饰图片。

默认行为：

- `download_images: true`
- 图片保存到每个会话 JSON/Markdown 同级的 `<base_name>_assets/`
- JSON 附件会写入 `src`、`thumbSrc`、`localPath`、`relativePath`、`downloadStatus`
- Markdown 使用 `relativePath` 渲染 `![图片](...)`

如只需要远程 URL，不下载图片：

```yaml
download_images: false
```

后续 Agent 读取客服对话图片时，优先阅读 `docs/AGENT_IMAGE_REFERENCE_GUIDE.md`。
```

- [ ] **Step 3: Add runbook validation commands**

Append under `docs/HERMES_AGENT_RUNBOOK.md` export validation section:

```markdown
### 图片导出验收

导出后检查正文图片数量和本地文件数量：

```bash
python3 - <<'PY'
import json
from pathlib import Path
out = Path(".im_archive/output")
images = []
for p in out.rglob("IMChatlogExport_*.json"):
    data = json.loads(p.read_text(encoding="utf-8"))
    for msg in data.get("messages", []):
        for att in msg.get("attachments", []) or []:
            if att.get("source") == "messageBody":
                images.append(att)
print("inline_images", len(images))
print("downloaded", sum(1 for item in images if item.get("downloadStatus") == "downloaded"))
print("failed", sum(1 for item in images if item.get("downloadStatus") == "failed"))
PY
```

验收标准：

- `inline_images` 大于 `0` 时，`downloaded` 应接近 `inline_images`
- `failed` 不应阻断文本导出，但需要检查 JSON 内的 `downloadError`
- Markdown 中应出现 `![图片](..._assets/...)`
```

- [ ] **Step 4: Run documentation sanity check**

Run:

```bash
rg -n "正文图片导出|图片导出验收|Agent Image Reference Guide|iter_message_images|iter_markdown_images|download_images|relativePath" README.md docs/HERMES_AGENT_RUNBOOK.md docs/AGENT_IMAGE_REFERENCE_GUIDE.md
```

Expected:

```text
README.md:...
docs/HERMES_AGENT_RUNBOOK.md:...
docs/AGENT_IMAGE_REFERENCE_GUIDE.md:...
```

## Task 6: Remove DOM-Based Structured Chat Export

**Files:**
- Create: `im_archive_cli/markdown_export.py`
- Modify: `im_archive_cli/http_export.py`
- Modify: `im_archive_cli/cdp_proxy_export.py`
- Modify: `im_archive_cli/imx_cli.py`
- Modify: `tests/test_http_export.py`
- Modify: `README.md`
- Modify: `docs/HERMES_AGENT_RUNBOOK.md`
- Delete: `im_archive_cli/export_structured.py`
- Delete: `detail-page.js`
- Delete: `tests/detail_page_dom_filter_test.js`

**Boundary:** This task removes only DOM-based structured JSON/Markdown chat extraction. Keep CDP for `collect --via cdp`, `discover detail-xhr --via cdp|proxy`, `preflight`, auth/page inspection, and `singlefile` page archival.

- [ ] **Step 1: Write failing tests that forbid DOM structured export**

Modify imports at the top of `tests/test_http_export.py`:

```python
import importlib.util
```

Remove these imports from `tests/test_http_export.py`:

```python
from im_archive_cli.cdp_proxy_export import export_structured_via_cdp_proxy
from im_archive_cli.export_structured import export_structured
```

Append these tests to `tests/test_http_export.py`:

```python
def test_structured_export_via_cdp_is_rejected_by_parser(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = AppConfig(
        state_file=str(tmp_path / "state.json"),
        output_dir=str(tmp_path / "out"),
        log_dir=str(tmp_path / "logs"),
        failures_file=str(tmp_path / "failures.jsonl"),
        window_sec=0,
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg.__dict__, sort_keys=False, allow_unicode=True), encoding="utf-8")
    StateStore(Path(cfg.state_file)).set_sessions(
        [SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 09:00:00")],
        auto_select_all=True,
    )

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--config",
                str(cfg_path),
                "run",
                "export",
                "--kind",
                "structured",
                "--via",
                "cdp",
            ]
        )

    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert "invalid choice: 'cdp'" in captured.err


def test_dom_structured_export_modules_are_removed() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert importlib.util.find_spec("im_archive_cli.export_structured") is None
    assert not (repo_root / "detail-page.js").exists()
```

- [ ] **Step 2: Run tests and verify they fail before implementation**

Run:

```bash
python3 -m pytest \
  tests/test_http_export.py::test_structured_export_via_cdp_is_rejected_by_parser \
  tests/test_http_export.py::test_dom_structured_export_modules_are_removed \
  -q
```

Expected before implementation:

```text
FAILED tests/test_http_export.py::test_structured_export_via_cdp_is_rejected_by_parser
FAILED tests/test_http_export.py::test_dom_structured_export_modules_are_removed
```

The first failure proves `structured --via cdp` is still accepted. The second failure proves `im_archive_cli/export_structured.py` or `detail-page.js` still exists.

- [ ] **Step 3: Move Markdown rendering out of the Selenium module**

Create `im_archive_cli/markdown_export.py`:

```python
from __future__ import annotations

from .models import SessionRecord


def create_markdown(meta: SessionRecord, messages: list[dict]) -> str:
    lines = [
        f"# 会话 {meta.session_id}",
        "",
        f"- 客服: {meta.cs_name}",
        f"- 链接: {meta.detail_url}",
        f"- 消息数: {len(messages)}",
        "",
    ]
    for message in messages:
        sender = message.get("senderRole", "unknown")
        name = message.get("senderName", "")
        lines.append(f"## {message.get('sequence', '-')}. {sender}{f' / {name}' if name else ''}")
        lines.append("")
        lines.append(f"- 时间: {message.get('timestampText', '-')}")
        lines.append(f"- 类型: {message.get('messageType', '-')}")
        text = message.get("text") or ("[图片消息]" if message.get("messageType") == "image" else "[空内容]")
        lines.append(f"- 文本: {text}")
        attachments = message.get("attachments") or []
        for attachment in attachments:
            path = attachment.get("relativePath") or attachment.get("src") or ""
            if message.get("messageType") == "image":
                lines.append(f"![图片]({path})")
            elif path:
                lines.append(f"- 附件: {path}")
        lines.append("")
    return "\n".join(lines)
```

Modify `im_archive_cli/http_export.py` import:

```python
from .markdown_export import create_markdown
```

Replace Markdown writes in `im_archive_cli/http_export.py`:

```python
md_path.write_text(create_markdown(sess, data.get("messages", [])), encoding="utf-8")
```

- [ ] **Step 4: Remove CDP Proxy structured DOM export**

Modify the import in `im_archive_cli/cdp_proxy_export.py` so it no longer imports Markdown rendering or image download:

```python
from .config import AppConfig
from .models import SessionRecord
from .utils import append_failure, normalize_create_time_parts, safe_name
```

Delete the entire `export_structured_via_cdp_proxy()` function from `im_archive_cli/cdp_proxy_export.py`.

Keep these existing symbols in `im_archive_cli/cdp_proxy_export.py` without changing their public signatures:

```python
class CdpProxyClient:
    def __init__(self, base_url: str):
        self.base_url = str(base_url).rstrip("/")

def export_singlefile_via_cdp_proxy(
    proxy: CdpProxyClient,
    repo_root: Path,
    config: AppConfig,
    sessions: list[SessionRecord],
    log: Callable[[str], None],
    resume_from_state: bool = True,
) -> tuple[int, int]:
    # Existing SingleFile implementation stays in this function.
    # Do not move SingleFile behavior into HTTP export.
```

- [ ] **Step 5: Make structured export HTTP-only in the CLI**

Modify imports in `im_archive_cli/imx_cli.py`:

```python
from .cdp_proxy_export import CdpProxyClient, export_singlefile_via_cdp_proxy
```

Modify `cmd_run_export()` signature default:

```python
via: str = "http",
```

Modify the request-budget guard text in `cmd_run_export()`:

```python
if (request_budget is not None or request_ledger) and not (kind == "structured" and via == "http"):
    raise RuntimeError("export request-budget/request-ledger 只支持 structured --via http；SingleFile/links 路径无法精确计数")
```

Replace the structured branch in `cmd_run_export()` with:

```python
if kind == "structured":
    if via != "http":
        raise RuntimeError("structured export 已剥离 CDP/Selenium DOM 抓取路径，只支持 --via http")
    selected_formats = _parse_csv(formats) or ["json"]
    invalid = [f for f in selected_formats if f not in {"json", "markdown"}]
    if invalid:
        raise RuntimeError(f"未知结构化导出格式: {', '.join(invalid)}")
    if budget and budget.remaining < len(sessions):
        raise RuntimeError(
            "export request-budget 剩余额度不足："
            f"remaining={budget.remaining}, selected_sessions={len(sessions)}；"
            "为避免中途耗尽预算，已在发出任何携程详情请求前停止"
        )
    client = CtripImDetailHttpClient(cfg, log=logger.info, request_budget=budget)
    try:
        success, failed = export_structured_via_http(client, cfg, sessions, selected_formats, logger.info)
    finally:
        if budget:
            logger.info(f"携程接口请求计数: used={budget.used}, limit={budget.limit}")
    summary.success = success
    summary.failed = failed
    summary.finished_at = _now_utc_iso()
    store.set_summary(summary)
    logger.info(f"导出结束: kind={kind} via=http success={success} failed={failed}")
    return 0
```

Keep the remaining proxy branch only for `singlefile`:

```python
proxy = CdpProxyClient(cfg.cdp_proxy_base_url)
if kind == "singlefile":
    success, failed = export_singlefile_via_cdp_proxy(proxy, repo_root, cfg, sessions, logger.info)
else:
    raise RuntimeError(f"未知导出类型: {kind}")
```

Modify parser default and choices in `build_parser()`:

```python
export.add_argument("--via", choices=["http"], default="http", help="structured 导出方式；只支持纯 HTTP 请求")
```

- [ ] **Step 6: Delete obsolete DOM structured files**

Run:

```bash
git rm detail-page.js im_archive_cli/export_structured.py tests/detail_page_dom_filter_test.js
```

Expected:

```text
rm 'detail-page.js'
rm 'im_archive_cli/export_structured.py'
rm 'tests/detail_page_dom_filter_test.js'
```

- [ ] **Step 7: Remove obsolete CDP/Selenium structured tests**

Delete these entire test functions from `tests/test_http_export.py`:

```text
test_export_structured_via_cdp_proxy_messages_empty_fails_and_does_not_write_files
test_export_structured_via_selenium_messages_empty_fails_and_does_not_write_files
```

Keep HTTP export tests, image download tests, request-budget tests, and CLI HTTP entrypoint tests.

- [ ] **Step 8: Update operator docs to state structured export is HTTP-only**

In `README.md`, replace the structured export section that says `--via cdp|http` with:

```markdown
#### 结构化 JSON/Markdown 导出

`structured` 导出只支持 `--via http`。它读取 `state.json` 里的会话列表，通过已验证的 `16037/getMessagesBySession` 请求合同拉取消息，并写出 JSON/Markdown。

```bash
imx run export --kind structured --via http --formats json,markdown
```

CDP/Selenium DOM 聊天记录抓取已经剥离；不要再通过 `detail-page.js` 或页面 DOM 解析聊天列表生成 JSON/Markdown。CDP 仍用于 `collect --via cdp`、`discover detail-xhr`、`preflight` 和 `singlefile` 页面归档。
```

In `docs/HERMES_AGENT_RUNBOOK.md`, replace the section that recommends `run export --kind structured --via cdp` with:

```markdown
结构化 JSON/Markdown 导出只允许使用 HTTP 详情接口：

```bash
imx run export --kind structured --via http --formats json,markdown
```

如果 HTTP 详情导出返回 `401/403` 或 `messages` 为空，不要回退到 CDP/Selenium DOM 聊天记录抓取。应先执行 `discover detail-xhr` 复核 `getMessagesBySession` 请求体、请求头和登录态，再修正配置。
```

- [ ] **Step 9: Run focused tests**

Run:

```bash
python3 -m pytest tests/test_http_export.py tests/test_media.py tests/test_ctrip_http.py tests/test_imx_cli_state_flow.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 10: Verify DOM structured hooks are gone**

Run:

```bash
rg -n "detail-page|export_structured_via_cdp_proxy|from \\.export_structured|extractConversationStructured" im_archive_cli tests README.md docs/HERMES_AGENT_RUNBOOK.md || true
```

Expected:

```text
<no output>
```

Run:

```bash
python3 - <<'PY'
from im_archive_cli.imx_cli import build_parser
parser = build_parser()
try:
    parser.parse_args(["run", "export", "--kind", "structured", "--via", "cdp"])
except SystemExit as exc:
    print(exc.code)
PY
```

Expected:

```text
2
```

- [ ] **Step 11: Commit**

```bash
git add \
  README.md \
  docs/HERMES_AGENT_RUNBOOK.md \
  im_archive_cli/markdown_export.py \
  im_archive_cli/http_export.py \
  im_archive_cli/cdp_proxy_export.py \
  im_archive_cli/imx_cli.py \
  tests/test_http_export.py \
  detail-page.js \
  im_archive_cli/export_structured.py \
  tests/detail_page_dom_filter_test.js
git commit -m "refactor: remove dom structured chat export"
```

## Task 7: End-To-End Verification

**Files:**
- No source edits

- [ ] **Step 1: Run all targeted tests**

Run:

```bash
python3 -m pytest tests/test_media.py tests/test_ctrip_http.py tests/test_http_export.py tests/test_imx_cli_state_flow.py -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Run one-session HTTP image smoke export**

Use one known image session from the sample, such as `300001161305505`. Select only that session in state or use a temporary config/state path, then run:

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export --kind structured --formats json,markdown --via http
```

Expected:

```text
导出结束: kind=structured via=http success=1 failed=0
```

- [ ] **Step 3: Verify local image files**

Run:

```bash
python3 - <<'PY'
import json
from pathlib import Path
out = Path(".im_archive/output")
json_file = next(out.rglob("IMChatlogExport_*.json"))
data = json.loads(json_file.read_text(encoding="utf-8"))
attachments = [
    att
    for msg in data.get("messages", [])
    for att in (msg.get("attachments") or [])
    if att.get("source") == "messageBody"
]
print("attachments", len(attachments))
print("downloaded", sum(1 for att in attachments if att.get("downloadStatus") == "downloaded"))
for att in attachments[:3]:
    path = Path(att["localPath"])
    print(path.exists(), path.stat().st_size, att["relativePath"])
PY
```

Expected:

```text
attachments 1
downloaded 1
True <positive byte count> <asset relative path>
```

- [ ] **Step 4: Verify Markdown image references**

Run:

```bash
rg -n "!\[图片\]\(.*_assets/.*\)" .im_archive/output
```

Expected:

```text
.im_archive/output/.../IMChatlogExport_....md:...
```

- [ ] **Step 5: Verify no secrets were written**

Run:

```bash
rg -n "cookie=|vbkticket=|bticket=|GUID=" docs im_archive_cli tests README.md
```

Expected:

```text
No real cookie values. Test fixture values such as foo=bar are acceptable.
```

- [ ] **Step 6: Verify DOM structured export is not present**

Run:

```bash
rg -n "detail-page|export_structured_via_cdp_proxy|from \\.export_structured|extractConversationStructured" im_archive_cli tests README.md docs/HERMES_AGENT_RUNBOOK.md || true
```

Expected:

```text
<no output>
```

## Commit Plan

- Commit 1: `feat: parse ctrip inline image messages`
  - Files: `im_archive_cli/media.py`, `tests/test_media.py`
- Commit 2: `feat: classify ctrip inline images`
  - Files: `im_archive_cli/ctrip_http.py`, `tests/test_ctrip_http.py`
- Commit 3: `feat: download ctrip inline images`
  - Files: `im_archive_cli/config.py`, `im_archive_cli/media_download.py`, `tests/test_media.py`
- Commit 4: `feat: include local images in ctrip exports`
  - Files: `im_archive_cli/export_structured.py`, `im_archive_cli/http_export.py`, `im_archive_cli/cdp_proxy_export.py`, `tests/test_http_export.py`
- Commit 5: `docs: document ctrip inline image references`
  - Files: `README.md`, `docs/HERMES_AGENT_RUNBOOK.md`, `docs/AGENT_IMAGE_REFERENCE_GUIDE.md`
- Commit 6: `refactor: remove dom structured chat export`
  - Files: `im_archive_cli/markdown_export.py`, `im_archive_cli/http_export.py`, `im_archive_cli/cdp_proxy_export.py`, `im_archive_cli/imx_cli.py`, `tests/test_http_export.py`, `README.md`, `docs/HERMES_AGENT_RUNBOOK.md`, `detail-page.js`, `im_archive_cli/export_structured.py`, `tests/detail_page_dom_filter_test.js`

## Self-Review

- Spec coverage: HTTP 请求合同、最大分页和控速基线、正文图片识别、正文/噪音区分、本地存储、JSON 引用、Markdown 引用、Agent 图片读取指南、CDP/Selenium DOM 结构化聊天记录剥离、端到端验收均有任务覆盖。
- Placeholder scan: 本计划不包含禁止占位词或空泛实现步骤。
- Type consistency: `extract_inline_image_attachment()`、`iter_inline_image_attachments()`、`attachment_filename()`、`download_conversation_images()`、`create_markdown()` 在测试、实现和导出集成步骤中命名一致。
