from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError

import requests

from .cdp_plugin_controller import CDPClient
from .config import AppConfig
from .models import SessionRecord
from .media import extract_inline_image_attachment
from .state import dedupe_sessions

EMPLOYEE_URL = "https://m.ctrip.com/restapi/soa2/13807/getEmployeeDimMetricDetailsV3"
SESSION_URL = "https://m.ctrip.com/restapi/soa2/13807/getSessionDimMetricDetailsV3"
VBOOKING_ORIGIN = "https://vbooking.ctrip.com"
VBOOKING_REFERER = "https://vbooking.ctrip.com/"
IMVENDOR_ORIGIN = "https://imvendor.ctrip.com"
IMVENDOR_REFERER = "https://imvendor.ctrip.com/"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36 Edg/149.0.0.0"
)
KNOWN_NON_MESSAGE_DETAIL_ENDPOINTS = (
    "/15529/queryIMSessionInfo",
)


@dataclass(frozen=True)
class CustomerServiceAccount:
    account_id: str
    account_name: str
    session_count: int = 0

    @property
    def display_name(self) -> str:
        if self.account_id and self.account_name:
            return f"{self.account_id}/{self.account_name}"
        return self.account_name or self.account_id or "Unknown"


class CtripRequestBudgetExceeded(RuntimeError):
    pass


class CtripHttpError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_sec: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_sec = retry_after_sec


@dataclass
class CtripRequestBudget:
    limit: int
    used: int = 0
    ledger_path: Path | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.ledger_path and self.ledger_path.exists():
            try:
                data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
                self.used = int(data.get("used") or 0)
            except Exception:  # noqa: BLE001
                self.used = 0

    def consume(self, label: str) -> None:
        with self._lock:
            if self.used + 1 > self.limit:
                raise CtripRequestBudgetExceeded(f"携程接口请求预算已耗尽：limit={self.limit}, used={self.used}, next={label}")
            self.used += 1
            self.save()

    def add_used(self, count: int) -> None:
        with self._lock:
            next_used = self.used + int(count)
            if next_used > self.limit:
                raise CtripRequestBudgetExceeded(f"携程接口请求预算已耗尽：limit={self.limit}, used={self.used}, next_add={count}")
            self.used = next_used
            self.save()

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.used)

    @property
    def exceeded(self) -> bool:
        return self.used > self.limit

    def save(self) -> None:
        if not self.ledger_path:
            return
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self.ledger_path.write_text(
            json.dumps(
                {"limit": self.limit, "used": self.used, "remaining": self.remaining, "exceeded": self.exceeded},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def default_date_range() -> tuple[str, str]:
    yesterday = date.today() - timedelta(days=1)
    value = yesterday.isoformat()
    return value, value


def load_cookie_header(cfg: AppConfig) -> tuple[str, str]:
    cookie_file = Path(cfg.ctrip_cookie_header_file).expanduser()
    if cookie_file.exists():
        value = cookie_file.read_text(encoding="utf-8").strip()
        if value:
            return value, str(cookie_file)

    auth_json = Path(cfg.ctrip_auth_json).expanduser()
    if auth_json.exists():
        data = json.loads(auth_json.read_text(encoding="utf-8"))
        value = str(data.get("cookieHeader") or "").strip()
        if value:
            return value, str(auth_json)

    raise RuntimeError(f"未找到可用携程 Cookie：{cookie_file} / {auth_json}")


def inspect_auth_sources(cfg: AppConfig) -> dict[str, Any]:
    cookie_file = Path(cfg.ctrip_cookie_header_file).expanduser()
    auth_json = Path(cfg.ctrip_auth_json).expanduser()
    sources: list[dict[str, Any]] = []
    selected: str | None = None
    for kind, path in (("cookie_header_file", cookie_file), ("auth_json", auth_json)):
        item: dict[str, Any] = {"kind": kind, "path": str(path), "exists": path.exists(), "usable": False}
        if path.exists():
            item["size"] = path.stat().st_size
            try:
                if kind == "auth_json":
                    data = json.loads(path.read_text(encoding="utf-8"))
                    cookie_header = str(data.get("cookieHeader") or "").strip()
                    item["createdAt"] = data.get("createdAt")
                    item["source"] = data.get("source")
                else:
                    cookie_header = path.read_text(encoding="utf-8").strip()
                item.update(_summarize_cookie_header(cookie_header))
                item["usable"] = bool(cookie_header)
            except Exception as exc:  # noqa: BLE001
                item["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        if item["usable"] and selected is None:
            selected = str(path)
        sources.append(item)
    return {"selected": selected, "sources": sources}


def _summarize_cookie_header(cookie_header: str) -> dict[str, Any]:
    pairs = [part.strip() for part in str(cookie_header or "").split(";") if part.strip()]
    names = []
    for pair in pairs:
        name = pair.split("=", 1)[0].strip()
        if name:
            names.append(name)
    return {
        "cookieHeaderLength": len(str(cookie_header or "")),
        "cookieCount": len(names),
        "cookieNames": names[:20],
        "truncatedCookieNames": max(0, len(names) - 20),
    }


def build_vbooking_headers(cookie_header: str) -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=UTF-8",
        "origin": VBOOKING_ORIGIN,
        "referer": VBOOKING_REFERER,
        "appname": "vbkbusiness",
        "user-agent": BROWSER_USER_AGENT,
        "cookie": cookie_header,
    }


def build_imvendor_headers(cookie_header: str) -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": IMVENDOR_ORIGIN,
        "referer": IMVENDOR_REFERER,
        "cookieorigin": IMVENDOR_ORIGIN,
        "user-agent": BROWSER_USER_AGENT,
        "cookie": cookie_header,
    }


def build_headers(cookie_header: str) -> dict[str, str]:
    return build_vbooking_headers(cookie_header)


def build_employee_body(
    cfg: AppConfig,
    start_date: str,
    end_date: str,
    page_no: int,
    page_size: int,
) -> dict[str, Any]:
    return {
        "metricList": ["sev_session_count", "avg_work_duration", "avg_efficiency"],
        "searchMap": {},
        "filterType": "",
        "orderColumn": "sev_session_count",
        "orderType": "desc",
        "butype": cfg.ctrip_im_butype,
        "consultationScene": cfg.ctrip_im_consultation_scene,
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": int(page_no),
        "pageSize": int(page_size),
        "productChannel": cfg.ctrip_im_product_channel,
        "currencyType": cfg.ctrip_im_currency_type,
    }


def build_session_body(
    cfg: AppConfig,
    account: CustomerServiceAccount,
    start_date: str,
    end_date: str,
    page_no: int,
    page_size: int,
) -> dict[str, Any]:
    return {
        "metricList": [],
        "searchMap": {
            "vendor_account_id": account.account_id,
            "vendor_account_name": account.account_name,
        },
        "orderColumn": "session_create_time",
        "orderType": "asc",
        "butype": cfg.ctrip_im_butype,
        "consultationScene": cfg.ctrip_im_consultation_scene,
        "startDate": start_date,
        "endDate": end_date,
        "pageNo": int(page_no),
        "pageSize": int(page_size),
        "productChannel": cfg.ctrip_im_product_channel,
    }


def _response_error(data: Any, text: str) -> str:
    if isinstance(data, dict):
        status = data.get("ResponseStatus") or {}
        errors = status.get("Errors") if isinstance(status, dict) else None
        if errors:
            return json.dumps(errors, ensure_ascii=False)
        for key in ("message", "Message", "errorMessage"):
            if data.get(key):
                return str(data[key])
    return text[:500]


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return max(0.0, seconds)


def _http_status_retryable(status_code: int) -> bool:
    return status_code == 429 or status_code in {500, 502, 503, 504}


def _backoff_seconds(attempt: int, retry_after_sec: float | None = None) -> float:
    if retry_after_sec is not None:
        return retry_after_sec
    return 0.5 * (2 ** max(0, attempt - 1))


class CtripImHttpClient:
    def __init__(
        self,
        cfg: AppConfig,
        log: Callable[[str], None] | None = None,
        request_interval_sec: float | None = None,
        session: requests.Session | None = None,
        request_budget: CtripRequestBudget | None = None,
    ):
        self.cfg = cfg
        self.log = log or (lambda _msg: None)
        self.cookie_header, self.auth_source = load_cookie_header(cfg)
        self.session = session or requests.Session()
        self.last_request_at = 0.0
        interval = cfg.ctrip_request_interval_sec if request_interval_sec is None else request_interval_sec
        self.request_interval_sec = max(0.0, float(interval))
        self.request_budget = request_budget

    def build_request_headers(self) -> dict[str, str]:
        return build_vbooking_headers(self.cookie_header)

    def post_json(self, url: str, body: dict[str, Any], timeout: int = 45) -> dict[str, Any]:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            elapsed = time.monotonic() - self.last_request_at
            if elapsed < self.request_interval_sec:
                time.sleep(self.request_interval_sec - elapsed)
            if self.request_budget:
                self.request_budget.consume(url)
            try:
                response = self.session.post(url, headers=self.build_request_headers(), json=body, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                self.last_request_at = time.monotonic()
                if attempt >= max_attempts:
                    raise CtripHttpError(f"携程接口网络失败：{exc}", retryable=True) from exc
                time.sleep(_backoff_seconds(attempt))
                continue
            finally:
                self.last_request_at = time.monotonic()

            text = response.text
            try:
                data: Any = response.json()
            except ValueError:
                data = None
            if not response.ok:
                retryable = _http_status_retryable(int(response.status_code))
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                message = f"携程接口请求失败：HTTP {response.status_code}，{_response_error(data, text)}"
                if retryable and attempt < max_attempts:
                    time.sleep(_backoff_seconds(attempt, retry_after))
                    continue
                raise CtripHttpError(
                    message,
                    status_code=int(response.status_code),
                    retryable=retryable,
                    retry_after_sec=retry_after,
                )
            if not isinstance(data, dict):
                raise RuntimeError(f"携程接口返回非 JSON：{text[:300]}")
            status = data.get("ResponseStatus")
            if isinstance(status, dict) and status.get("Ack") not in (None, "Success"):
                raise RuntimeError(f"携程接口返回失败：{_response_error(data, text)}")
            return data
        raise RuntimeError("携程接口请求重试状态异常")

    def list_customer_services(self, start_date: str, end_date: str, page_size: int = 100) -> list[CustomerServiceAccount]:
        accounts: list[CustomerServiceAccount] = []
        page_no = 1
        while True:
            body = build_employee_body(self.cfg, start_date, end_date, page_no, page_size)
            data = self.post_json(EMPLOYEE_URL, body)
            rows = data.get("tableDataItemList") or []
            total = int(data.get("totalNum") or len(rows) or 0)
            for row in rows:
                item = _merge_dim_metric(row)
                account_id = str(item.get("vendor_account_id") or item.get("vendor_account") or "").strip()
                account_name = str(item.get("vendor_account_name") or item.get("name") or "").strip()
                count = _first_int(item, ("session_cnt", "session_count", "consult_session_cnt", "im_session_cnt"))
                if account_id or account_name:
                    accounts.append(CustomerServiceAccount(account_id, account_name, count))
            if not rows or page_no * page_size >= total:
                break
            page_no += 1
        return accounts

    def list_sessions_for_account(
        self,
        account: CustomerServiceAccount,
        start_date: str,
        end_date: str,
        page_size: int = 100,
        max_pages: int = 50,
    ) -> list[SessionRecord]:
        sessions: list[SessionRecord] = []
        for page_no in range(1, max_pages + 1):
            body = build_session_body(self.cfg, account, start_date, end_date, page_no, page_size)
            data = self.post_json(SESSION_URL, body)
            rows = data.get("tableDataItemList") or []
            total = int(data.get("totalNum") or len(rows) or 0)
            for row in rows:
                item = _merge_dim_metric(row)
                session_id = str(item.get("session_id") or "").strip()
                if not session_id:
                    continue
                sessions.append(
                    SessionRecord(
                        session_id=session_id,
                        cs_name=account.display_name,
                        create_time=str(item.get("session_create_time") or ""),
                    ).normalized()
                )
            if not rows or page_no * page_size >= total:
                break
        return dedupe_sessions(sessions)

    def collect_sessions(
        self,
        start_date: str,
        end_date: str,
        page_size: int = 100,
        max_pages: int = 50,
        include_roles: set[str] | None = None,
    ) -> list[SessionRecord]:
        self.log(f"使用 Cookie 来源：{self.auth_source}")
        accounts = self.list_customer_services(start_date, end_date, page_size=page_size)
        if include_roles:
            accounts = [a for a in accounts if a.display_name in include_roles or a.account_id in include_roles or a.account_name in include_roles]
        self.log(f"找到 {len(accounts)} 位客服")
        all_sessions: list[SessionRecord] = []
        for account in accounts:
            self.log(f"请求客服 {account.display_name} 会话列表")
            all_sessions.extend(self.list_sessions_for_account(account, start_date, end_date, page_size=page_size, max_pages=max_pages))
        return dedupe_sessions(all_sessions)


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


def _read_json_url(url: str, timeout: float = 5.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _format_cdp_discovery_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:200]}"


class CtripImCdpFetchClient(CtripImHttpClient):
    """Run the same SOA requests through an already logged-in vbooking page."""

    def __init__(
        self,
        cfg: AppConfig,
        log: Callable[[str], None] | None = None,
        request_interval_sec: float | None = None,
        target_id: str | None = None,
        request_budget: CtripRequestBudget | None = None,
    ):
        self.cfg = cfg
        self.log = log or (lambda _msg: None)
        self.session = requests.Session()
        self.last_request_at = 0.0
        interval = cfg.ctrip_request_interval_sec if request_interval_sec is None else request_interval_sec
        self.request_interval_sec = max(0.0, float(interval))
        self.request_budget = request_budget
        self.cookie_header = ""
        self.auth_source = "cdp-page-fetch"
        self.proxy_base_url = str(cfg.cdp_proxy_base_url).rstrip("/")
        self.direct_cdp_base_url = f"http://127.0.0.1:{int(getattr(cfg, 'cdp_port', 9222) or 9222)}"
        self._cdp_eval_mode = "proxy"
        self._target_ws_url = ""
        self.target_id = target_id or self._find_vbooking_target()

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

    def post_json(self, url: str, body: dict[str, Any], timeout: int = 45) -> dict[str, Any]:
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < self.request_interval_sec:
            time.sleep(self.request_interval_sec - elapsed)
        if self.request_budget:
            self.request_budget.consume(url)
        script = f"""
(async () => {{
  const resp = await fetch({json.dumps(url)}, {{
    method: 'POST',
    credentials: 'include',
    headers: {{
      'accept': 'application/json, text/plain, */*',
      'content-type': 'application/json;charset=utf-8',
      'appname': 'vbkbusiness'
    }},
    body: JSON.stringify({json.dumps(body, ensure_ascii=False)})
  }});
  const text = await resp.text();
  let data = null;
  try {{ data = JSON.parse(text); }} catch (_error) {{}}
  return {{status: resp.status, ok: resp.ok, text, data}};
}})()
"""
        try:
            envelope = self._eval_script(script, timeout)
        finally:
            self.last_request_at = time.monotonic()
        result = envelope.get("value") if isinstance(envelope, dict) else None
        if not isinstance(result, dict):
            raise RuntimeError(f"CDP 页面请求返回异常：{str(envelope)[:500]}")
        if not result.get("ok"):
            data = result.get("data")
            raise RuntimeError(f"携程页面上下文请求失败：HTTP {result.get('status')}，{_response_error(data, str(result.get('text') or ''))}")
        data = result.get("data")
        if not isinstance(data, dict):
            raise RuntimeError(f"携程页面上下文返回非 JSON：{str(result.get('text') or '')[:300]}")
        status = data.get("ResponseStatus")
        if isinstance(status, dict) and status.get("Ack") not in (None, "Success"):
            raise RuntimeError(f"携程页面上下文返回失败：{_response_error(data, str(result.get('text') or ''))}")
        return data


def build_detail_body(cfg: AppConfig, session: SessionRecord, page_no: int, page_size: int) -> dict[str, Any]:
    body: dict[str, Any] = {
        "sessionId": session.session_id,
        "head": {
            "cver": "2",
            "extension": [
                {"name": "cpc", "value": "pc"},
                {"name": "protocal", "value": "https"},
                {"name": "amp-product-type", "value": "IM"},
                {"name": "amp-account-source", "value": "vbk"},
                {"name": "client-source", "value": ""},
                {"name": "locale", "value": "zh-CN"},
            ],
        },
    }
    extra = getattr(cfg, "ctrip_im_detail_extra_body", None)
    if isinstance(extra, dict):
        body.update(extra)
    return body


class CtripImDetailHttpClient(CtripImHttpClient):
    def build_request_headers(self) -> dict[str, str]:
        return build_imvendor_headers(self.cookie_header)

    def fetch_conversation(self, session: SessionRecord, page_size: int | None = None, max_pages: int = 50) -> dict[str, Any]:
        url = str(self.cfg.ctrip_im_detail_messages_url or "").strip()
        if not url:
            raise RuntimeError(
                "未配置携程详情消息接口 ctrip_im_detail_messages_url；"
                "请先通过真实详情页抓包确认消息接口，再写入 config.yaml"
            )
        validate_detail_messages_url(
            url,
            verified_source=str(getattr(self.cfg, "ctrip_im_detail_verified_source", "") or ""),
        )
        size = int(page_size or self.cfg.ctrip_im_detail_page_size or 100)
        messages: list[dict[str, Any]] = []
        for page_no in range(1, max_pages + 1):
            body = build_detail_body(self.cfg, session, page_no, size)
            data = self.post_json(url, body)
            page_messages = normalize_detail_messages(data, session)
            messages.extend(page_messages)
            if not _detail_has_next_page(data, page_no, size, len(page_messages), len(messages)):
                break
        return {
            "sessionId": session.session_id,
            "csName": session.cs_name,
            "detailUrl": session.detail_url,
            "title": "",
            "createTime": session.create_time,
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "messages": _renumber_messages(messages),
        }


def validate_detail_messages_url(url: str, verified_source: str = "") -> None:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"携程详情消息接口 URL 无效：{url}")
    path = parsed.path or ""
    for endpoint in KNOWN_NON_MESSAGE_DETAIL_ENDPOINTS:
        if endpoint in path:
            raise RuntimeError(
                "拒绝请求已知非消息详情接口："
                f"{url}；该接口只返回会话概览，不返回聊天消息。"
                "请先用浏览器 detail-xhr 发现真实消息列表接口，再执行纯 requests 导出"
            )
    host = parsed.hostname or ""
    if _is_ctrip_or_trip_host(host) and verified_source != "browser_detail_xhr":
        raise RuntimeError(
            "拒绝请求未经过浏览器 detail-xhr 验证的携程详情接口；"
            "请先用 `imx discover detail-xhr --output <报告>` 捕获真实详情页 XHR，"
            "再用 `imx discover apply-config --report <报告>` 写入验证配置"
        )


def _is_ctrip_or_trip_host(host: str) -> bool:
    host = str(host or "").lower()
    return host.endswith(".ctrip.com") or host == "ctrip.com" or host.endswith(".trip.com") or host == "trip.com"


def normalize_detail_messages(data: Any, session: SessionRecord) -> list[dict[str, Any]]:
    rows = _find_message_rows(data)
    messages: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        message_body = _first_str(row, ("messageBody",))
        text = _first_str(
            row,
            (
                "text",
                "content",
                "message",
                "messageBody",
                "msg",
                "msgContent",
                "messageContent",
                "contentText",
                "body",
            ),
        )
        inline_image = extract_inline_image_attachment(message_body)
        if inline_image is not None:
            text = "[图片]"
        sender_name = _first_str(row, ("senderName", "sendName", "fromName", "nickName", "userName", "name"))
        sender_role = _normalize_sender_role(_first_str(row, ("senderRole", "role", "senderType", "fromType", "source")))
        timestamp = _first_str(row, ("timestampText", "sendTime", "messageTime", "createTime", "time", "createdAt"))
        attachments = _extract_attachments(row)
        if inline_image is not None:
            attachments.append(inline_image)
        message_type = _normalize_message_type(_first_str(row, ("messageType", "msgType", "msgtype", "type")), text, attachments)
        messages.append(
            {
                "sessionId": session.session_id,
                "csName": session.cs_name,
                "detailUrl": session.detail_url,
                "sequence": index + 1,
                "timestampText": timestamp,
                "senderRole": sender_role,
                "senderName": sender_name,
                "messageType": message_type,
                "text": text,
                "rawHtml": _first_str(row, ("rawHtml", "html", "contentHtml")),
                "attachments": attachments,
            }
        )
    return messages


def _find_message_rows(data: Any) -> list[Any]:
    candidates: list[list[Any]] = []

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(node, list):
            if node and sum(1 for item in node if _looks_like_message(item)) >= max(1, len(node) // 2):
                candidates.append(node)
            for item in node:
                walk(item, depth + 1)
            return
        if isinstance(node, dict):
            for key in ("messages", "messageList", "msgList", "records", "list", "items", "dataList", "chatList"):
                value = node.get(key)
                if isinstance(value, list):
                    walk(value, depth + 1)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)

    walk(data)
    if not candidates:
        return []
    return max(candidates, key=len)


def _looks_like_message(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    keys = {str(k) for k in item}
    signal_keys = {
        "text",
        "content",
        "message",
        "messageBody",
        "msgContent",
        "messageContent",
        "sendTime",
        "messageTime",
        "senderName",
        "senderRole",
        "msgType",
        "messageType",
    }
    return bool(keys & signal_keys)


def _detail_has_next_page(data: Any, page_no: int, page_size: int, page_count: int, total_seen: int) -> bool:
    if page_count <= 0:
        return False
    if isinstance(data, dict):
        for key in ("hasNext", "hasMore", "has_next", "more"):
            if key in data:
                return bool(data.get(key))
        total = _first_int(data, ("total", "totalNum", "totalCount", "count"))
        if total:
            return total_seen < total
    return page_count >= page_size


def _renumber_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        item = dict(message)
        item["sequence"] = index
        out.append(item)
    return out


def _first_str(data: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _normalize_sender_role(value: str) -> str:
    text = value.lower()
    if any(x in text for x in ("seller", "service", "staff", "agent", "vendor", "客服", "商家")):
        return "seller"
    if any(x in text for x in ("buyer", "customer", "user", "guest", "客户", "用户", "游客")):
        return "buyer"
    if any(x in text for x in ("system", "notice", "系统")):
        return "system"
    return value or "unknown"


def _normalize_message_type(value: str, text: str, attachments: list[dict[str, str]]) -> str:
    lowered = value.lower()
    if any(x in lowered for x in ("image", "img", "pic")):
        return "image"
    if any(isinstance(att, dict) and att.get("source") == "messageBody" for att in attachments):
        return "image"
    if "card" in lowered or "order" in lowered:
        return "card"
    if text:
        return "text"
    return value or "unknown"


def _extract_attachments(row: dict[str, Any]) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    for key in ("attachments", "attachmentList", "images", "imageList", "files", "fileList"):
        value = row.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item:
                attachments.append({"src": item, "alt": ""})
            elif isinstance(item, dict):
                src = _first_str(item, ("src", "url", "href", "imageUrl", "fileUrl"))
                if src:
                    attachments.append({"src": src, "alt": _first_str(item, ("alt", "name", "fileName"))})
    for key in ("imageUrl", "imgUrl", "url"):
        value = str(row.get(key) or "").strip()
        if value and value.startswith(("http://", "https://", "data:")):
            attachments.append({"src": value, "alt": ""})
    return attachments


def _merge_dim_metric(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    merged: dict[str, Any] = {}
    for key in ("dimMap", "metricMap"):
        value = row.get(key)
        if isinstance(value, dict):
            merged.update(value)
    merged.update({k: v for k, v in row.items() if k not in {"dimMap", "metricMap"}})
    return merged


def _first_int(data: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        try:
            return int(float(str(value).replace(",", "")))
        except ValueError:
            continue
    return 0
