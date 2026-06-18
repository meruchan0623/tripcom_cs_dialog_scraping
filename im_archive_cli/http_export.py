from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from .config import AppConfig
from .ctrip_http import CtripImDetailHttpClient, CtripRequestBudgetExceeded
from .export_structured import _create_markdown
from .models import SessionRecord
from .utils import append_failure, normalize_create_time_parts, safe_name


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
    interval = max(0.05, float(config.window_sec) / max(1, int(config.concurrency)))

    success = 0
    failed = 0
    for i, sess in enumerate(sessions, start=1):
        cs_safe = safe_name(sess.cs_name)
        create_stamp, create_date = normalize_create_time_parts(sess.create_time)
        base_name = f"IMChatlogExport_{create_stamp}_{sess.session_id}_{cs_safe}"
        out_dir = output_dir / create_date / cs_safe
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"{base_name}.json"
        md_path = out_dir / f"{base_name}.md"

        if resume_from_state and "json" in formats and json_path.exists() and json_path.stat().st_size > 0:
            if "markdown" not in formats or (md_path.exists() and md_path.stat().st_size > 0):
                log(f"[{i}/{len(sessions)}] 跳过已存在: {sess.session_id}")
                success += 1
                continue

        try:
            log(f"[{i}/{len(sessions)}] 结构化(HTTP): {sess.session_id}")
            data = client.fetch_conversation(sess)
            messages = data.get("messages", [])
            if not isinstance(messages, list):
                raise RuntimeError("提取失败：返回数据缺少 messages")
            if not messages:
                raise RuntimeError("提取失败：messages 为空")
            if "json" in formats:
                json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            if "markdown" in formats:
                md_path.write_text(_create_markdown(sess, messages), encoding="utf-8")
            success += 1
        except CtripRequestBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            failed += 1
            append_failure(
                failures_file,
                {"kind": "structured_http", "session_id": sess.session_id, "cs_name": sess.cs_name, "error": str(exc)},
            )
            log(f"  失败: {sess.session_id} - {exc}")
        finally:
            time.sleep(interval)
    return success, failed
