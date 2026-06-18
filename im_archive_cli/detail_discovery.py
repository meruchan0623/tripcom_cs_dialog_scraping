from __future__ import annotations

import json
import time
import urllib.request
from urllib.error import HTTPError, URLError
from dataclasses import dataclass
from typing import Any, Callable

import websocket

from .cdp_proxy_export import CdpProxyClient
from .models import DETAIL_BASE_URL


DISCOVERY_HELPER_JS = r"""
(() => {
  const STATE_KEY = "__IM_ARCHIVE_DETAIL_DISCOVERY__";
  if (window[STATE_KEY]) return true;
  const state = {
    budget: -1,
    used: 0,
    requests: [],
    responses: [],
    blocked: false
  };
  function isCtripApi(url) {
    const text = String(url || "");
    return /(?:ctrip|trip)\.com/i.test(text) && (
      /\/restapi\/soa2\//i.test(text) ||
      /imvendor\.ctrip\.com/i.test(text) ||
      /queryMessages/i.test(text)
    );
  }
  function consume(url) {
    if (!isCtripApi(url)) return;
    if (state.budget >= 0 && state.used + 1 > state.budget) {
      state.blocked = true;
      throw new Error(`IM detail discovery request budget exceeded: limit=${state.budget}, used=${state.used}, next=${url}`);
    }
    state.used += 1;
  }
  function recordRequest(method, url, body) {
    if (!isCtripApi(url)) return;
    state.requests.push({
      sequence: state.requests.length + 1,
      method: String(method || "GET").toUpperCase(),
      url: String(url || ""),
      body: body == null ? "" : String(body).slice(0, 4000),
      at: new Date().toISOString()
    });
  }
  function recordResponse(method, url, status, text) {
    if (!isCtripApi(url)) return;
    state.responses.push({
      sequence: state.responses.length + 1,
      method: String(method || "GET").toUpperCase(),
      url: String(url || ""),
      status: Number(status || 0),
      bodySample: String(text || "").slice(0, 4000),
      at: new Date().toISOString()
    });
  }
  const originalFetch = window.fetch;
  if (typeof originalFetch === "function") {
    window.fetch = async function(input, init = {}) {
      const url = typeof input === "string" ? input : input && input.url;
      const method = init.method || (input && input.method) || "GET";
      consume(url);
      recordRequest(method, url, init.body);
      const response = await originalFetch.apply(this, arguments);
      try {
        const text = await response.clone().text();
        recordResponse(method, url || response.url, response.status, text);
      } catch (_error) {}
      return response;
    };
  }
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__imArchiveDiscoveryMethod = method;
    this.__imArchiveDiscoveryUrl = url;
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function(body) {
    consume(this.__imArchiveDiscoveryUrl);
    recordRequest(this.__imArchiveDiscoveryMethod, this.__imArchiveDiscoveryUrl, body);
    this.addEventListener("loadend", function() {
      try {
        recordResponse(
          this.__imArchiveDiscoveryMethod,
          this.__imArchiveDiscoveryUrl || this.responseURL,
          this.status,
          this.responseText
        );
      } catch (_error) {}
    }, { once: true });
    return originalSend.apply(this, arguments);
  };
  window[STATE_KEY] = state;
  return true;
})()
"""


@dataclass
class DetailDiscoveryResult:
    detail_url: str
    request_budget: int
    used: int
    blocked: bool
    requests: list[dict[str, Any]]
    responses: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        candidates = summarize_candidate_endpoints(self.responses)
        return {
            "detailUrl": self.detail_url,
            "requestBudget": self.request_budget,
            "used": self.used,
            "blocked": self.blocked,
            "requests": self.requests,
            "responses": self.responses,
            "candidateEndpoints": candidates,
            "recommendedConfig": recommend_detail_config(candidates, self.requests),
        }


def summarize_candidate_endpoints(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: dict[str, dict[str, Any]] = {}
    for response in responses:
        url = str(response.get("url") or "")
        if "/restapi/soa2/" not in url:
            continue
        item = candidates.setdefault(url, {"url": url, "statuses": set(), "looksLikeMessages": False, "samples": []})
        item["statuses"].add(int(response.get("status") or 0))
        sample = str(response.get("bodySample") or "")
        if _sample_looks_like_messages(sample):
            item["looksLikeMessages"] = True
        if sample and len(item["samples"]) < 2:
            item["samples"].append(sample[:500])
    out: list[dict[str, Any]] = []
    for item in candidates.values():
        out.append(
            {
                "url": item["url"],
                "statuses": sorted(item["statuses"]),
                "looksLikeMessages": bool(item["looksLikeMessages"]),
                "samples": item["samples"],
            }
        )
    return sorted(out, key=lambda x: (not x["looksLikeMessages"], x["url"]))


def recommend_detail_config(candidates: list[dict[str, Any]], requests: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    for candidate in candidates:
        if candidate.get("looksLikeMessages") and candidate.get("url"):
            body_config = _derive_body_config(str(candidate["url"]), requests or [])
            return {
                "ctrip_im_detail_messages_url": candidate["url"],
                "ctrip_im_detail_page_size": body_config["page_size"],
                "ctrip_im_detail_extra_body": body_config["extra_body"],
            }
    return {}


def _derive_body_config(url: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
    page_size = 100
    extra_body: dict[str, Any] = {}
    for request in requests:
        if str(request.get("url") or "") != url:
            continue
        body = _parse_json_body(request.get("body"))
        if not isinstance(body, dict):
            continue
        raw_page_size = body.get("pageSize") or body.get("page_size") or body.get("limit")
        try:
            if raw_page_size:
                page_size = int(raw_page_size)
        except (TypeError, ValueError):
            page_size = 100
        for key, value in body.items():
            if key in _DYNAMIC_DETAIL_BODY_KEYS:
                continue
            extra_body[key] = value
        break
    return {"page_size": page_size, "extra_body": extra_body or None}


_DYNAMIC_DETAIL_BODY_KEYS = {
    "sessionId",
    "sessionID",
    "session_id",
    "imsessionid",
    "imSessionId",
    "accountsource",
    "accountSource",
    "pageNo",
    "pageNO",
    "page_no",
    "pageIndex",
    "page",
    "pageSize",
    "page_size",
    "limit",
    "offset",
}


def _parse_json_body(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def inspect_cdp_status(cdp_base_url: str) -> dict[str, Any]:
    base_url = str(cdp_base_url).rstrip("/")
    version = _read_json_url(f"{base_url}/json/version")
    targets = _read_json_url(f"{base_url}/json/list")
    if not isinstance(version, dict):
        raise RuntimeError(f"CDP version 返回非对象: {base_url}")
    if not isinstance(targets, list):
        raise RuntimeError(f"CDP target list 返回非数组: {base_url}")
    normalized_targets: list[dict[str, Any]] = []
    vbooking_targets = 0
    detail_targets = 0
    for target in targets:
        if not isinstance(target, dict):
            continue
        url = str(target.get("url") or "")
        if "vbooking.ctrip.com" in url:
            vbooking_targets += 1
        if "imvendor.ctrip.com" in url or "queryMessages" in url:
            detail_targets += 1
        normalized_targets.append(
            {
                "id": str(target.get("id") or target.get("targetId") or ""),
                "type": str(target.get("type") or ""),
                "title": str(target.get("title") or "")[:200],
                "url": url,
                "hasWebSocketDebuggerUrl": bool(target.get("webSocketDebuggerUrl")),
            }
        )
    return {
        "cdpBaseUrl": base_url,
        "via": "cdp",
        "browser": version.get("Browser") or version.get("browser") or "",
        "protocolVersion": version.get("Protocol-Version") or version.get("protocolVersion") or "",
        "hasBrowserWebSocket": bool(version.get("webSocketDebuggerUrl")),
        "targetCount": len(normalized_targets),
        "vbookingTargetCount": vbooking_targets,
        "detailTargetCount": detail_targets,
        "readyForDetailDiscovery": bool(version.get("webSocketDebuggerUrl")) and bool(normalized_targets),
        "targets": normalized_targets,
    }


def inspect_proxy_status(proxy_base_url: str) -> dict[str, Any]:
    base_url = str(proxy_base_url).rstrip("/")
    targets = _read_json_url(f"{base_url}/targets")
    if not isinstance(targets, list):
        raise RuntimeError(f"CDP Proxy targets 返回非数组: {base_url}")
    normalized_targets: list[dict[str, Any]] = []
    vbooking_targets = 0
    detail_targets = 0
    for target in targets:
        if not isinstance(target, dict):
            continue
        url = str(target.get("url") or "")
        if "vbooking.ctrip.com" in url:
            vbooking_targets += 1
        if "imvendor.ctrip.com" in url or "queryMessages" in url:
            detail_targets += 1
        normalized_targets.append(
            {
                "id": str(target.get("targetId") or target.get("id") or ""),
                "type": str(target.get("type") or ""),
                "title": str(target.get("title") or "")[:200],
                "url": url,
                "attached": bool(target.get("attached")),
                "pid": target.get("pid"),
            }
        )
    return {
        "proxyBaseUrl": base_url,
        "via": "proxy",
        "targetCount": len(normalized_targets),
        "vbookingTargetCount": vbooking_targets,
        "detailTargetCount": detail_targets,
        "readyForPageContextCollect": vbooking_targets > 0,
        "readyForDetailPageInspection": detail_targets > 0,
        "targets": normalized_targets,
    }


def discover_detail_xhr_via_proxy(
    proxy: CdpProxyClient,
    session_id: str,
    request_budget: int,
    wait_sec: float = 8.0,
    log: Callable[[str], None] | None = None,
) -> DetailDiscoveryResult:
    logger = log or (lambda _msg: None)
    detail_url = f"{DETAIL_BASE_URL}{session_id}"
    target_id = proxy.new_tab("about:blank")
    try:
        proxy.eval(target_id, DISCOVERY_HELPER_JS, timeout=30)
        proxy.eval(
            target_id,
            f"""
(() => {{
  const state = window.__IM_ARCHIVE_DETAIL_DISCOVERY__;
  state.budget = {int(request_budget)};
  state.used = 0;
  state.requests = [];
  state.responses = [];
  state.blocked = false;
  location.href = {json.dumps(detail_url)};
  return true;
}})()
""",
            timeout=30,
        )
        deadline = time.time() + max(0.5, float(wait_sec))
        while time.time() < deadline:
            time.sleep(0.5)
            state = _read_discovery_state(proxy, target_id)
            logger(f"detail discovery: used={state.get('used', 0)} requests={len(state.get('requests') or [])}")
            if state.get("blocked"):
                break
        state = _read_discovery_state(proxy, target_id)
        return DetailDiscoveryResult(
            detail_url=detail_url,
            request_budget=int(request_budget),
            used=int(state.get("used") or 0),
            blocked=bool(state.get("blocked")),
            requests=list(state.get("requests") or []),
            responses=list(state.get("responses") or []),
        )
    finally:
        proxy.close(target_id)


class EventCdpClient:
    def __init__(self, ws_url: str):
        self.ws = websocket.create_connection(ws_url, timeout=10)
        self.seq = 0
        self.pending: list[dict[str, Any]] = []

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass

    def send(self, method: str, params: dict[str, Any] | None = None) -> int:
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}, ensure_ascii=False))
        return self.seq

    def call(self, method: str, params: dict[str, Any] | None = None, timeout_sec: float = 10.0) -> dict[str, Any]:
        msg_id = self.send(method, params)
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            msg = self._pop_pending_response(msg_id)
            if msg is None:
                msg = self._recv_ws(timeout_sec=max(0.1, deadline - time.time()))
            if msg.get("id") != msg_id:
                self.pending.append(msg)
                continue
            if "error" in msg:
                raise RuntimeError(f"CDP error {method}: {msg['error']}")
            return dict(msg.get("result") or {})
        raise TimeoutError(f"CDP call timeout: {method}")

    def recv(self, timeout_sec: float = 1.0) -> dict[str, Any]:
        if self.pending:
            return self.pending.pop(0)
        return self._recv_ws(timeout_sec=timeout_sec)

    def _recv_ws(self, timeout_sec: float = 1.0) -> dict[str, Any]:
        old_timeout = self.ws.gettimeout()
        self.ws.settimeout(timeout_sec)
        try:
            raw = self.ws.recv()
        finally:
            self.ws.settimeout(old_timeout)
        return json.loads(raw)

    def _pop_pending_response(self, msg_id: int) -> dict[str, Any] | None:
        for index, msg in enumerate(self.pending):
            if msg.get("id") == msg_id:
                return self.pending.pop(index)
        return None


def discover_detail_xhr_via_cdp(
    cdp_base_url: str,
    session_id: str,
    request_budget: int,
    wait_sec: float = 8.0,
    log: Callable[[str], None] | None = None,
) -> DetailDiscoveryResult:
    logger = log or (lambda _msg: None)
    base_url = str(cdp_base_url).rstrip("/")
    detail_url = f"{DETAIL_BASE_URL}{session_id}"
    browser_ws = _read_json_url(f"{base_url}/json/version").get("webSocketDebuggerUrl")
    if not browser_ws:
        raise RuntimeError(f"CDP version 未返回 webSocketDebuggerUrl: {base_url}")

    browser = EventCdpClient(str(browser_ws))
    target_id = ""
    page: EventCdpClient | None = None
    try:
        target_id = str(browser.call("Target.createTarget", {"url": "about:blank"}).get("targetId") or "")
        if not target_id:
            raise RuntimeError("CDP createTarget 未返回 targetId")
        page_ws = _wait_for_target_ws(base_url, target_id)
        page = EventCdpClient(page_ws)
        page.call("Page.enable")
        page.call("Network.enable")
        page.call(
            "Fetch.enable",
            {
                "patterns": [
                    {"urlPattern": "*://*.ctrip.com/*", "requestStage": "Request"},
                    {"urlPattern": "*://*.trip.com/*", "requestStage": "Request"},
                    {"urlPattern": "*://imvendor.ctrip.com/*", "requestStage": "Request"},
                ]
            },
        )
        page.send("Page.navigate", {"url": detail_url})

        used = 0
        blocked = False
        requests: list[dict[str, Any]] = []
        responses_by_id: dict[str, dict[str, Any]] = {}
        responses: list[dict[str, Any]] = []
        deadline = time.time() + max(0.5, float(wait_sec))
        while time.time() < deadline:
            try:
                msg = page.recv(timeout_sec=0.5)
            except websocket.WebSocketTimeoutException:
                logger(f"detail discovery(cdp): used={used} requests={len(requests)}")
                continue
            method = msg.get("method")
            params = msg.get("params") or {}
            if method == "Fetch.requestPaused":
                request = params.get("request") or {}
                url = str(request.get("url") or "")
                fetch_id = str(params.get("requestId") or "")
                if _is_ctrip_detail_api(url):
                    if used + 1 > int(request_budget):
                        blocked = True
                        page.send("Fetch.failRequest", {"requestId": fetch_id, "errorReason": "BlockedByClient"})
                        logger(f"detail discovery(cdp): blocked next request after used={used}: {url}")
                        break
                    used += 1
                    requests.append(
                        {
                            "sequence": len(requests) + 1,
                            "method": str(request.get("method") or "GET").upper(),
                            "url": url,
                            "body": str(request.get("postData") or "")[:4000],
                            "at": _utc_now_iso(),
                        }
                    )
                page.send("Fetch.continueRequest", {"requestId": fetch_id})
            elif method == "Network.responseReceived":
                response = params.get("response") or {}
                url = str(response.get("url") or "")
                if _is_ctrip_detail_api(url):
                    responses_by_id[str(params.get("requestId") or "")] = {
                        "sequence": len(responses_by_id) + 1,
                        "method": "",
                        "url": url,
                        "status": int(response.get("status") or 0),
                        "bodySample": "",
                        "at": _utc_now_iso(),
                    }
            elif method == "Network.loadingFinished":
                request_id = str(params.get("requestId") or "")
                item = responses_by_id.pop(request_id, None)
                if item:
                    try:
                        body = page.call("Network.getResponseBody", {"requestId": request_id}, timeout_sec=3.0)
                        item["bodySample"] = str(body.get("body") or "")[:4000]
                    except Exception as exc:  # noqa: BLE001
                        item["bodyError"] = str(exc)
                    responses.append(item)
        return DetailDiscoveryResult(
            detail_url=detail_url,
            request_budget=int(request_budget),
            used=used,
            blocked=blocked,
            requests=requests,
            responses=responses + list(responses_by_id.values()),
        )
    finally:
        if page:
            page.close()
        if target_id:
            try:
                browser.call("Target.closeTarget", {"targetId": target_id}, timeout_sec=2.0)
            except Exception:  # noqa: BLE001
                pass
        browser.close()


def _read_discovery_state(proxy: CdpProxyClient, target_id: str) -> dict[str, Any]:
    state = proxy.eval(
        target_id,
        """
(() => {
  const state = window.__IM_ARCHIVE_DETAIL_DISCOVERY__ || {};
  return {
    budget: state.budget || 0,
    used: state.used || 0,
    blocked: Boolean(state.blocked),
    requests: state.requests || [],
    responses: state.responses || []
  };
})()
""",
        timeout=30,
    )
    return dict(state or {}) if isinstance(state, dict) else {}


def _sample_looks_like_messages(sample: str) -> bool:
    lowered = sample.lower()
    if not sample:
        return False
    message_keys = ("messagelist", "messages", "msglist", "chatlist", "msgcontent", "messagecontent", "sendtime")
    return any(key in lowered for key in message_keys)


def _read_json_url(url: str) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(
            f"CDP HTTP 端点不可用：{url} 返回 HTTP {exc.code}；"
            "请先运行 `imx chrome start --debug`。如果该端口已被非 CDP 服务占用，"
            "请修改 config.yaml 的 cdp_port 或用 `imx discover ... --cdp-base-url ...` 指向可用端点"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"CDP HTTP 端点不可用：{url}；请先运行 `imx chrome start --debug`。"
            "如果该端口已被非 CDP 服务占用，请修改 config.yaml 的 cdp_port 或用 "
            "`imx discover ... --cdp-base-url ...` 指向可用端点"
        ) from exc


def _wait_for_target_ws(cdp_base_url: str, target_id: str, timeout_sec: float = 5.0) -> str:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        targets = _read_json_url(f"{cdp_base_url}/json/list")
        if isinstance(targets, list):
            for target in targets:
                if str(target.get("id") or target.get("targetId") or "") == target_id and target.get("webSocketDebuggerUrl"):
                    return str(target["webSocketDebuggerUrl"])
        time.sleep(0.1)
    raise TimeoutError(f"等待 target websocket 超时: {target_id}")


def _is_ctrip_detail_api(url: str) -> bool:
    text = str(url or "")
    return bool(
        ("ctrip.com" in text or "trip.com" in text)
        and ("/restapi/soa2/" in text or "imvendor.ctrip.com" in text or "queryMessages" in text)
    )


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
