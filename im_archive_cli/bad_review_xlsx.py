from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.cell.cell import Cell


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "、".join(_text(item) for item in value)
    return str(value)


def _append_text_row(ws, values: Iterable[Any]) -> None:
    ws.append([_text(value) for value in values])
    for cell in ws[ws.max_row]:
        _format_text_cell(cell)


def _format_text_cell(cell: Cell) -> None:
    if cell.value is not None:
        cell.value = str(cell.value)
        cell.data_type = "s"
        cell.number_format = "@"


def _build_workbook(report: dict[str, Any]) -> Workbook:
    wb = Workbook()
    summary = wb.active
    summary.title = "差评汇总"
    for key, value in (
        ("项目", "值"),
        ("开始日期", report.get("start_date")),
        ("结束日期", report.get("end_date")),
        ("总客服评价数", report.get("total_evaluations")),
        ("总差评数", report.get("total_bad_reviews")),
        ("差评率", f"{float(report.get('bad_review_rate') or 0) * 100:.2f}%" if report.get("bad_review_rate") is not None else ""),
        ("唯一会话数", report.get("unique_session_count")),
        ("客服数", report.get("employee_count")),
        ("抓取时间", report.get("fetched_at")),
        ("状态", report.get("status")),
        ("对账问题", report.get("reconciliation_errors") or []),
        ("落盘模式", "增量去重旁车标记" if (report.get("landing") or {}).get("enabled") else ""),
        ("新建落盘标记数", (report.get("landing") or {}).get("new_marker_count")),
        ("已有落盘标记数", (report.get("landing") or {}).get("existing_marker_count")),
        ("补充专门落盘数", (report.get("landing") or {}).get("special_landed_count")),
        ("未落盘数", (report.get("landing") or {}).get("not_landed_count")),
        ("失败数", len(report.get("failures") or [])),
    ):
        _append_text_row(summary, (key, value))

    employees = wb.create_sheet("客服差评统计")
    _append_text_row(employees, ("客服账号", "客服名", "差评数", "会话数", "状态"))
    for item in report.get("employees") or []:
        _append_text_row(
            employees,
            (item.get("account_id"), item.get("account_name"), item.get("bad_review_count"), item.get("session_count"), item.get("status")),
        )

    sessions = wb.create_sheet("差评会话")
    _append_text_row(sessions, ("会话ID", "客服账号", "客服名", "创建时间", "差评计数", "订单号", "聊天状态", "落盘标记状态", "归档/专门落盘路径", "标记文件"))
    for item in report.get("sessions") or []:
        _append_text_row(
            sessions,
            (
                item.get("session_id"), item.get("account_id"), item.get("account_name"), item.get("create_time"),
                item.get("bad_review_count"), item.get("order_numbers") or [], item.get("chat_status"),
                item.get("landing_status"), item.get("archive_paths") or [], item.get("marker_paths") or [],
            ),
        )

    chats = wb.create_sheet("聊天内容")
    _append_text_row(chats, ("会话ID", "订单号", "内容"))
    chat_map = report.get("chats") or {}
    for item in report.get("sessions") or []:
        session_id = _text(item.get("session_id"))
        _append_text_row(chats, (session_id, item.get("order_numbers") or [], ""))
        for message in chat_map.get(session_id) or []:
            sender = _text(message.get("sender_name"))
            role = _text(message.get("role"))
            role_identity = f"{role}（{sender}）" if sender else role
            _append_text_row(chats, (message.get("timestamp"), role_identity, message.get("text")))
        _append_text_row(chats, ("", "", ""))

    for ws in wb.worksheets:
        ws.freeze_panes = "A2"
        for column in ws.columns:
            letter = column[0].column_letter
            width = max((len(_text(cell.value)) for cell in column if cell.value is not None), default=8)
            ws.column_dimensions[letter].width = min(max(width + 2, 10), 80)
    return wb


def _summary_payload(report: dict[str, Any], summary_path: Path, xlsx_path: Path) -> dict[str, Any]:
    allowed = (
        "start_date", "end_date", "timezone", "fetched_at", "transport", "shared_state_used",
        "status", "total_evaluations", "total_bad_reviews", "bad_review_rate", "calculated_bad_review_rate",
        "session_row_count", "unique_session_count",
        "employee_count", "employee_api_total", "employees", "sessions", "failures", "reconciliation_errors",
        "duplicate_session_ids", "landing",
    )
    payload = {key: report.get(key) for key in allowed}
    payload["summary_path"] = str(summary_path.resolve())
    payload["xlsx_path"] = str(xlsx_path.resolve())
    return payload


def write_bad_review_outputs(run_dir: Path, report: dict[str, Any]) -> tuple[Path, Path]:
    run_dir = Path(run_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    run_key = f"{str(report.get('start_date') or '').replace('-', '')}-{str(report.get('end_date') or '').replace('-', '')}"
    xlsx_path = run_dir / f"bad_reviews_{run_key}.xlsx"
    summary_path = run_dir / "summary.json"
    workbook = _build_workbook(report)
    workbook.save(xlsx_path)
    summary_path.write_text(
        json.dumps(_summary_payload(report, summary_path, xlsx_path), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary_path, xlsx_path
