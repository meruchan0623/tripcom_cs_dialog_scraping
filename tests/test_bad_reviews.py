from __future__ import annotations

import json
from pathlib import Path

import pytest

from im_archive_cli.bad_reviews import collect_bad_reviews, extract_filtered_chat, mark_landed_bad_review_sessions, validate_run_options
from im_archive_cli.config import AppConfig
from im_archive_cli.models import SessionRecord
from im_archive_cli.ctrip_http import (
    BadReviewEmployee,
    BadReviewSession,
    CtripImDetailHttpClient,
    CtripImHttpClient,
    build_bad_review_employee_body,
    build_bad_review_metric_card_body,
    build_bad_review_session_body,
)


def make_cfg(tmp_path: Path) -> AppConfig:
    cookie = tmp_path / "cookie.txt"
    cookie.write_text("foo=secret", encoding="utf-8")
    return AppConfig(
        output_dir=str(tmp_path / "output"),
        state_file=str(tmp_path / "state.json"),
        ctrip_cookie_header_file=str(cookie),
        ctrip_auth_json=str(tmp_path / "missing.json"),
        ctrip_im_detail_messages_url="http://127.0.0.1/detail",
    )


def test_bad_review_payloads_match_browser_contract() -> None:
    cfg = AppConfig()
    employee = build_bad_review_employee_body(cfg, "2026-08-01", "2026-08-02", 2, 100)
    session = build_bad_review_session_body(
        cfg, BadReviewEmployee("vbk_1", "Alice", 3), "2026-08-01", "2026-08-02", 4, 100
    )
    assert employee["metricList"] == ["low_comment_cnt"]
    assert employee["searchMap"] == {}
    assert employee["filterType"] == "1"
    assert employee["orderColumn"] == "low_comment_cnt"
    assert employee["orderType"] == "desc"
    assert employee["pageNo"] == 2
    assert employee["currencyType"] == "CNY"
    assert session["searchMap"] == {
        "vendor_account_id": "vbk_1",
        "vendor_account_name": "Alice",
    }
    assert session["filterType"] == "0"
    assert session["orderColumn"] == "session_create_time"
    assert session["orderType"] == "asc"
    metric_card = build_bad_review_metric_card_body(cfg, "2026-08-01", "2026-08-02")
    assert metric_card["timeType"] == "0"
    assert metric_card["startDate"] == "2026-08-01"
    assert metric_card["endDate"] == "2026-08-02"


def test_http_paginates_and_rejects_truncation(tmp_path: Path) -> None:
    calls: list[int] = []

    class FakeClient(CtripImHttpClient):
        def post_json(self, url, body, timeout=45):
            calls.append(body["pageNo"])
            return {
                "totalNum": 3,
                "tableDataItemList": [
                    {
                        "dimMap": {
                            "vendor_account_id": f"a{body['pageNo']}",
                            "vendor_account_name": "A",
                        },
                        "metricMap": {"low_comment_cnt": 1},
                    }
                ],
            }

    client = FakeClient(make_cfg(tmp_path), request_interval_sec=0)
    agents, total = client.list_bad_review_customer_services(
        "2026-08-01", "2026-08-01", page_size=1, max_pages=3
    )
    assert total == 3
    assert [item.account_id for item in agents] == ["a1", "a2", "a3"]
    assert calls == [1, 2, 3]
    with pytest.raises(RuntimeError, match="分页未完成"):
        client.list_bad_review_customer_services(
            "2026-08-01", "2026-08-01", page_size=1, max_pages=2
        )


@pytest.mark.parametrize(
    ("start", "end", "size", "pages", "match"),
    [
        ("2026-8-1", "2026-08-01", 1, 1, "ISO"),
        ("2026-08-02", "2026-08-01", 1, 1, "不能晚于"),
        ("2026-08-01", "2026-08-01", 0, 1, "page-size"),
        ("2026-08-01", "2026-08-01", 1, 0, "max-pages"),
    ],
)
def test_validation(start, end, size, pages, match) -> None:
    with pytest.raises(RuntimeError, match=match):
        validate_run_options(start, end, size, pages)


def test_filtering_reads_top_level_action_ext_and_all_order_ids() -> None:
    conversation = {
        "messages": [
            {"action": "CBZ06", "timestampText": "1785557242246", "senderName": "_ti_guest", "text": "buyer"},
            {"action": "CBZ04", "timestampText": "2026-08-01T01:02:03Z", "senderName": "csm1", "text": "seller"},
            {"action": "CBZ07", "timestampText": "2026-08-01 10:00:00", "senderName": "csm1", "ext": {"answer": "fallback"}},
            {"action": "NBZ_CARD", "text": "订单 1234567890123456 和 0000000000000001"},
            {"action": "session_status", "text": "closed"},
            {"senderRole": "system", "text": "not visible"},
        ]
    }
    messages, orders = extract_filtered_chat(conversation)
    assert [item["text"] for item in messages] == ["buyer", "seller", "fallback"]
    assert [item["role"] for item in messages] == ["buyer", "seller", "seller"]
    assert orders == ["1234567890123456", "0000000000000001"]
    assert all(len(item["timestamp"]) == 16 for item in messages)


def test_collection_reconciles_and_marks_mismatch_partial() -> None:
    class Lists:
        def get_bad_review_period_metrics(self, *args, **kwargs):
            return {"total_evaluations": 4, "bad_review_rate": 0.5}

        def list_bad_review_customer_services(self, *args, **kwargs):
            return [BadReviewEmployee("a1", "Alice", 2)], 1

        def list_bad_review_sessions_for_account(self, *args, **kwargs):
            return [BadReviewSession("s1", "", 1), BadReviewSession("s2", "", 1)], 2

    class Detail:
        def fetch_conversation(self, session, max_pages=50):
            return {"messages": [{"action": "CBZ06", "senderName": "guest", "text": session.session_id}]}

    report = collect_bad_reviews(
        Lists(), Detail(), "2026-08-01", "2026-08-01", page_size=100, max_pages=2
    )
    assert report["status"] == "success"
    assert report["total_bad_reviews"] == report["session_row_count"] == report["unique_session_count"] == 2
    assert report["total_evaluations"] == 4
    assert report["bad_review_rate"] == report["calculated_bad_review_rate"] == 0.5
    assert report["shared_state_used"] is False

    class MismatchLists(Lists):
        def list_bad_review_sessions_for_account(self, *args, **kwargs):
            return [BadReviewSession("s1", "", 1)], 1

    mismatch = collect_bad_reviews(
        MismatchLists(), Detail(), "2026-08-01", "2026-08-01", no_chat=True
    )
    assert mismatch["status"] == "partial"
    assert mismatch["reconciliation_errors"]


def test_employee_api_failure_is_failed_not_zero() -> None:
    class Failed:
        def get_bad_review_period_metrics(self, *args, **kwargs):
            return {"total_evaluations": 0, "bad_review_rate": 0.0}

        def list_bad_review_customer_services(self, *args, **kwargs):
            raise RuntimeError("HTTP 403 Authorization=secret")

    report = collect_bad_reviews(Failed(), None, "2026-08-01", "2026-08-01", no_chat=True)
    assert report["status"] == "failed"
    assert report["failures"][0]["stage"] == "employee-list"
    assert "secret" not in report["failures"][0]["error"]


def test_detail_api_is_single_request_because_endpoint_has_no_paging_contract(tmp_path: Path) -> None:
    calls = []

    class FakeDetail(CtripImDetailHttpClient):
        def post_json(self, url, body, timeout=45):
            calls.append(body)
            return {
                "messages": [
                    {"action": "CBZ06", "text": "one", "senderName": "buyer"},
                    {"action": "CBZ04", "text": "two", "senderName": "seller"},
                ]
            }

    client = FakeDetail(make_cfg(tmp_path), request_interval_sec=0)
    result = client.fetch_conversation(SessionRecord("s1", "Alice", ""), page_size=1, max_pages=50)
    assert len(calls) == 1
    assert len(result["messages"]) == 2

def test_metric_card_requires_real_total_and_rate_fields(tmp_path: Path) -> None:
    class MetricClient(CtripImHttpClient):
        def post_json(self, url, body, timeout=45):
            return {
                "comment": [
                    {"metric": "session_evaluate_count", "values": {"non": 149.0}},
                    {"metric": "low_comment_rate", "values": {"non": 76 / 149}},
                ]
            }

    metrics = MetricClient(make_cfg(tmp_path), request_interval_sec=0).get_bad_review_period_metrics(
        "2026-08-01", "2026-08-31"
    )
    assert metrics["total_evaluations"] == 149
    assert metrics["bad_review_rate"] == pytest.approx(76 / 149)


def test_landing_marker_is_non_destructive_and_idempotent(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    source = archive_root / "20260801" / "Alice" / "IMChatlogExport_20260801120000_s1_Alice.json"
    source.parent.mkdir(parents=True)
    original = '{"sessionId":"s1","messages":[]}'
    source.write_text(original, encoding="utf-8")
    report = {
        "start_date": "2026-08-01",
        "end_date": "2026-08-01",
        "status": "success",
        "employees": [{"account_id": "a1"}],
        "sessions": [{"session_id": "s1"}],
        "raw_conversations": {"s1": {"sessionId": "s1", "messages": []}},
        "failures": [],
        "reconciliation_errors": [],
    }
    run_dir = tmp_path / "report"

    first = mark_landed_bad_review_sessions(report, archive_root, run_dir)
    second = mark_landed_bad_review_sessions(report, archive_root, run_dir)

    marker = Path(f"{source.resolve()}.bad-review.flag")
    assert source.read_text(encoding="utf-8") == original
    assert marker.is_file()
    assert json.loads(marker.read_text(encoding="utf-8"))["sessionId"] == "s1"
    assert first["new_marker_count"] == 1
    assert second["existing_marker_count"] == 1
    assert not list(source.parent.glob("【差评】*"))


def test_missing_original_is_specially_landed_and_marked(tmp_path: Path) -> None:
    report = {
        "start_date": "2026-08-01",
        "end_date": "2026-08-01",
        "status": "success",
        "employees": [{"account_id": "a1"}],
        "sessions": [{"session_id": "s2"}],
        "raw_conversations": {"s2": {"sessionId": "s2", "messages": [{"text": "hello"}]}},
        "failures": [],
        "reconciliation_errors": [],
    }
    run_dir = tmp_path / "report"
    counters = mark_landed_bad_review_sessions(report, tmp_path / "archive", run_dir)
    special = run_dir / "bad-review-conversations" / "s2.bad-review.json"
    assert special.is_file()
    assert Path(f"{special.resolve()}.bad-review.flag").is_file()
    assert counters["special_landed_count"] == 1
    assert report["sessions"][0]["landing_status"] == "special_landed"

def test_special_landing_conflict_is_never_overwritten(tmp_path: Path) -> None:
    run_dir = tmp_path / "report"
    special = run_dir / "bad-review-conversations" / "s3.bad-review.json"
    special.parent.mkdir(parents=True)
    original = '{"sessionId":"different","messages":[]}'
    special.write_text(original, encoding="utf-8")
    report = {
        "start_date": "2026-08-01",
        "end_date": "2026-08-01",
        "status": "success",
        "employees": [{"account_id": "a1"}],
        "sessions": [{"session_id": "s3"}],
        "raw_conversations": {"s3": {"sessionId": "s3", "messages": []}},
        "failures": [],
        "reconciliation_errors": [],
    }
    mark_landed_bad_review_sessions(report, tmp_path / "archive", run_dir)
    assert special.read_text(encoding="utf-8") == original
    assert report["status"] == "partial"
    assert any(item["stage"] == "special-landing" for item in report["failures"])
