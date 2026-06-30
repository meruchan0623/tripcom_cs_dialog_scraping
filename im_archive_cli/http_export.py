from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .config import AppConfig
from .ctrip_http import CtripHttpError, CtripImDetailHttpClient, CtripRequestBudgetExceeded
from .image_index import write_conversation_image_index
from .markdown_export import create_markdown
from .media_download import download_conversation_images
from .models import SessionRecord
from .utils import append_failure, normalize_create_time_parts, safe_name, write_text_atomic


class ExportStageError(RuntimeError):
    def __init__(self, stage: str, original: Exception) -> None:
        super().__init__(str(original))
        self.stage = stage
        self.original = original


def _existing_export_complete(json_path: Path, md_path: Path, formats: list[str]) -> bool:
    if "json" not in formats:
        return False
    if not json_path.exists() or json_path.stat().st_size <= 0:
        return False
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list) or not messages:
        return False
    if "markdown" in formats and (not md_path.exists() or md_path.stat().st_size <= 0):
        return False
    return True


def _failure_error_type(exc: Exception) -> str:
    original = exc.original if isinstance(exc, ExportStageError) else exc
    if isinstance(original, CtripHttpError):
        return f"http_{original.status_code}" if original.status_code else "http_network"
    if isinstance(original, CtripRequestBudgetExceeded):
        return "request_budget_exceeded"
    return type(original).__name__


def _failure_retryable(exc: Exception) -> bool:
    original = exc.original if isinstance(exc, ExportStageError) else exc
    return bool(isinstance(original, CtripHttpError) and original.retryable)


def _failure_stage(exc: Exception) -> str:
    if isinstance(exc, ExportStageError):
        return exc.stage
    return "fetch_conversation"


def export_structured_via_http(
    client: CtripImDetailHttpClient,
    config: AppConfig,
    sessions: list[SessionRecord],
    formats: list[str],
    log: Callable[[str], None],
    resume_from_state: bool = True,
) -> tuple[int, int]:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    failures_file = Path(config.failures_file)
    max_workers = max(1, int(config.concurrency))
    interval = max(0.05, float(config.window_sec) / max_workers)

    success = 0
    failed = 0
    pending: list[tuple[int, SessionRecord, Path, Path]] = []
    for i, sess in enumerate(sessions, start=1):
        cs_safe = safe_name(sess.cs_name)
        create_stamp, create_date = normalize_create_time_parts(sess.create_time)
        base_name = f"IMChatlogExport_{create_stamp}_{sess.session_id}_{cs_safe}"
        out_dir = output_dir / create_date / cs_safe
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"{base_name}.json"
        md_path = out_dir / f"{base_name}.md"

        if resume_from_state and _existing_export_complete(json_path, md_path, formats):
            log(f"[{i}/{len(sessions)}] 跳过已存在: {sess.session_id}")
            success += 1
            continue

        pending.append((i, sess, json_path, md_path))

    consecutive_retryable_failures = 0
    consecutive_fatal_failures = 0
    for batch_start in range(0, len(pending), max_workers):
        if batch_start > 0:
            time.sleep(interval)
        if consecutive_retryable_failures >= 3 or consecutive_fatal_failures >= 3:
            log("连续接口失败达到 3 次，已停止本轮结构化导出")
            break
        batch = pending[batch_start : batch_start + max_workers]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_and_write_session, client, config, item, formats, log, len(sessions)): item
                for item in batch
            }
            for future in as_completed(futures):
                _i, sess, _json_path, _md_path = futures[future]
                try:
                    future.result()
                    success += 1
                except CtripRequestBudgetExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    retryable = _failure_retryable(exc)
                    consecutive_retryable_failures = consecutive_retryable_failures + 1 if retryable else 0
                    consecutive_fatal_failures = consecutive_fatal_failures + 1 if not retryable else 0
                    append_failure(
                        failures_file,
                        {
                            "kind": "structured_http",
                            "session_id": sess.session_id,
                            "cs_name": sess.cs_name,
                            "stage": _failure_stage(exc),
                            "error_type": _failure_error_type(exc),
                            "retryable": retryable,
                            "attempt": 1,
                            "error": str(exc),
                        },
                    )
                    log(f"  失败: {sess.session_id} - {exc}")
                    if consecutive_retryable_failures >= 3 or consecutive_fatal_failures >= 3:
                        break
    return success, failed


def _build_session_client(template_client: CtripImDetailHttpClient, log: Callable[[str], None]) -> CtripImDetailHttpClient:
    return CtripImDetailHttpClient(
        template_client.cfg,
        log=log,
        request_interval_sec=template_client.cfg.structured_request_interval_sec,
        request_budget=getattr(template_client, "request_budget", None),
    )


def _fetch_and_write_session(
    client: CtripImDetailHttpClient,
    config: AppConfig,
    item: tuple[int, SessionRecord, Path, Path],
    formats: list[str],
    log: Callable[[str], None],
    total: int,
) -> None:
    i, sess, json_path, md_path = item
    log(f"[{i}/{total}] 结构化(HTTP): {sess.session_id}")
    worker_client: CtripImDetailHttpClient = _build_session_client(client, log) if isinstance(client, CtripImDetailHttpClient) else client
    try:
        data = worker_client.fetch_conversation(sess)
    except CtripRequestBudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ExportStageError("fetch_conversation", exc) from exc
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        raise ExportStageError("fetch_conversation", RuntimeError("提取失败：返回数据缺少 messages"))
    if not messages:
        raise ExportStageError("fetch_conversation", RuntimeError("提取失败：messages 为空"))
    try:
        download_conversation_images(data, json_path.parent, json_path.stem, config, log)
    except Exception as exc:  # noqa: BLE001
        raise ExportStageError("download_images", exc) from exc
    try:
        if "json" in formats:
            write_text_atomic(json_path, json.dumps(data, ensure_ascii=False, indent=2))
            write_conversation_image_index(data, json_path)
        if "markdown" in formats:
            write_text_atomic(md_path, create_markdown(sess, messages))
    except Exception as exc:  # noqa: BLE001
        raise ExportStageError("write_output", exc) from exc
