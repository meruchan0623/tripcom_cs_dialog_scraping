from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from .config import AppConfig
from .models import SessionRecord
from .state import dedupe_sessions

EMPLOYEE_URL = "https://m.ctrip.com/restapi/soa2/13807/getEmployeeDimMetricDetailsV3"
SESSION_URL = "https://m.ctrip.com/restapi/soa2/13807/getSessionDimMetricDetailsV3"
REFERER = "https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience"


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


def build_headers(cookie_header: str) -> dict[str, str]:
    return {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json;charset=utf-8",
        "origin": "https://vbooking.ctrip.com",
        "referer": REFERER,
        "appname": "vbkbusiness",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        ),
        "cookie": cookie_header,
    }


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
        "filterType": "fail",
        "orderColumn": "sev_session_count",
        "orderType": "asc",
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


class CtripImHttpClient:
    def __init__(
        self,
        cfg: AppConfig,
        log: Callable[[str], None] | None = None,
        request_interval_sec: float = 0.2,
        session: requests.Session | None = None,
    ):
        self.cfg = cfg
        self.log = log or (lambda _msg: None)
        self.cookie_header, self.auth_source = load_cookie_header(cfg)
        self.session = session or requests.Session()
        self.last_request_at = 0.0
        self.request_interval_sec = max(0.0, float(request_interval_sec))

    def post_json(self, url: str, body: dict[str, Any], timeout: int = 45) -> dict[str, Any]:
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < self.request_interval_sec:
            time.sleep(self.request_interval_sec - elapsed)
        try:
            response = self.session.post(url, headers=build_headers(self.cookie_header), json=body, timeout=timeout)
        finally:
            self.last_request_at = time.monotonic()
        text = response.text
        try:
            data: Any = response.json()
        except ValueError:
            data = None
        if not response.ok:
            raise RuntimeError(f"携程接口请求失败：HTTP {response.status_code}，{_response_error(data, text)}")
        if not isinstance(data, dict):
            raise RuntimeError(f"携程接口返回非 JSON：{text[:300]}")
        status = data.get("ResponseStatus")
        if isinstance(status, dict) and status.get("Ack") not in (None, "Success"):
            raise RuntimeError(f"携程接口返回失败：{_response_error(data, text)}")
        return data

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


class CtripImCdpFetchClient(CtripImHttpClient):
    """Run the same SOA requests through an already logged-in vbooking page."""

    def __init__(
        self,
        cfg: AppConfig,
        log: Callable[[str], None] | None = None,
        request_interval_sec: float = 0.2,
        target_id: str | None = None,
    ):
        self.cfg = cfg
        self.log = log or (lambda _msg: None)
        self.session = requests.Session()
        self.last_request_at = 0.0
        self.request_interval_sec = max(0.0, float(request_interval_sec))
        self.cookie_header = ""
        self.auth_source = "cdp-page-fetch"
        self.proxy_base_url = str(cfg.cdp_proxy_base_url).rstrip("/")
        self.target_id = target_id or self._find_vbooking_target()

    def _find_vbooking_target(self) -> str:
        with urllib.request.urlopen(f"{self.proxy_base_url}/targets", timeout=5) as resp:  # noqa: S310
            targets = json.loads(resp.read().decode("utf-8"))
        for target in targets:
            url = str(target.get("url") or "")
            if "vbooking.ctrip.com" in url and "IMExperience" in url:
                return str(target["targetId"])
        for target in targets:
            url = str(target.get("url") or "")
            if "vbooking.ctrip.com" in url:
                return str(target["targetId"])
        raise RuntimeError("未找到已登录的 vbooking.ctrip.com 页面；请先在浏览器打开携程 IMExperience 页面")

    def post_json(self, url: str, body: dict[str, Any], timeout: int = 45) -> dict[str, Any]:
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < self.request_interval_sec:
            time.sleep(self.request_interval_sec - elapsed)
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
