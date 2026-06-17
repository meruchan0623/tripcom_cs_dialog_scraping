from __future__ import annotations

import pytest

from im_archive_cli.config import AppConfig
from im_archive_cli.ctrip_http import (
    CustomerServiceAccount,
    build_employee_body,
    build_session_body,
)


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


def test_build_employee_body_uses_cli_defaults() -> None:
    body = build_employee_body(AppConfig(), "2026-06-16", "2026-06-16", 2, 100)

    assert body["butype"] == "品类活动"
    assert body["consultationScene"] == "aggregate"
    assert body["productChannel"] == "aggregate"
    assert body["filterType"] == "fail"
    assert body["pageNo"] == 2
    assert body["pageSize"] == 100


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
