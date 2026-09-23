from __future__ import annotations

import json
from pathlib import Path

from openpyxl import load_workbook

from im_archive_cli.bad_review_xlsx import write_bad_review_outputs


def sample_report() -> dict:
    return {
        "start_date": "2026-08-01",
        "end_date": "2026-08-01",
        "fetched_at": "2026-09-01T00:00:00+00:00",
        "status": "success",
        "total_evaluations": 4,
        "total_bad_reviews": 1,
        "bad_review_rate": 0.25,
        "calculated_bad_review_rate": 0.25,
        "unique_session_count": 1,
        "employee_count": 1,
        "employee_api_total": 1,
        "employees": [{"account_id": "000123", "account_name": "Alice", "bad_review_count": 1, "session_count": 1, "status": "ok"}],
        "sessions": [{"session_id": "0012345678901234", "account_id": "000123", "account_name": "Alice", "create_time": "2026-08-01 08:00:00", "bad_review_count": 1, "order_numbers": ["1234567890123456"], "chat_status": "success", "landing_status": "archive_marked", "archive_paths": ["/tmp/source.json"], "marker_paths": ["/tmp/source.json.bad-review.flag"]}],
        "chats": {"0012345678901234": [{"timestamp": "2026-08-01 16:00", "role": "buyer", "sender_name": "000045", "text": "需要退款"}]},
        "failures": [],
        "reconciliation_errors": [],
        "duplicate_session_ids": [],
        "landing": {"enabled": True, "new_marker_count": 1, "existing_marker_count": 0, "special_landed_count": 0, "not_landed_count": 0},
    }


def test_native_xlsx_has_required_sheets_vertical_chat_sections_and_text_cells(tmp_path: Path) -> None:
    report = sample_report()
    summary_path, xlsx_path = write_bad_review_outputs(tmp_path, report)

    assert summary_path.name == "summary.json"
    assert xlsx_path.suffix == ".xlsx"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert "chats" not in summary
    assert summary["xlsx_path"] == str(xlsx_path.resolve())
    assert summary["total_evaluations"] == 4
    assert summary["bad_review_rate"] == 0.25

    wb = load_workbook(xlsx_path, data_only=False)
    assert wb.sheetnames == ["差评汇总", "客服差评统计", "差评会话", "聊天内容"]
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    assert cell.data_type == "s"
                    assert cell.number_format == "@"
    chat = wb["聊天内容"]
    assert chat["A1"].value == "会话ID"
    assert chat["B1"].value == "订单号"
    assert chat["A2"].value == "0012345678901234"
    assert chat["B2"].value == "1234567890123456"
    assert chat["A3"].value == "2026-08-01 16:00"
    assert chat["B3"].value == "buyer（000045）"
    assert chat["C3"].value == "需要退款"
    wb.close()
