from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo


_CST = ZoneInfo("Asia/Shanghai")
_ORDER_RE_16 = re.compile(r"(?<!\d)(\d{16})(?!\d)")
_REDACT_RE = re.compile(r"(?i)\b(cookie|authorization|cticket|token|password|secret|sid)\b\s*[:=]\s*[^\s,;]+")

_CBZ_BUYER = frozenset({"CBZ06"})
_CBZ_SELLER = frozenset({"CBZ04", "CBZ07", "CBZ08"})
_EXCLUDE_PREFIXES = ("NBZ", "NHT", "CTL")


def redact_error(text: str) -> str:
    """Strip credential values from error strings (keeps key names)."""
    return _REDACT_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", str(text or ""))[:240]


def validate_run_options(start_date: str, end_date: str, page_size: int, max_pages: int) -> None:
    """Validate date range and pagination options; raise RuntimeError with descriptive message."""
    for label, value in (("start-date", start_date), ("end-date", end_date)):
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise RuntimeError(
                f"{label} 格式必须为 ISO 8601 (YYYY-MM-DD)，当前值: {value!r}"
            )
        if parsed.strftime("%Y-%m-%d") != value:
            raise RuntimeError(
                f"{label} 格式必须为 ISO 8601 (YYYY-MM-DD)，当前值: {value!r}"
            )
    if start_date > end_date:
        raise RuntimeError(
            f"start-date ({start_date}) 不能晚于 end-date ({end_date})"
        )
    if int(page_size) <= 0:
        raise RuntimeError(f"page-size 必须大于 0，当前: {page_size}")
    if int(max_pages) <= 0:
        raise RuntimeError(f"max-pages 必须大于 0，当前: {max_pages}")


def _parse_ts_cst(ts: str) -> str:
    """Parse various timestamp formats to 'YYYY-MM-DD HH:MM' Beijing time."""
    if not ts:
        return ""
    # ISO 8601 with Z suffix
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(ts, fmt).replace(tzinfo=timezone.utc)
            return dt.astimezone(_CST).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    # ISO 8601 with offset
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_CST)
        return dt.astimezone(_CST).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        pass
    # Human-readable without tz → assume CST
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(ts, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    # Pure digits → unix ms
    if re.fullmatch(r"\d+", ts):
        try:
            dt = datetime.fromtimestamp(int(ts) / 1000.0, tz=timezone.utc)
            return dt.astimezone(_CST).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            pass
    return ts[:16] if len(ts) >= 16 else ts


def _try_json(text: str) -> dict | None:
    stripped = str(text or "").strip()
    if not stripped.startswith("{"):
        return None
    try:
        val = json.loads(stripped)
        return val if isinstance(val, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _cbz_text(action: str, payload: dict) -> str:
    for key in ("text", "content", "title", "msg", "message"):
        v = payload.get(key)
        if v and isinstance(v, str) and v.strip():
            return v.strip()
    ext = payload.get("ext")
    if isinstance(ext, dict):
        for key in ("answer", "content", "text"):
            v = ext.get(key)
            if v and isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _should_exclude(action: str, payload: dict | None) -> bool:
    if not action:
        return False
    upper = action.upper()
    normalized = re.sub(r"[^A-Z0-9]", "", upper)
    if normalized in {"SESSIONSTATUS", "COMEIN", "GOOUT", "SENDRECEIPT", "RECEIVERECEIPT", "READRECEIPT", "DELIVERYRECEIPT", "ACK", "RECEIPT"}:
        return True
    if upper.startswith(_EXCLUDE_PREFIXES):
        return True
    if upper == "CBZ07" and isinstance(payload, dict):
        return str(payload.get("event") or "").lower() in {"receipt", "ack"}
    return False


def extract_filtered_chat(conversation: dict) -> tuple[list[dict], list[str]]:
    """Filter to human buyer/seller messages and extract every 16-digit order ID."""
    raw_source = json.dumps(conversation, ensure_ascii=False, default=str)
    order_numbers = list(dict.fromkeys(_ORDER_RE_16.findall(raw_source)))
    filtered: list[dict] = []
    for msg in (conversation.get("messages") or []):
        if not isinstance(msg, dict):
            continue
        raw_text = str(msg.get("text") or msg.get("content") or "")
        payload = _try_json(raw_text)
        action = str(
            msg.get("action")
            or msg.get("rawAction")
            or msg.get("messageAction")
            or (payload or {}).get("action")
            or ""
        ).strip()
        if _should_exclude(action, payload):
            continue
        upper = action.upper()
        if upper in _CBZ_BUYER:
            role = "buyer"
        elif upper in _CBZ_SELLER:
            role = "seller"
        elif action:
            continue
        else:
            role = str(msg.get("senderRole") or "").lower()
            if role not in {"buyer", "seller"}:
                continue
        if payload is not None:
            display = _cbz_text(action, payload)
        else:
            display = raw_text.strip()
        if not display:
            ext = msg.get("ext")
            if isinstance(ext, dict):
                display = str(ext.get("answer") or ext.get("content") or ext.get("text") or "").strip()
        if not display:
            continue
        sender_name = str(msg.get("senderName") or msg.get("subject") or "").strip()
        if not sender_name:
            sender_name = str(msg.get("fromJid") or "").rsplit("/", 1)[-1].strip()
        filtered.append({
            "timestamp": _parse_ts_cst(str(msg.get("timestampText") or msg.get("createTime") or msg.get("sendTime") or "")),
            "role": role,
            "sender_name": sender_name,
            "text": display,
        })
    return filtered, order_numbers


def _empty_report(start_date: str, end_date: str) -> dict[str, Any]:
    return {
        "start_date": start_date,
        "end_date": end_date,
        "timezone": "Asia/Shanghai",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "transport": "http",
        "shared_state_used": False,
        "status": "failed",
        "total_bad_reviews": 0,
        "session_row_count": 0,
        "unique_session_count": 0,
        "employee_count": 0,
        "employee_api_total": 0,
        "total_evaluations": None,
        "bad_review_rate": None,
        "calculated_bad_review_rate": None,
        "employees": [],
        "sessions": [],
        "chats": {},
        "failures": [],
        "reconciliation_errors": [],
        "duplicate_session_ids": [],
        "landing": {
            "enabled": False,
            "mode": "incremental_deduplicated_sidecar",
            "new_marker_count": 0,
            "existing_marker_count": 0,
            "special_landed_count": 0,
            "not_landed_count": 0,
        },
    }


def collect_bad_reviews(
    list_client: Any,
    detail_client: Any,
    start_date: str,
    end_date: str,
    *,
    page_size: int = 100,
    max_pages: int = 50,
    no_chat: bool = False,
) -> dict[str, Any]:
    """Collect one date range through requests-only list/detail clients."""
    from .models import SessionRecord

    validate_run_options(start_date, end_date, page_size, max_pages)
    report = _empty_report(start_date, end_date)
    try:
        period_metrics = list_client.get_bad_review_period_metrics(start_date, end_date)
        report["total_evaluations"] = int(period_metrics["total_evaluations"])
        report["bad_review_rate"] = float(period_metrics["bad_review_rate"])
    except Exception as exc:  # noqa: BLE001
        report["failures"].append({"stage": "period-metrics", "error": redact_error(str(exc))})

    try:
        employees, employee_api_total = list_client.list_bad_review_customer_services(
            start_date, end_date, page_size=page_size, max_pages=max_pages
        )
    except Exception as exc:  # noqa: BLE001
        report["failures"].append({"stage": "employee-list", "error": redact_error(str(exc))})
        return report

    report["employee_api_total"] = employee_api_total
    report["employee_count"] = len(employees)
    if employee_api_total != len(employees):
        report["reconciliation_errors"].append(
            f"客服接口 totalNum ({employee_api_total}) 与去重客服数 ({len(employees)}) 不一致"
        )

    sessions_by_id: dict[str, Any] = {}
    owners_by_session: dict[str, list[str]] = {}
    employee_rows: list[dict[str, Any]] = []
    session_metric_total = 0
    session_row_count = 0

    for employee in employees:
        key = employee.account_id or employee.account_name
        try:
            sessions, reported_total = list_client.list_bad_review_sessions_for_account(
                employee, start_date, end_date, page_size=page_size, max_pages=max_pages
            )
        except Exception as exc:  # noqa: BLE001
            report["failures"].append(
                {"stage": "session-list", "employee_id": employee.account_id, "error": redact_error(str(exc))}
            )
            sessions, reported_total = [], 0
        session_count = len(sessions)
        low_comment_total = sum(int(item.bad_review_count) for item in sessions)
        session_row_count += session_count
        session_metric_total += low_comment_total
        matched = employee.bad_review_count == reported_total == session_count == low_comment_total
        if not matched:
            report["reconciliation_errors"].append(
                f"客服 {key} 对账不一致：employee={employee.bad_review_count}, reported={reported_total}, "
                f"sessions={session_count}, metrics={low_comment_total}"
            )
        employee_rows.append({
            "account_id": employee.account_id,
            "account_name": employee.account_name,
            "bad_review_count": employee.bad_review_count,
            "session_count": session_count,
            "reported_session_total": reported_total,
            "status": "ok" if matched else "mismatch",
        })
        for session in sessions:
            sessions_by_id.setdefault(session.session_id, session)
            owners_by_session.setdefault(session.session_id, []).append(key)

    duplicate_session_ids = sorted(sid for sid, owners in owners_by_session.items() if len(owners) > 1)
    if duplicate_session_ids:
        report["reconciliation_errors"].append(f"跨客服重复 sessionId：{len(duplicate_session_ids)} 个")

    total_bad_reviews = sum(int(employee.bad_review_count) for employee in employees)
    total_evaluations = report.get("total_evaluations")
    if isinstance(total_evaluations, int):
        calculated_rate = (total_bad_reviews / total_evaluations) if total_evaluations else 0.0
        report["calculated_bad_review_rate"] = calculated_rate
        api_rate = report.get("bad_review_rate")
        if total_bad_reviews > total_evaluations:
            report["reconciliation_errors"].append(
                f"差评数 ({total_bad_reviews}) 大于总客服评价数 ({total_evaluations})"
            )
        if isinstance(api_rate, (int, float)) and abs(float(api_rate) - calculated_rate) > 1e-6:
            report["reconciliation_errors"].append(
                f"差评率对账不一致：api={float(api_rate):.8f}, calculated={calculated_rate:.8f}"
            )
    unique_sessions = list(sessions_by_id.values())
    if not (total_bad_reviews == session_row_count == session_metric_total == len(unique_sessions)):
        report["reconciliation_errors"].append(
            f"全局对账不一致：employee={total_bad_reviews}, rows={session_row_count}, "
            f"metrics={session_metric_total}, unique={len(unique_sessions)}"
        )

    account_by_session: dict[str, tuple[str, str]] = {}
    employee_map = {employee.account_id or employee.account_name: employee for employee in employees}
    for session_id, owners in owners_by_session.items():
        if owners:
            employee = employee_map[owners[0]]
            account_by_session[session_id] = (employee.account_id, employee.account_name)

    chats: dict[str, list[dict]] = {}
    raw_conversations: dict[str, dict[str, Any]] = {}
    orders: dict[str, list[str]] = {}
    detail_failed: set[str] = set()
    if not no_chat:
        for session in unique_sessions:
            account_id, account_name = account_by_session.get(session.session_id, ("", ""))
            try:
                conversation = detail_client.fetch_conversation(
                    SessionRecord(session_id=session.session_id, cs_name=f"{account_id}/{account_name}".strip("/"), create_time=session.create_time),
                    max_pages=max_pages,
                )
                raw_conversations[session.session_id] = conversation
                chats[session.session_id], orders[session.session_id] = extract_filtered_chat(conversation)
            except Exception as exc:  # noqa: BLE001
                detail_failed.add(session.session_id)
                report["failures"].append(
                    {"stage": "conversation-detail", "session_id": session.session_id, "error": redact_error(str(exc))}
                )

    sessions_out = []
    for session in unique_sessions:
        account_id, account_name = account_by_session.get(session.session_id, ("", ""))
        sessions_out.append({
            "session_id": session.session_id,
            "account_id": account_id,
            "account_name": account_name,
            "create_time": session.create_time,
            "bad_review_count": session.bad_review_count,
            "order_numbers": orders.get(session.session_id, []),
            "chat_status": "skipped" if no_chat else "failed" if session.session_id in detail_failed else "success",
        })

    report.update({
        "total_bad_reviews": total_bad_reviews,
        "session_row_count": session_row_count,
        "unique_session_count": len(unique_sessions),
        "employees": employee_rows,
        "sessions": sessions_out,
        "chats": chats,
        "raw_conversations": raw_conversations,
        "duplicate_session_ids": duplicate_session_ids,
    })
    report["status"] = "partial" if report["failures"] or report["reconciliation_errors"] else "success"
    return report


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp-{uuid4().hex}")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def mark_landed_bad_review_sessions(report: dict[str, Any], archive_root: Path, run_dir: Path) -> dict[str, int]:
    """Mark existing archive JSONs with non-JSON sidecars; specially land missing ones."""
    archive_root = Path(archive_root).expanduser().resolve()
    run_dir = Path(run_dir).expanduser().resolve()
    sessions = report.get("sessions") or []
    targets = {str(item.get("session_id") or "").strip() for item in sessions}
    targets.discard("")
    found: dict[str, list[Path]] = {session_id: [] for session_id in targets}

    start = datetime.strptime(str(report["start_date"]), "%Y-%m-%d").date()
    end = datetime.strptime(str(report["end_date"]), "%Y-%m-%d").date()
    cursor = start
    while cursor <= end:
        date_dir = archive_root / cursor.strftime("%Y%m%d")
        if date_dir.is_dir():
            for source_path in date_dir.rglob("IMChatlogExport_*.json"):
                if source_path.name.endswith(".image-index.json"):
                    continue
                name = source_path.name
                for session_id in targets:
                    if f"_{session_id}_" in name:
                        found[session_id].append(source_path.resolve())
        cursor = cursor.fromordinal(cursor.toordinal() + 1)

    counters = {
        "new_marker_count": 0,
        "existing_marker_count": 0,
        "special_landed_count": 0,
        "not_landed_count": 0,
    }
    raw_conversations = report.get("raw_conversations") or {}
    marked_at = datetime.now(timezone.utc).isoformat()
    for item in sessions:
        session_id = str(item.get("session_id") or "").strip()
        source_paths = list(dict.fromkeys(found.get(session_id) or []))
        special_created = False
        if not source_paths and isinstance(raw_conversations.get(session_id), dict):
            special_path = run_dir / "bad-review-conversations" / f"{session_id}.bad-review.json"
            if special_path.exists():
                try:
                    existing_special = json.loads(special_path.read_text(encoding="utf-8"))
                    if str(existing_special.get("sessionId") or "") != session_id:
                        raise ValueError("sessionId 不一致")
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    report["failures"].append({
                        "stage": "special-landing",
                        "session_id": session_id,
                        "error": redact_error(f"已有专门落盘文件不可用，拒绝覆盖：{exc}"),
                    })
                    special_path = None
            else:
                _write_json_atomic(special_path, raw_conversations[session_id])
                special_created = True
            if special_path is not None:
                source_paths = [special_path.resolve()]
                counters["special_landed_count"] += 1

        marker_paths: list[str] = []
        marker_states: list[str] = []
        for source_path in source_paths:
            marker_path = Path(f"{source_path}.bad-review.flag")
            marker_paths.append(str(marker_path))
            if marker_path.exists():
                try:
                    existing = json.loads(marker_path.read_text(encoding="utf-8"))
                    if str(existing.get("sessionId") or "") != session_id:
                        raise ValueError("sessionId 不一致")
                    marker_states.append("existing")
                    counters["existing_marker_count"] += 1
                    continue
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    marker_states.append("failed")
                    report["failures"].append({
                        "stage": "landing-marker",
                        "session_id": session_id,
                        "error": redact_error(f"已有标记不可用：{exc}"),
                    })
                    continue
            payload = {
                "schemaVersion": "1",
                "kind": "trip-im-bad-review",
                "sessionId": session_id,
                "reviewStartDate": report.get("start_date"),
                "reviewEndDate": report.get("end_date"),
                "sourceJson": str(source_path),
                "markedAt": marked_at,
            }
            try:
                _write_json_atomic(marker_path, payload)
                marker_states.append("new")
                counters["new_marker_count"] += 1
            except OSError as exc:
                marker_states.append("failed")
                report["failures"].append({
                    "stage": "landing-marker",
                    "session_id": session_id,
                    "error": redact_error(str(exc)),
                })

        if not source_paths:
            counters["not_landed_count"] += 1
            item["landing_status"] = "not_landed"
            item["archive_paths"] = []
            item["marker_paths"] = []
            report["failures"].append({
                "stage": "landing-marker",
                "session_id": session_id,
                "error": "原归档与本次详情均不存在，无法特别落盘",
            })
        else:
            if "failed" in marker_states:
                status = "mark_failed"
            elif special_created:
                status = "special_landed"
            elif marker_states and all(state == "existing" for state in marker_states):
                status = "already_marked"
            else:
                status = "archive_marked"
            item["landing_status"] = status
            item["archive_paths"] = [str(path) for path in source_paths]
            item["marker_paths"] = marker_paths

    report["landing"] = {
        "enabled": True,
        "mode": "incremental_deduplicated_sidecar",
        **counters,
    }
    if report.get("failures") or report.get("reconciliation_errors"):
        report["status"] = "partial" if report.get("employees") or report.get("sessions") else "failed"
    return counters
