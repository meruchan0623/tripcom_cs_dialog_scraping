# Tripcom IM CLI CDP Export Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the documented `run export --kind structured --via cdp` mismatch and make `run collect --via cdp` recover cleanly when `cdp_proxy_base_url` is unavailable but the configured DevTools endpoint is available.

**Architecture:** Keep structured JSON/Markdown export HTTP-only; do not reintroduce CDP/Selenium DOM structured export. Update stale operator docs and the Hermes skill so the production flow is `collect --via cdp`, `roles select`, `links export`, then `structured export` with no `--via` or `--via http`. Harden `CtripImCdpFetchClient` target discovery by accepting both `targetId` and `id`, then falling back from the proxy `/targets` endpoint to direct Chrome/Edge DevTools `/json/list` and `Runtime.evaluate` when the proxy times out.

**Tech Stack:** Python 3.10+, argparse, urllib, websocket-client, pytest, Markdown docs, local `imx` CLI.

## Global Constraints

- Current implementation choice is Option A: structured export is HTTP-only.
- Do not add `cdp` to `run export --via` argparse choices unless a separate design implements real CDP detail export end-to-end.
- Keep `run collect --via cdp` supported and default.
- Keep `run export --kind structured` defaulting to `--via http`.
- Do not reintroduce `detail-page.js`, Selenium DOM structured export, or chromedriver-based structured export.
- Do not log Cookie, Authorization, cticket, full request headers, or full request bodies containing credentials.
- Keep changes constrained to CLI runtime behavior, tests, README/runbook/skill instructions, and the CDP fetch client.

---

## Success Criteria

- `imx run export --kind structured --formats json,markdown --via cdp` still fails at argparse with `invalid choice: 'cdp'`.
- `imx run export --kind structured --formats json,markdown` remains the recommended structured export command.
- `README.md`, `docs/HERMES_AGENT_RUNBOOK.md`, and `skills/hermes-ctrip-im-archive/SKILL.md` do not recommend `structured --via cdp`.
- `CtripImCdpFetchClient` accepts proxy targets shaped as `{"targetId": "..."}` and Chrome DevTools targets shaped as `{"id": "...", "webSocketDebuggerUrl": "..."}`.
- If `cdp_proxy_base_url` `/targets` times out and `http://127.0.0.1:<cdp_port>/json/list` has the vbooking target, `collect --via cdp` logs `cdp_proxy_base_url 不可用，已 fallback 到 127.0.0.1:<cdp_port> DevTools endpoint` and evaluates fetch through direct CDP.
- If both proxy and direct DevTools target discovery fail, the CLI prints a clean `错误:` message with no Python traceback.

## File Structure

- Modify `README.md`: clarify that structured JSON/Markdown export is HTTP-only and document the collect CDP proxy fallback behavior.
- Modify `docs/HERMES_AGENT_RUNBOOK.md`: keep the current HTTP-only structured export guidance and add direct DevTools fallback troubleshooting.
- Modify `skills/hermes-ctrip-im-archive/SKILL.md`: replace stale `structured --via cdp` production guidance with the current HTTP-only structured export flow.
- Create `tests/test_runbook_commands.py`: regression guard for stale docs and skill command snippets.
- Modify `tests/test_ctrip_http.py`: add target schema, proxy timeout fallback, and direct Runtime.evaluate tests for `CtripImCdpFetchClient`.
- Modify `tests/test_imx_cli_state_flow.py`: add a CLI-level no-traceback failure test for unavailable proxy and unavailable direct DevTools.
- Modify `im_archive_cli/ctrip_http.py`: implement target schema normalization, proxy-to-direct fallback, and direct CDP evaluation.

## Not In Scope

- Do not implement `run export --via cdp` for structured JSON/Markdown.
- Do not create or ship a mini CDP proxy in this repository.
- Do not change SingleFile export semantics; it still needs a proxy-compatible `/new`, `/eval`, and `/close` surface.
- Do not touch unrelated parser, media, image index, XLSX, or GUI code.

### Task 1: Lock Structured Export Docs To HTTP-Only

**Files:**
- Create: `tests/test_runbook_commands.py`
- Modify: `README.md`
- Modify: `docs/HERMES_AGENT_RUNBOOK.md`
- Modify: `skills/hermes-ctrip-im-archive/SKILL.md`

**Interfaces:**
- Consumes: current argparse contract from `im_archive_cli/imx_cli.py` where `run export --via` choices are `["http"]`.
- Produces: docs and skill instructions that recommend:
  - `imx run collect --start-date YYYY-MM-DD --end-date YYYY-MM-DD --page-size 100 --max-pages 50 --via cdp`
  - `imx roles select --all`
  - `imx run export --kind links --output /path/to/links.xlsx`
  - `imx run export --kind structured --formats json,markdown`

- [ ] **Step 1: Add the docs regression test**

Create `tests/test_runbook_commands.py` with this exact content:

```python
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_PATHS = [
    ROOT / "README.md",
    ROOT / "docs" / "HERMES_AGENT_RUNBOOK.md",
    ROOT / "skills" / "hermes-ctrip-im-archive" / "SKILL.md",
]

FORBIDDEN_PATTERNS = [
    re.compile(r"run export\s+--kind structured[^\n`]*--via cdp"),
    re.compile(r"structured\s+--via cdp"),
    re.compile(r"结构化和 SingleFile 导出同样复用 web-access CDP proxy"),
    re.compile(r"detail-page\.js"),
]


def test_structured_export_docs_do_not_recommend_cdp() -> None:
    failures: list[str] = []
    for path in DOC_PATHS:
        text = path.read_text(encoding="utf-8")
        for pattern in FORBIDDEN_PATTERNS:
            match = pattern.search(text)
            if match:
                failures.append(f"{path.relative_to(ROOT)}: forbidden {pattern.pattern!r} at offset {match.start()}")

    assert failures == []


def test_hermes_skill_contains_current_production_flow() -> None:
    text = (ROOT / "skills" / "hermes-ctrip-im-archive" / "SKILL.md").read_text(encoding="utf-8")

    assert "--via cdp" in text
    assert "python3 -m im_archive_cli.imx_cli run collect" in text
    assert "python3 -m im_archive_cli.imx_cli run export --kind links" in text
    assert "python3 -m im_archive_cli.imx_cli run export --kind structured --formats \"${STRUCTURED_FORMATS:-json,markdown}\"" in text
    assert "structured export 已剥离 CDP/Selenium DOM 抓取路径，只支持 --via http" in text
```

- [ ] **Step 2: Run the docs test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_runbook_commands.py -q
```

Expected: FAIL. The failure must point to `skills/hermes-ctrip-im-archive/SKILL.md` stale `structured --via cdp`, `detail-page.js`, or the README sentence that says structured export reuses the CDP proxy.

- [ ] **Step 3: Fix the README contradiction**

In `README.md`, replace the sentence that currently says:

```markdown
本仓库新增 `imx` 命令：Python 通过 CLI 执行采集/筛选/导出。会话采集默认走“模拟前端请求”模式：复用当前已登录的 `vbooking.ctrip.com` 页面，在页面上下文内直接 `fetch` 携程后台 SOA 接口，不再依赖展开表格的 DOM 点击循环。结构化和 SingleFile 导出同样复用 web-access CDP proxy 的当前登录浏览器，不再依赖 chromedriver 下载。
```

with this exact text:

```markdown
本仓库新增 `imx` 命令：Python 通过 CLI 执行采集/筛选/导出。会话采集默认走“模拟前端请求”模式：复用当前已登录的 `vbooking.ctrip.com` 页面，在页面上下文内直接 `fetch` 携程后台 SOA 接口，不再依赖展开表格的 DOM 点击循环。结构化 JSON/Markdown 导出只走已验证详情接口的 HTTP 请求；SingleFile HTML 归档继续复用 web-access CDP proxy 的当前登录浏览器。
```

- [ ] **Step 4: Replace the Hermes skill verified flow**

In `skills/hermes-ctrip-im-archive/SKILL.md`, replace lines 21-38 with:

```markdown
## 已验证链路

当前项目在 Hermes/Agent 机器上的已验证链路是：

1. `run collect --via cdp`：
   复用已登录的 `vbooking.ctrip.com` 页面，在页面上下文里发真实前端 `fetch`，抓客服列表和会话列表。
2. `roles select`：
   基于 `.im_archive/state.json` 选择全部或部分客服。
3. `run export --kind links`：
   读取 Python state 并导出链接表 xlsx，便于人工抽检和后续二次处理。
4. `run export --kind structured --formats json,markdown`：
   通过已验证的详情消息接口走纯 HTTP 请求，落盘 `json` / `md`。不传 `--via` 时默认等同 `--via http`。
5. 后处理：
   用导出的 `IMChatlogExport_*.json`、`links xlsx`、会话索引 `csv/xlsx` 做筛选、统计、质检分析。

注意：

- 纯 `requests` 的 `collect --via http` 在当前环境里可能对 `13807` 直接返回 `403`，所以会话列表采集优先使用 `collect --via cdp`。
- `structured` 导出只支持 `--via http`。使用前应先通过 `discover detail-xhr` 验证并写回 `ctrip_im_detail_messages_url`。
- `--via cdp` 仍用于采集列表、请求发现、预检和 SingleFile 页面归档；不要再用 CDP/Selenium DOM 或旧详情页注入脚本解析聊天列表生成结构化 JSON/Markdown。
```

- [ ] **Step 5: Replace the Hermes skill export section**

In `skills/hermes-ctrip-im-archive/SKILL.md`, replace the `### 4. 导出` section from its header through the `说明：` bullet list with this exact section:

````markdown
### 4. 导出

链接表：

```bash
python3 -m im_archive_cli.imx_cli run export --kind links
```

结构化 JSON：

```bash
python3 -m im_archive_cli.imx_cli run export --kind structured --formats json
```

结构化 JSON + Markdown：

```bash
python3 -m im_archive_cli.imx_cli run export --kind structured --formats json,markdown
```

SingleFile HTML：

```bash
python3 -m im_archive_cli.imx_cli run export --kind singlefile
```

Hermes 正式任务推荐：

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export --kind links
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export \
  --kind structured \
  --formats json,markdown
```

说明：

- `structured`：只支持 `--via http`；不传 `--via` 时默认就是 HTTP。
- `links`：先导出链接表，方便确认 state 里选中的会话和详情 URL。
- `singlefile`：适合保留页面归档，不适合作为主要分析输入。
````

- [ ] **Step 6: Replace full-day and single-role command templates**

In `skills/hermes-ctrip-im-archive/SKILL.md`, in both the `全量抓取某一天` and `只抓某个客服` templates, replace each structured export block:

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export \
  --kind structured \
  --formats json,markdown \
  --via cdp
```

with:

```bash
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export --kind links
python3 -m im_archive_cli.imx_cli --config .im_archive/config.yaml run export \
  --kind structured \
  --formats json,markdown
```

- [ ] **Step 7: Update the automatic task snippet**

In `skills/hermes-ctrip-im-archive/SKILL.md`, ensure the automatic task snippet contains this exact export sequence:

```bash
python3 -m im_archive_cli.imx_cli roles select --all
python3 -m im_archive_cli.imx_cli run export --kind links
python3 -m im_archive_cli.imx_cli run export --kind structured --formats "${STRUCTURED_FORMATS:-json,markdown}"
python3 -m im_archive_cli.imx_cli state watch --once
```

- [ ] **Step 8: Update success and fault guidance**

In `skills/hermes-ctrip-im-archive/SKILL.md`, replace the stale success rule:

```markdown
- JSON 文件存在但 `messages=0`：失败，需要检查详情页是否登录、`sessionId` 是否被截断、`detail-page.js` 选择器是否失效。
```

with:

```markdown
- JSON 文件存在但 `messages=0`：失败，需要检查 `getMessagesBySession` 请求体、请求头、分页参数和登录态；不要回退到 DOM 抓取。
```

Replace the `### 403` section with:

```markdown
### 403

症状：`structured --via http` 返回 401/403，或业务错误提示登录态、Token、风控。

处理：不要循环重试，也不要把 structured export 改成 `--via cdp`。先刷新登录态，再执行 `discover detail-xhr` 复核详情消息接口合同和 Cookie 同步状态。`collect --via cdp` 仍可作为列表采集验证，不代表 HTTP 详情导出一定可用。
```

Replace the final `不要做` bullet:

```markdown
- 不要把 `structured` / `singlefile` 改回 chromedriver 依赖；Hermes 自动主机应走 `cdp_proxy_base_url`。
```

with:

```markdown
- 不要把 `structured` 改回 CDP/Selenium DOM 抓取；结构化 JSON/Markdown 只走已验证详情接口的 HTTP 请求。
- 不要把 `singlefile` 改回 chromedriver 依赖；Hermes 自动主机应走 `cdp_proxy_base_url` 或兼容该接口的 proxy。
```

- [ ] **Step 9: Run docs guard**

Run:

```bash
python3 -m pytest tests/test_runbook_commands.py -q
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add README.md docs/HERMES_AGENT_RUNBOOK.md skills/hermes-ctrip-im-archive/SKILL.md tests/test_runbook_commands.py
git commit -m "docs: align tripcom structured export flow"
```

### Task 2: Normalize CDP Target Schemas

**Files:**
- Modify: `tests/test_ctrip_http.py`
- Modify: `im_archive_cli/ctrip_http.py`

**Interfaces:**
- Consumes: `CtripImCdpFetchClient(cfg: AppConfig, log: Callable[[str], None] | None = None, request_interval_sec: float | None = None, target_id: str | None = None, request_budget: CtripRequestBudget | None = None)`.
- Produces:
  - `_target_identifier(target: dict[str, Any]) -> str`
  - `_select_vbooking_target(targets: list[dict[str, Any]]) -> dict[str, Any]`
  - `CtripImCdpFetchClient._find_vbooking_target() -> str` accepting both `targetId` and `id`.

- [ ] **Step 1: Add imports and URL response helper in tests**

Modify the top of `tests/test_ctrip_http.py` so the import section includes:

```python
import json

import pytest
import requests

from im_archive_cli import ctrip_http
from im_archive_cli.config import AppConfig
from im_archive_cli.ctrip_http import (
    CustomerServiceAccount,
    CtripHttpError,
    CtripImCdpFetchClient,
    CtripImDetailHttpClient,
    CtripImHttpClient,
    CtripRequestBudget,
    CtripRequestBudgetExceeded,
    EMPLOYEE_URL,
    build_employee_body,
    build_detail_body,
    build_imvendor_headers,
    build_session_body,
    build_vbooking_headers,
    inspect_auth_sources,
    normalize_detail_messages,
)
from im_archive_cli.models import SessionRecord
```

After the imports, add:

```python
class FakeUrlopenResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeUrlopenResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def url_text(url: object) -> str:
    return str(getattr(url, "full_url", url))
```

- [ ] **Step 2: Add the failing target schema tests**

Append these tests to `tests/test_ctrip_http.py`:

```python
def test_cdp_fetch_client_uses_proxy_target_id_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    seen_urls: list[str] = []

    def fake_urlopen(url, timeout=5):
        seen_urls.append(url_text(url))
        assert url_text(url) == "http://localhost:3456/targets"
        return FakeUrlopenResponse(
            [
                {
                    "targetId": "proxy-target-1",
                    "url": "https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience",
                }
            ]
        )

    monkeypatch.setattr(ctrip_http.urllib.request, "urlopen", fake_urlopen)

    client = CtripImCdpFetchClient(AppConfig(), request_interval_sec=0)

    assert client.target_id == "proxy-target-1"
    assert seen_urls == ["http://localhost:3456/targets"]


def test_cdp_fetch_client_accepts_chrome_devtools_id_schema_from_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(url, timeout=5):
        assert url_text(url) == "http://localhost:3456/targets"
        return FakeUrlopenResponse(
            [
                {
                    "id": "devtools-page-1",
                    "webSocketDebuggerUrl": "ws://devtools-page-1",
                    "url": "https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience",
                }
            ]
        )

    monkeypatch.setattr(ctrip_http.urllib.request, "urlopen", fake_urlopen)

    client = CtripImCdpFetchClient(AppConfig(), request_interval_sec=0)

    assert client.target_id == "devtools-page-1"
```

- [ ] **Step 3: Run target schema tests to verify one fails**

Run:

```bash
python3 -m pytest \
  tests/test_ctrip_http.py::test_cdp_fetch_client_uses_proxy_target_id_schema \
  tests/test_ctrip_http.py::test_cdp_fetch_client_accepts_chrome_devtools_id_schema_from_targets \
  -q
```

Expected: first test PASS, second test FAIL with `KeyError: 'targetId'` or a target id assertion failure.

- [ ] **Step 4: Add target normalization helpers**

In `im_archive_cli/ctrip_http.py`, add these helpers above `class CtripImCdpFetchClient`:

```python
def _target_identifier(target: dict[str, Any]) -> str:
    return str(target.get("targetId") or target.get("id") or "")


def _select_vbooking_target(targets: list[dict[str, Any]]) -> dict[str, Any]:
    for require_imexperience in (True, False):
        for target in targets:
            url = str(target.get("url") or "")
            if "vbooking.ctrip.com" not in url:
                continue
            if require_imexperience and "IMExperience" not in url:
                continue
            if _target_identifier(target):
                return target
    raise RuntimeError("未找到已登录的 vbooking.ctrip.com 页面；请先在浏览器打开携程 IMExperience 页面")
```

- [ ] **Step 5: Use target normalization in `_find_vbooking_target`**

Replace `CtripImCdpFetchClient._find_vbooking_target()` in `im_archive_cli/ctrip_http.py` with:

```python
    def _find_vbooking_target(self) -> str:
        with urllib.request.urlopen(f"{self.proxy_base_url}/targets", timeout=5) as resp:  # noqa: S310
            raw_targets = json.loads(resp.read().decode("utf-8"))
        targets = raw_targets if isinstance(raw_targets, list) else []
        target = _select_vbooking_target(targets)
        return _target_identifier(target)
```

- [ ] **Step 6: Run target schema tests**

Run:

```bash
python3 -m pytest \
  tests/test_ctrip_http.py::test_cdp_fetch_client_uses_proxy_target_id_schema \
  tests/test_ctrip_http.py::test_cdp_fetch_client_accepts_chrome_devtools_id_schema_from_targets \
  -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add im_archive_cli/ctrip_http.py tests/test_ctrip_http.py
git commit -m "fix: accept chrome cdp target schema"
```

### Task 3: Add Proxy-To-DevTools Fallback For CDP Collect

**Files:**
- Modify: `tests/test_ctrip_http.py`
- Modify: `im_archive_cli/ctrip_http.py`

**Interfaces:**
- Consumes:
  - `_target_identifier(target: dict[str, Any]) -> str`
  - `_select_vbooking_target(targets: list[dict[str, Any]]) -> dict[str, Any]`
  - `CDPClient.call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]`
- Produces:
  - `_read_json_url(url: str, timeout: float = 5.0) -> Any`
  - `_format_cdp_discovery_error(exc: BaseException) -> str`
  - `CtripImCdpFetchClient._cdp_eval_mode: str` with values `"proxy"` or `"direct"`
  - `CtripImCdpFetchClient._target_ws_url: str`
  - `CtripImCdpFetchClient._eval_script(script: str, timeout: int) -> dict[str, Any]`

- [ ] **Step 1: Add fallback discovery test**

Append this test to `tests/test_ctrip_http.py`:

```python
def test_cdp_fetch_client_falls_back_to_devtools_json_list_when_proxy_targets_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_urls: list[str] = []

    def fake_urlopen(url, timeout=5):
        text = url_text(url)
        seen_urls.append(text)
        if text == "http://localhost:3456/targets":
            raise TimeoutError("timed out")
        if text == "http://127.0.0.1:9222/json/list":
            return FakeUrlopenResponse(
                [
                    {
                        "id": "page-1",
                        "webSocketDebuggerUrl": "ws://page-1",
                        "url": "https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience",
                    }
                ]
            )
        raise AssertionError(f"unexpected url: {text}")

    logs: list[str] = []
    monkeypatch.setattr(ctrip_http.urllib.request, "urlopen", fake_urlopen)

    client = CtripImCdpFetchClient(AppConfig(cdp_port=9222), log=logs.append, request_interval_sec=0)

    assert client.target_id == "page-1"
    assert seen_urls == ["http://localhost:3456/targets", "http://127.0.0.1:9222/json/list"]
    assert any("cdp_proxy_base_url 不可用，已 fallback 到 127.0.0.1:9222 DevTools endpoint" in line for line in logs)
```

- [ ] **Step 2: Add direct Runtime.evaluate test**

Append this test to `tests/test_ctrip_http.py`:

```python
def test_cdp_fetch_client_uses_direct_devtools_runtime_evaluate_after_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(url, timeout=5):
        text = url_text(url)
        if text == "http://localhost:3456/targets":
            raise TimeoutError("timed out")
        if text == "http://127.0.0.1:9222/json/list":
            return FakeUrlopenResponse(
                [
                    {
                        "id": "page-1",
                        "webSocketDebuggerUrl": "ws://page-1",
                        "url": "https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience",
                    }
                ]
            )
        raise AssertionError(f"direct fallback should not call proxy eval url: {text}")

    class FakeCDPClient:
        ws_urls: list[str] = []
        calls: list[tuple[str, dict]] = []
        closed = False

        def __init__(self, ws_url: str) -> None:
            self.ws_url = ws_url
            FakeCDPClient.ws_urls.append(ws_url)

        def call(self, method: str, params: dict | None = None) -> dict:
            payload = params or {}
            FakeCDPClient.calls.append((method, payload))
            if method == "Runtime.enable":
                return {}
            if method == "Runtime.evaluate":
                return {
                    "result": {
                        "value": {
                            "status": 200,
                            "ok": True,
                            "text": '{"ResponseStatus":{"Ack":"Success"},"ok":true}',
                            "data": {"ResponseStatus": {"Ack": "Success"}, "ok": True},
                        }
                    }
                }
            raise AssertionError(f"unexpected CDP method: {method}")

        def close(self) -> None:
            FakeCDPClient.closed = True

    monkeypatch.setattr(ctrip_http.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ctrip_http, "CDPClient", FakeCDPClient)

    client = CtripImCdpFetchClient(AppConfig(cdp_port=9222), request_interval_sec=0)
    data = client.post_json(EMPLOYEE_URL, {"probe": True})

    assert data == {"ResponseStatus": {"Ack": "Success"}, "ok": True}
    assert FakeCDPClient.ws_urls == ["ws://page-1"]
    assert FakeCDPClient.closed is True
    assert FakeCDPClient.calls[0] == ("Runtime.enable", {})
    assert FakeCDPClient.calls[1][0] == "Runtime.evaluate"
    evaluate_params = FakeCDPClient.calls[1][1]
    assert evaluate_params["awaitPromise"] is True
    assert evaluate_params["returnByValue"] is True
    assert "fetch(" in evaluate_params["expression"]
```

- [ ] **Step 3: Run fallback tests to verify they fail**

Run:

```bash
python3 -m pytest \
  tests/test_ctrip_http.py::test_cdp_fetch_client_falls_back_to_devtools_json_list_when_proxy_targets_timeout \
  tests/test_ctrip_http.py::test_cdp_fetch_client_uses_direct_devtools_runtime_evaluate_after_fallback \
  -q
```

Expected: FAIL because `CtripImCdpFetchClient` currently stops at proxy `/targets` and has no direct CDP evaluation path.

- [ ] **Step 4: Add direct CDP import**

In `im_archive_cli/ctrip_http.py`, add this import with the existing imports:

```python
from urllib.error import HTTPError, URLError
```

Add this project import with the existing relative imports:

```python
from .cdp_plugin_controller import CDPClient
```

- [ ] **Step 5: Add JSON URL and error formatting helpers**

In `im_archive_cli/ctrip_http.py`, add these helpers below `_select_vbooking_target()`:

```python
def _read_json_url(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _format_cdp_discovery_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"
```

- [ ] **Step 6: Initialize CDP evaluation mode**

In `CtripImCdpFetchClient.__init__()` in `im_archive_cli/ctrip_http.py`, replace:

```python
        self.proxy_base_url = str(cfg.cdp_proxy_base_url).rstrip("/")
        self.target_id = target_id or self._find_vbooking_target()
```

with:

```python
        self.proxy_base_url = str(cfg.cdp_proxy_base_url).rstrip("/")
        self.direct_cdp_base_url = f"http://127.0.0.1:{int(getattr(cfg, 'cdp_port', 9222) or 9222)}"
        self._cdp_eval_mode = "proxy"
        self._target_ws_url = ""
        self.target_id = target_id or self._find_vbooking_target()
```

- [ ] **Step 7: Replace `_find_vbooking_target` with fallback logic**

Replace `CtripImCdpFetchClient._find_vbooking_target()` in `im_archive_cli/ctrip_http.py` with:

```python
    def _find_vbooking_target(self) -> str:
        proxy_error: BaseException | None = None
        try:
            raw_targets = _read_json_url(f"{self.proxy_base_url}/targets", timeout=5)
            targets = raw_targets if isinstance(raw_targets, list) else []
            target = _select_vbooking_target(targets)
            self._cdp_eval_mode = "proxy"
            self._target_ws_url = str(target.get("webSocketDebuggerUrl") or "")
            return _target_identifier(target)
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as exc:
            proxy_error = exc

        try:
            raw_targets = _read_json_url(f"{self.direct_cdp_base_url}/json/list", timeout=5)
            targets = raw_targets if isinstance(raw_targets, list) else []
            target = _select_vbooking_target(targets)
            target_id = _target_identifier(target)
            ws_url = str(target.get("webSocketDebuggerUrl") or "")
            if not ws_url:
                raise RuntimeError(f"DevTools target 未返回 webSocketDebuggerUrl: {target_id}")
            self._cdp_eval_mode = "direct"
            self._target_ws_url = ws_url
            self.log(
                "cdp_proxy_base_url 不可用，已 fallback 到 "
                f"127.0.0.1:{int(getattr(self.cfg, 'cdp_port', 9222) or 9222)} DevTools endpoint"
            )
            return target_id
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, json.JSONDecodeError) as direct_exc:
            proxy_text = _format_cdp_discovery_error(proxy_error) if proxy_error else "unknown"
            direct_text = _format_cdp_discovery_error(direct_exc)
            port = int(getattr(self.cfg, "cdp_port", 9222) or 9222)
            raise RuntimeError(
                "cdp_proxy_base_url 不可用"
                f"（{self.proxy_base_url}/targets: {proxy_text}），且 "
                f"127.0.0.1:{port} DevTools endpoint 不可用或未找到 vbooking 页面"
                f"（{direct_text}）；请启动 CDP proxy，或确认 Edge/Chrome 已用 "
                f"--remote-debugging-port={port} 打开 IMExperience 页面"
            ) from direct_exc
```

- [ ] **Step 8: Add `_eval_script` helper**

In `CtripImCdpFetchClient`, add this method between `_find_vbooking_target()` and `post_json()`:

```python
    def _eval_script(self, script: str, timeout: int) -> dict[str, Any]:
        if self._cdp_eval_mode == "direct":
            if not self._target_ws_url:
                raise RuntimeError("DevTools target 未返回 webSocketDebuggerUrl，无法执行页面上下文请求")
            page = CDPClient(self._target_ws_url)
            try:
                page.call("Runtime.enable")
                result = page.call(
                    "Runtime.evaluate",
                    {
                        "expression": script,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                )
                if result.get("exceptionDetails"):
                    raise RuntimeError(f"CDP Runtime.evaluate 异常: {result['exceptionDetails']}")
                return {"value": result.get("result", {}).get("value")}
            finally:
                page.close()

        request = urllib.request.Request(
            f"{self.proxy_base_url}/eval?target={urllib.parse.quote(self.target_id)}",
            data=script.encode("utf-8"),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout + 5) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
```

- [ ] **Step 9: Route `post_json` through `_eval_script`**

In `CtripImCdpFetchClient.post_json()` in `im_archive_cli/ctrip_http.py`, replace:

```python
        request = urllib.request.Request(
            f"{self.proxy_base_url}/eval?target={urllib.parse.quote(self.target_id)}",
            data=script.encode("utf-8"),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout + 5) as resp:  # noqa: S310
                envelope = json.loads(resp.read().decode("utf-8"))
        finally:
            self.last_request_at = time.monotonic()
```

with:

```python
        try:
            envelope = self._eval_script(script, timeout)
        finally:
            self.last_request_at = time.monotonic()
```

- [ ] **Step 10: Run fallback tests**

Run:

```bash
python3 -m pytest \
  tests/test_ctrip_http.py::test_cdp_fetch_client_falls_back_to_devtools_json_list_when_proxy_targets_timeout \
  tests/test_ctrip_http.py::test_cdp_fetch_client_uses_direct_devtools_runtime_evaluate_after_fallback \
  -q
```

Expected: PASS.

- [ ] **Step 11: Run the full HTTP/CDP client test file**

Run:

```bash
python3 -m pytest tests/test_ctrip_http.py -q
```

Expected: PASS.

- [ ] **Step 12: Commit**

```bash
git add im_archive_cli/ctrip_http.py tests/test_ctrip_http.py
git commit -m "fix: fallback to direct cdp for collect"
```

### Task 4: Clean CLI Error And Operator Fallback Docs

**Files:**
- Modify: `tests/test_imx_cli_state_flow.py`
- Modify: `README.md`
- Modify: `docs/HERMES_AGENT_RUNBOOK.md`
- Modify: `skills/hermes-ctrip-im-archive/SKILL.md`

**Interfaces:**
- Consumes: `main(argv: list[str] | None = None) -> int`, which catches `RuntimeError` and prints `错误: ...`.
- Produces: CLI-visible failure message with no traceback when both proxy and direct DevTools discovery fail.

- [ ] **Step 1: Add CLI no-traceback test import**

In `tests/test_imx_cli_state_flow.py`, add this import after the existing project imports:

```python
import im_archive_cli.ctrip_http as ctrip_http
```

- [ ] **Step 2: Add the CLI no-traceback test**

Append this test to `tests/test_imx_cli_state_flow.py`:

```python
def test_main_collect_cdp_reports_proxy_and_devtools_discovery_failure_without_traceback(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from im_archive_cli.config import save_config
    from im_archive_cli.imx_cli import main

    cfg = make_cfg(tmp_path)
    cfg.cdp_proxy_base_url = "http://localhost:3456"
    cfg.cdp_port = 9222
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg_path, cfg)

    def fake_urlopen(url, timeout=5):
        raise TimeoutError("timed out")

    monkeypatch.setattr(ctrip_http.urllib.request, "urlopen", fake_urlopen)

    rc = main(
        [
            "--config",
            str(cfg_path),
            "run",
            "collect",
            "--via",
            "cdp",
            "--start-date",
            "2026-06-23",
            "--end-date",
            "2026-06-29",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "cdp_proxy_base_url 不可用" in captured.err
    assert "127.0.0.1:9222 DevTools endpoint" in captured.err
    assert "Traceback" not in captured.err
```

- [ ] **Step 3: Run the CLI error test**

Run:

```bash
python3 -m pytest tests/test_imx_cli_state_flow.py::test_main_collect_cdp_reports_proxy_and_devtools_discovery_failure_without_traceback -q
```

Expected: PASS after Task 3. If it fails with a traceback, wrap only the CDP discovery failure as `RuntimeError` in `CtripImCdpFetchClient._find_vbooking_target()`; do not add broad traceback suppression outside `main()`.

- [ ] **Step 4: Document fallback in README config section**

In `README.md`, under the `cdp_proxy_base_url` bullet in the configuration section, add:

```markdown
  当该 proxy 的 `/targets` 超时但 `cdp_port` 指向的 Edge/Chrome DevTools 正常时，`run collect --via cdp` 会自动读取 `http://127.0.0.1:<cdp_port>/json/list`，接受 DevTools 原生 `id` 字段，并通过目标页的 `webSocketDebuggerUrl` 执行 `Runtime.evaluate`。SingleFile 归档仍需要 `cdp_proxy_base_url` 提供 `/new`、`/eval`、`/close`。
```

- [ ] **Step 5: Document fallback in Hermes runbook**

In `docs/HERMES_AGENT_RUNBOOK.md`, after the existing CDP proxy checks near the top, add:

````markdown
如果 `curl http://localhost:3456/targets` 超时，但 Edge/Chrome DevTools 端口正常：

```bash
curl -s http://127.0.0.1:9222/json/list | python3 -m json.tool | rg 'vbooking\\.ctrip\\.com|IMExperience|webSocketDebuggerUrl'
```

`run collect --via cdp` 会自动 fallback 到 `127.0.0.1:<cdp_port>` DevTools endpoint。日志中应出现：

```text
cdp_proxy_base_url 不可用，已 fallback 到 127.0.0.1:9222 DevTools endpoint
```

如果 direct DevTools 也不可用，先重新启动带 `--remote-debugging-port` 的 Edge/Chrome，或修正 `config.yaml` 的 `cdp_port`。
````

- [ ] **Step 6: Document fallback in Hermes skill troubleshooting**

In `skills/hermes-ctrip-im-archive/SKILL.md`, replace the `### CDP proxy 不可用` section with:

````markdown
### CDP proxy 不可用

症状：`curl http://localhost:3456/targets` 失败或超时。

处理：

```bash
node /Users/tashima_meru/.cc-switch/skills/web-access/scripts/check-deps.mjs
curl -s http://localhost:3456/targets
curl -s http://127.0.0.1:9222/json/list | python3 -m json.tool | rg 'vbooking\\.ctrip\\.com|IMExperience|webSocketDebuggerUrl'
```

如果 `cdp_proxy_base_url` 不可用但 `cdp_port` DevTools endpoint 可用，`run collect --via cdp` 会自动 fallback 到 `127.0.0.1:<cdp_port>`，并输出：

```text
cdp_proxy_base_url 不可用，已 fallback 到 127.0.0.1:9222 DevTools endpoint
```

SingleFile 归档仍需要兼容 `/new`、`/eval`、`/close` 的 proxy；direct DevTools fallback 只覆盖 `collect --via cdp` 的页面上下文 fetch。
````

- [ ] **Step 7: Run focused docs and CLI checks**

Run:

```bash
python3 -m pytest \
  tests/test_runbook_commands.py \
  tests/test_imx_cli_state_flow.py::test_main_collect_cdp_reports_proxy_and_devtools_discovery_failure_without_traceback \
  -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add README.md docs/HERMES_AGENT_RUNBOOK.md skills/hermes-ctrip-im-archive/SKILL.md tests/test_imx_cli_state_flow.py
git commit -m "docs: document cdp collect fallback"
```

### Task 5: End-To-End Regression Verification

**Files:**
- Modify: no files

**Interfaces:**
- Consumes all changes from Tasks 1-4.
- Produces verified behavior for parser contract, docs contract, HTTP client behavior, CDP fallback, and state flow.

- [ ] **Step 1: Verify export parser still rejects `--via cdp`**

Run:

```bash
python3 -m pytest tests/test_http_export.py::test_structured_export_via_cdp_is_rejected_by_parser -q
```

Expected: PASS with the test asserting `invalid choice: 'cdp'`.

- [ ] **Step 2: Verify docs guard**

Run:

```bash
python3 -m pytest tests/test_runbook_commands.py -q
```

Expected: PASS.

- [ ] **Step 3: Verify CDP/HTTP client tests**

Run:

```bash
python3 -m pytest tests/test_ctrip_http.py -q
```

Expected: PASS.

- [ ] **Step 4: Verify CLI state flow tests**

Run:

```bash
python3 -m pytest tests/test_imx_cli_state_flow.py -q
```

Expected: PASS.

- [ ] **Step 5: Verify export tests are not regressed**

Run:

```bash
python3 -m pytest tests/test_http_export.py tests/test_cdp_proxy_export.py -q
```

Expected: PASS.

- [ ] **Step 6: Run the targeted stale-command search**

Run:

```bash
rg -n "run export .*--via cdp|structured --via cdp|detail-page\\.js|结构化和 SingleFile 导出同样复用" README.md docs skills tests im_archive_cli || true
```

Expected output may include historical plan files under `docs/superpowers/plans/`; it must not include active operator docs or active skill files:

```text
```

If `rg` prints only old `docs/superpowers/plans/...` references, leave those immutable historical plans unchanged.

- [ ] **Step 7: Run full test suite**

Run:

```bash
python3 -m pytest -q
```

Expected: PASS.

- [ ] **Step 8: Final commit if Task 5 found any small verification-only fixes**

If Task 5 required a small follow-up fix, commit only that fix:

```bash
git add <files changed by the verification fix>
git commit -m "test: cover tripcom cdp export contracts"
```

If Task 5 changed no files, skip this commit.

## Self-Review

**Spec coverage:** The plan covers the observed argparse failure, keeps structured export HTTP-only, updates README/runbook/skill guidance, preserves `collect --via cdp`, adds proxy timeout fallback to direct DevTools, accepts `targetId` and `id` schemas, and verifies clean CLI errors without traceback.

**Placeholder scan:** The plan contains exact files, exact test code, exact implementation snippets, exact doc replacement text, exact commands, and expected results. It does not require guessed functions or undefined interfaces.

**Type consistency:** New helper names are defined before use: `_target_identifier`, `_select_vbooking_target`, `_read_json_url`, `_format_cdp_discovery_error`, and `_eval_script`. `CtripImCdpFetchClient._eval_script()` returns the same `{"value": ...}` envelope shape that `post_json()` already consumes.
