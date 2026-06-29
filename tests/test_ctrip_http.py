from __future__ import annotations

import pytest

from im_archive_cli.config import AppConfig
from im_archive_cli.ctrip_http import (
    CustomerServiceAccount,
    CtripImDetailHttpClient,
    CtripImHttpClient,
    CtripRequestBudget,
    CtripRequestBudgetExceeded,
    build_employee_body,
    build_detail_body,
    build_imvendor_headers,
    build_session_body,
    build_vbooking_headers,
    inspect_auth_sources,
    normalize_detail_messages,
)
from im_archive_cli.models import SessionRecord


def test_build_session_body_matches_captured_contract() -> None:
    cfg = AppConfig()
    account = CustomerServiceAccount("vbk_2538177", "门票活动旅游管家Sara")

    body = build_session_body(cfg, account, "2026-06-16", "2026-06-16", 1, 10)

    assert body == {
        "metricList": [],
        "searchMap": {
            "vendor_account_id": "vbk_2538177",
            "vendor_account_name": "门票活动旅游管家Sara",
        },
        "orderColumn": "session_create_time",
        "orderType": "asc",
        "butype": "品类活动",
        "consultationScene": "aggregate",
        "startDate": "2026-06-16",
        "endDate": "2026-06-16",
        "pageNo": 1,
        "pageSize": 10,
        "productChannel": "aggregate",
    }


def test_build_vbooking_headers_match_browser_contract() -> None:
    headers = build_vbooking_headers("foo=bar")

    assert headers["accept"] == "application/json, text/plain, */*"
    assert headers["content-type"] == "application/json;charset=UTF-8"
    assert headers["origin"] == "https://vbooking.ctrip.com"
    assert headers["referer"] == "https://vbooking.ctrip.com/"
    assert headers["appname"] == "vbkbusiness"
    assert headers["cookie"] == "foo=bar"
    assert "Edg/" in headers["user-agent"]


def test_build_imvendor_headers_match_browser_contract() -> None:
    headers = build_imvendor_headers("foo=bar")

    assert headers["accept"] == "application/json, text/plain, */*"
    assert headers["content-type"] == "application/json"
    assert headers["origin"] == "https://imvendor.ctrip.com"
    assert headers["referer"] == "https://imvendor.ctrip.com/"
    assert headers["cookieorigin"] == "https://imvendor.ctrip.com"
    assert headers["cookie"] == "foo=bar"


def test_build_employee_body_uses_cli_defaults() -> None:
    body = build_employee_body(AppConfig(), "2026-06-16", "2026-06-16", 2, 100)

    assert body["butype"] == "品类活动"
    assert body["consultationScene"] == "aggregate"
    assert body["productChannel"] == "aggregate"
    assert body["filterType"] == ""
    assert body["orderType"] == "desc"
    assert body["pageNo"] == 2
    assert body["pageSize"] == 100


def test_inspect_auth_sources_masks_cookie_values_and_selects_cookie_file(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    auth_json = tmp_path / "auth.json"
    cookie_file.write_text("foo=secret; bar=value", encoding="utf-8")
    auth_json.write_text(
        '{"cookieHeader":"baz=hidden","createdAt":"2026-06-18T00:00:00Z","source":"browser"}',
        encoding="utf-8",
    )
    cfg = AppConfig(ctrip_cookie_header_file=str(cookie_file), ctrip_auth_json=str(auth_json))

    status = inspect_auth_sources(cfg)
    text = str(status)

    assert status["selected"] == str(cookie_file)
    assert status["sources"][0]["cookieCount"] == 2
    assert status["sources"][0]["cookieNames"] == ["foo", "bar"]
    assert "secret" not in text
    assert "hidden" not in text


def test_inspect_auth_sources_falls_back_to_auth_json(tmp_path) -> None:
    auth_json = tmp_path / "auth.json"
    auth_json.write_text(
        '{"cookieHeader":"baz=hidden","createdAt":"2026-06-18T00:00:00Z","source":"browser"}',
        encoding="utf-8",
    )
    cfg = AppConfig(ctrip_cookie_header_file=str(tmp_path / "missing.txt"), ctrip_auth_json=str(auth_json))

    status = inspect_auth_sources(cfg)

    assert status["selected"] == str(auth_json)
    assert status["sources"][1]["usable"] is True
    assert status["sources"][1]["cookieNames"] == ["baz"]


def test_http_client_collects_sessions_from_stubbed_responses(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from im_archive_cli import ctrip_http

    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(ctrip_cookie_header_file=str(cookie_file), ctrip_auth_json=str(tmp_path / "missing.json"))

    class FakeClient(ctrip_http.CtripImHttpClient):
        def post_json(self, url: str, body: dict, timeout: int = 45) -> dict:
            if url == ctrip_http.EMPLOYEE_URL:
                return {
                    "ResponseStatus": {"Ack": "Success"},
                    "totalNum": 1,
                    "tableDataItemList": [
                        {
                            "dimMap": {"vendor_account_id": "vbk_1", "vendor_account_name": "Alice"},
                            "metricMap": {"session_cnt": 2},
                        }
                    ],
                }
            return {
                "ResponseStatus": {"Ack": "Success"},
                "totalNum": 2,
                "tableDataItemList": [
                    {"dimMap": {"session_id": "s1", "session_create_time": "2026-06-16 08:00:00"}},
                    {"dimMap": {"session_id": "s2", "session_create_time": "2026-06-16 09:00:00"}},
                ],
            }

    client = FakeClient(cfg)
    sessions = client.collect_sessions("2026-06-16", "2026-06-16")

    assert [s.session_id for s in sessions] == ["s1", "s2"]
    assert sessions[0].cs_name == "vbk_1/Alice"


def test_request_budget_stops_before_extra_http_request(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(ctrip_cookie_header_file=str(cookie_file), ctrip_auth_json=str(tmp_path / "missing.json"))

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"ResponseStatus":{"Ack":"Success"}}'

        def json(self) -> dict:
            return {"ResponseStatus": {"Ack": "Success"}}

    class FakeSession:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, *args, **kwargs) -> FakeResponse:
            self.calls += 1
            return FakeResponse()

    fake_session = FakeSession()
    client = CtripImHttpClient(cfg, session=fake_session, request_interval_sec=0, request_budget=CtripRequestBudget(1))

    client.post_json("https://m.ctrip.com/restapi/soa2/13807/one", {})

    with pytest.raises(CtripRequestBudgetExceeded):
        client.post_json("https://m.ctrip.com/restapi/soa2/13807/two", {})
    assert fake_session.calls == 1


def test_collect_http_client_posts_with_vbooking_headers(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(ctrip_cookie_header_file=str(cookie_file), ctrip_auth_json=str(tmp_path / "missing.json"))

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"ResponseStatus":{"Ack":"Success"}}'

        def json(self) -> dict:
            return {"ResponseStatus": {"Ack": "Success"}}

    class FakeSession:
        def __init__(self) -> None:
            self.kwargs = None

        def post(self, *args, **kwargs):
            self.kwargs = kwargs
            return FakeResponse()

    fake = FakeSession()
    client = CtripImHttpClient(cfg, session=fake, request_interval_sec=0)

    client.post_json("https://m.ctrip.com/restapi/soa2/13807/example", {})

    headers = fake.kwargs["headers"]
    assert headers["origin"] == "https://vbooking.ctrip.com"
    assert headers["referer"] == "https://vbooking.ctrip.com/"
    assert headers["appname"] == "vbkbusiness"
    assert headers["cookie"] == "foo=bar"


def test_detail_http_client_posts_with_imvendor_headers(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(
        ctrip_cookie_header_file=str(cookie_file),
        ctrip_auth_json=str(tmp_path / "missing.json"),
        ctrip_im_detail_messages_url="http://127.0.0.1/detail",
    )

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"ResponseStatus":{"Ack":"Success"},"messageList":[{"msgContent":"hello"}]}'

        def json(self) -> dict:
            return {"ResponseStatus": {"Ack": "Success"}, "messageList": [{"msgContent": "hello"}]}

    class FakeSession:
        def __init__(self) -> None:
            self.kwargs = None

        def post(self, *args, **kwargs):
            self.kwargs = kwargs
            return FakeResponse()

    fake = FakeSession()
    client = CtripImDetailHttpClient(cfg, session=fake, request_interval_sec=0)

    client.fetch_conversation(SessionRecord(session_id="s1", cs_name="Alice"))

    headers = fake.kwargs["headers"]
    assert headers["origin"] == "https://imvendor.ctrip.com"
    assert headers["referer"] == "https://imvendor.ctrip.com/"
    assert headers["cookieorigin"] == "https://imvendor.ctrip.com"
    assert headers["cookie"] == "foo=bar"


def test_request_budget_zero_blocks_first_http_request(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(ctrip_cookie_header_file=str(cookie_file), ctrip_auth_json=str(tmp_path / "missing.json"))

    class FakeSession:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("request should be blocked before session.post")

    fake_session = FakeSession()
    client = CtripImHttpClient(cfg, session=fake_session, request_interval_sec=0, request_budget=CtripRequestBudget(0))

    with pytest.raises(CtripRequestBudgetExceeded):
        client.post_json("https://m.ctrip.com/restapi/soa2/13807/blocked", {})
    assert fake_session.calls == 0


def test_request_budget_ledger_persists_used_count(tmp_path) -> None:
    ledger = tmp_path / "request-ledger.json"
    budget = CtripRequestBudget(3, ledger_path=ledger)

    budget.consume("one")
    budget.consume("two")

    restored = CtripRequestBudget(3, ledger_path=ledger)
    assert restored.used == 2
    assert restored.remaining == 1

    restored.consume("three")
    with pytest.raises(CtripRequestBudgetExceeded):
        restored.consume("four")

    data = __import__("json").loads(ledger.read_text(encoding="utf-8"))
    assert data == {"limit": 3, "used": 3, "remaining": 0, "exceeded": False}


def test_request_budget_marks_ledger_that_already_exceeded_limit(tmp_path) -> None:
    ledger = tmp_path / "request-ledger.json"
    ledger.write_text('{"limit": 3, "used": 4, "remaining": 0}', encoding="utf-8")

    budget = CtripRequestBudget(3, ledger_path=ledger)

    assert budget.used == 4
    assert budget.remaining == 0
    assert budget.exceeded is True


def test_build_detail_body_uses_session_contract() -> None:
    session = SessionRecord(session_id="s1", cs_name="Alice")

    body = build_detail_body(AppConfig(), session, 2, 50)

    assert body == {
        "sessionId": "s1",
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


def test_normalize_detail_messages_finds_nested_message_rows() -> None:
    session = SessionRecord(session_id="s1", cs_name="Alice")
    payload = {
        "ResponseStatus": {"Ack": "Success"},
        "data": {
            "messageList": [
                {"msgContent": "hello", "sendTime": "2026-06-16 09:00:00", "senderType": "customer", "sendName": "Guest"},
                {"content": "hi", "messageTime": "2026-06-16 09:00:02", "senderRole": "service", "senderName": "Alice"},
            ]
        },
    }

    messages = normalize_detail_messages(payload, session)

    assert [m["text"] for m in messages] == ["hello", "hi"]
    assert [m["senderRole"] for m in messages] == ["buyer", "seller"]
    assert messages[0]["sessionId"] == "s1"


def test_detail_http_client_requires_captured_endpoint(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(
        ctrip_cookie_header_file=str(cookie_file),
        ctrip_auth_json=str(tmp_path / "missing.json"),
        ctrip_im_detail_messages_url="",
    )
    client = CtripImDetailHttpClient(cfg, request_interval_sec=0)

    with pytest.raises(RuntimeError, match="ctrip_im_detail_messages_url"):
        client.fetch_conversation(SessionRecord(session_id="s1", cs_name="Alice"))


def test_detail_http_client_rejects_known_non_message_endpoint_before_request(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(
        ctrip_cookie_header_file=str(cookie_file),
        ctrip_auth_json=str(tmp_path / "missing.json"),
        ctrip_im_detail_messages_url="https://m.ctrip.com/restapi/soa2/15529/queryIMSessionInfo",
    )
    budget = CtripRequestBudget(1)

    class GuardedSession:
        def post(self, *args, **kwargs):
            raise AssertionError("known wrong endpoint must be rejected before HTTP request")

    client = CtripImDetailHttpClient(cfg, session=GuardedSession(), request_interval_sec=0, request_budget=budget)

    with pytest.raises(RuntimeError, match="已知非消息详情接口"):
        client.fetch_conversation(SessionRecord(session_id="s1", cs_name="Alice"))

    assert budget.used == 0


def test_detail_http_client_rejects_unverified_ctrip_endpoint_before_request(tmp_path) -> None:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=bar", encoding="utf-8")
    cfg = AppConfig(
        ctrip_cookie_header_file=str(cookie_file),
        ctrip_auth_json=str(tmp_path / "missing.json"),
        ctrip_im_detail_messages_url="https://m.ctrip.com/restapi/soa2/15529/queryMessageHistory",
    )
    budget = CtripRequestBudget(1)

    class GuardedSession:
        def post(self, *args, **kwargs):
            raise AssertionError("unverified ctrip endpoint must be rejected before HTTP request")

    client = CtripImDetailHttpClient(cfg, session=GuardedSession(), request_interval_sec=0, request_budget=budget)

    with pytest.raises(RuntimeError, match="未经过浏览器 detail-xhr 验证"):
        client.fetch_conversation(SessionRecord(session_id="s1", cs_name="Alice"))

    assert budget.used == 0
