from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest
import yaml

from im_archive_cli.config import AppConfig
from im_archive_cli.detail_discovery import (
    DetailDiscoveryResult,
    EventCdpClient,
    _read_json_url,
    inspect_cdp_status,
    inspect_proxy_status,
    summarize_candidate_endpoints,
)
from im_archive_cli.imx_cli import cmd_discover_detail_xhr, main


class DummyLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)


def test_summarize_candidate_endpoints_prioritizes_message_like_samples() -> None:
    responses = [
        {
            "url": "https://m.ctrip.com/restapi/soa2/15529/queryIMSessionInfo",
            "status": 200,
            "bodySample": '{"imSessionInfoList":null,"count":null}',
        },
        {
            "url": "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
            "status": 200,
            "bodySample": '{"messageList":[{"msgContent":"hello","sendTime":"2026-06-18"}]}',
        },
    ]

    candidates = summarize_candidate_endpoints(responses)

    assert candidates[0]["url"].endswith("/queryMessageHistory")
    assert candidates[0]["looksLikeMessages"] is True
    assert candidates[1]["looksLikeMessages"] is False


def test_detail_discovery_result_recommends_config_from_message_candidate() -> None:
    result = DetailDiscoveryResult(
        detail_url="https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=s1",
        request_budget=10,
        used=1,
        blocked=False,
        requests=[],
        responses=[
            {
                "url": "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
                "status": 200,
                "bodySample": '{"messageList":[{"msgContent":"hello"}]}',
            }
        ],
    )

    payload = result.to_dict()

    assert payload["recommendedConfig"] == {
        "ctrip_im_detail_messages_url": "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
        "ctrip_im_detail_page_size": 100,
        "ctrip_im_detail_extra_body": None,
    }


def test_detail_discovery_result_reuses_stable_fields_from_captured_request_body() -> None:
    result = DetailDiscoveryResult(
        detail_url="https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=s1",
        request_budget=10,
        used=1,
        blocked=False,
        requests=[
            {
                "method": "POST",
                "url": "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
                "body": json.dumps(
                    {
                        "sessionId": "browser-session-id",
                        "accountsource": "vbk",
                        "pageNo": 1,
                        "pageSize": 50,
                        "source": "vendorWeb",
                        "locale": "zh-CN",
                    }
                ),
            }
        ],
        responses=[
            {
                "url": "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
                "status": 200,
                "bodySample": '{"messageList":[{"msgContent":"hello"}]}',
            }
        ],
    )

    payload = result.to_dict()

    assert payload["recommendedConfig"] == {
        "ctrip_im_detail_messages_url": "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
        "ctrip_im_detail_page_size": 50,
        "ctrip_im_detail_extra_body": {"source": "vendorWeb", "locale": "zh-CN"},
    }


def test_discover_detail_xhr_rejects_budget_over_30() -> None:
    with pytest.raises(RuntimeError, match="不能超过 30"):
        cmd_discover_detail_xhr(AppConfig(), DummyLogger(), "s1", request_budget=31, wait_sec=1)


def test_discover_detail_xhr_returns_nonzero_on_runtime_error(monkeypatch) -> None:
    from im_archive_cli import imx_cli

    def fail_discovery(*args, **kwargs):
        raise RuntimeError("CDP unavailable")

    logger = DummyLogger()
    monkeypatch.setattr(imx_cli, "discover_detail_xhr_via_cdp", fail_discovery)

    rc = cmd_discover_detail_xhr(AppConfig(), logger, "s1", request_budget=1, wait_sec=1)

    assert rc == 1
    assert any("CDP unavailable" in line for line in logger.lines)


def test_discover_detail_xhr_uses_cdp_base_url_override(monkeypatch) -> None:
    from im_archive_cli import imx_cli
    from im_archive_cli.detail_discovery import DetailDiscoveryResult

    captured = {}

    def fake_discovery(cdp_base_url, session_id, request_budget, wait_sec, log=None):
        captured["cdp_base_url"] = cdp_base_url
        captured["session_id"] = session_id
        return DetailDiscoveryResult(
            detail_url="https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=s1",
            request_budget=request_budget,
            used=0,
            blocked=False,
            requests=[],
            responses=[],
        )

    logger = DummyLogger()
    monkeypatch.setattr(imx_cli, "discover_detail_xhr_via_cdp", fake_discovery)

    rc = cmd_discover_detail_xhr(
        AppConfig(),
        logger,
        "s1",
        request_budget=0,
        wait_sec=1,
        cdp_base_url="http://127.0.0.1:9333/",
    )

    assert rc == 0
    assert captured == {"cdp_base_url": "http://127.0.0.1:9333", "session_id": "s1"}
    assert any("used=0, limit=0" in line for line in logger.lines)


def test_discover_detail_xhr_uses_request_ledger_remaining(monkeypatch, tmp_path) -> None:
    from im_archive_cli import imx_cli
    from im_archive_cli.detail_discovery import DetailDiscoveryResult

    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"limit": 5, "used": 3, "remaining": 2}), encoding="utf-8")
    captured = {}

    def fake_discovery(cdp_base_url, session_id, request_budget, wait_sec, log=None):
        captured["request_budget"] = request_budget
        return DetailDiscoveryResult(
            detail_url="https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=s1",
            request_budget=request_budget,
            used=2,
            blocked=False,
            requests=[],
            responses=[],
        )

    logger = DummyLogger()
    monkeypatch.setattr(imx_cli, "discover_detail_xhr_via_cdp", fake_discovery)

    rc = cmd_discover_detail_xhr(
        AppConfig(),
        logger,
        "s1",
        request_budget=5,
        wait_sec=1,
        request_ledger=str(ledger),
    )

    assert rc == 0
    assert captured["request_budget"] == 2
    assert json.loads(ledger.read_text(encoding="utf-8")) == {"limit": 5, "used": 5, "remaining": 0, "exceeded": False}


def test_discover_detail_xhr_stops_before_opening_page_when_ledger_exhausted(monkeypatch, tmp_path) -> None:
    from im_archive_cli import imx_cli

    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"limit": 5, "used": 5, "remaining": 0}), encoding="utf-8")
    called = {"discovery": False}

    def fake_discovery(*args, **kwargs):
        called["discovery"] = True
        raise AssertionError("discovery should not run when ledger is exhausted")

    monkeypatch.setattr(imx_cli, "discover_detail_xhr_via_cdp", fake_discovery)

    with pytest.raises(RuntimeError, match="剩余额度为 0"):
        cmd_discover_detail_xhr(
            AppConfig(),
            DummyLogger(),
            "s1",
            request_budget=5,
            wait_sec=1,
            request_ledger=str(ledger),
        )

    assert called["discovery"] is False


def test_event_cdp_client_preserves_events_seen_during_call(monkeypatch) -> None:
    from im_archive_cli import detail_discovery

    class FakeWebSocket:
        def __init__(self) -> None:
            self.timeout = 10
            self.sent: list[str] = []
            self.messages = [
                '{"method":"Network.responseReceived","params":{"requestId":"r1"}}',
                '{"id":1,"result":{"ok":true}}',
            ]

        def send(self, payload: str) -> None:
            self.sent.append(payload)

        def recv(self) -> str:
            return self.messages.pop(0)

        def gettimeout(self):
            return self.timeout

        def settimeout(self, timeout) -> None:
            self.timeout = timeout

        def close(self) -> None:
            pass

    fake_ws = FakeWebSocket()
    monkeypatch.setattr(detail_discovery.websocket, "create_connection", lambda *args, **kwargs: fake_ws)

    client = EventCdpClient("ws://example")

    assert client.call("Page.enable") == {"ok": True}
    assert client.recv() == {"method": "Network.responseReceived", "params": {"requestId": "r1"}}


def test_read_json_url_explains_non_cdp_port(monkeypatch) -> None:
    from im_archive_cli import detail_discovery

    def fake_urlopen(*args, **kwargs):
        raise HTTPError("http://127.0.0.1:9222/json/version", 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(detail_discovery.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError) as exc_info:
        _read_json_url("http://127.0.0.1:9222/json/version")

    text = str(exc_info.value)
    assert "返回 HTTP 404" in text
    assert "非 CDP 服务占用" in text
    assert "--cdp-base-url" in text


def test_apply_config_writes_recommended_detail_endpoint(tmp_path) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(AppConfig().__dict__, sort_keys=False, allow_unicode=True), encoding="utf-8")
    report_path = tmp_path / "detail_xhr_probe.json"
    report_path.write_text(
        json.dumps(
            {
                "candidateEndpoints": [
                    {
                        "url": "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
                        "statuses": [200],
                        "looksLikeMessages": True,
                        "samples": ['{"messageList":[{"msgContent":"hello"}]}'],
                    }
                ],
                "recommendedConfig": {
                    "ctrip_im_detail_messages_url": "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
                    "ctrip_im_detail_page_size": 200,
                    "ctrip_im_detail_extra_body": {"foo": "bar"},
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    rc = main(["--config", str(cfg_path), "discover", "apply-config", "--report", str(report_path)])

    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert rc == 0
    assert data["ctrip_im_detail_messages_url"] == "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory"
    assert data["ctrip_im_detail_page_size"] == 200
    assert data["ctrip_im_detail_extra_body"] == {"foo": "bar"}
    assert data["ctrip_im_detail_verified_source"] == "browser_detail_xhr"
    assert data["ctrip_im_detail_verified_at"]


def test_apply_config_rejects_report_without_candidate_evidence(tmp_path, capsys) -> None:
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(AppConfig().__dict__, sort_keys=False, allow_unicode=True), encoding="utf-8")
    report_path = tmp_path / "detail_xhr_probe.json"
    report_path.write_text(
        json.dumps(
            {
                "recommendedConfig": {
                    "ctrip_im_detail_messages_url": "https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
                    "ctrip_im_detail_page_size": 100,
                    "ctrip_im_detail_extra_body": None,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    rc = main(["--config", str(cfg_path), "discover", "apply-config", "--report", str(report_path)])

    captured = capsys.readouterr()
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert rc == 1
    assert "candidateEndpoints 证据" in captured.err
    assert data["ctrip_im_detail_messages_url"] == ""
    assert data["ctrip_im_detail_verified_source"] == ""


def test_inspect_cdp_status_counts_relevant_targets(monkeypatch) -> None:
    from im_archive_cli import detail_discovery

    def fake_read_json_url(url: str):
        if url.endswith("/json/version"):
            return {"Browser": "Chrome/124", "Protocol-Version": "1.3", "webSocketDebuggerUrl": "ws://browser"}
        if url.endswith("/json/list"):
            return [
                {
                    "id": "t1",
                    "type": "page",
                    "title": "IMExperience",
                    "url": "https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience",
                    "webSocketDebuggerUrl": "ws://t1",
                },
                {
                    "id": "t2",
                    "type": "page",
                    "title": "queryMessages",
                    "url": "https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=s1",
                    "webSocketDebuggerUrl": "ws://t2",
                },
            ]
        raise AssertionError(url)

    monkeypatch.setattr(detail_discovery, "_read_json_url", fake_read_json_url)

    status = inspect_cdp_status("http://127.0.0.1:9333/")

    assert status["cdpBaseUrl"] == "http://127.0.0.1:9333"
    assert status["browser"] == "Chrome/124"
    assert status["targetCount"] == 2
    assert status["vbookingTargetCount"] == 1
    assert status["detailTargetCount"] == 1
    assert status["readyForDetailDiscovery"] is True


def test_inspect_proxy_status_counts_relevant_targets(monkeypatch) -> None:
    from im_archive_cli import detail_discovery

    def fake_read_json_url(url: str):
        assert url == "http://localhost:3456/targets"
        return [
            {
                "targetId": "t1",
                "type": "page",
                "title": "IMExperience",
                "url": "https://vbooking.ctrip.com/micro/tour-bi-vendor-new/#/tour/quality/IMExperience",
                "attached": False,
                "pid": 123,
            },
            {
                "targetId": "t2",
                "type": "page",
                "title": "queryMessages",
                "url": "https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId=s1",
                "attached": False,
                "pid": 123,
            },
        ]

    monkeypatch.setattr(detail_discovery, "_read_json_url", fake_read_json_url)

    status = inspect_proxy_status("http://localhost:3456/")

    assert status["proxyBaseUrl"] == "http://localhost:3456"
    assert status["via"] == "proxy"
    assert status["targetCount"] == 2
    assert status["vbookingTargetCount"] == 1
    assert status["detailTargetCount"] == 1
    assert status["readyForPageContextCollect"] is True
    assert status["readyForDetailPageInspection"] is True
