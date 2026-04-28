from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from selenium.webdriver.remote.webdriver import WebDriver

from .browser import execute_js, execute_js_async
from .config import AppConfig
from .models import SessionRecord
from .utils import append_failure, normalize_create_time_parts, safe_name


def _create_markdown(meta: SessionRecord, messages: list[dict]) -> str:
    lines = [
        f"# 会话 {meta.session_id}",
        "",
        f"- 客服: {meta.cs_name}",
        f"- 链接: {meta.detail_url}",
        f"- 消息数: {len(messages)}",
        "",
    ]
    for message in messages:
        sender = message.get("senderRole", "unknown")
        name = message.get("senderName", "")
        lines.append(f"## {message.get('sequence', '-')}. {sender}{f' / {name}' if name else ''}")
        lines.append("")
        lines.append(f"- 时间: {message.get('timestampText', '-')}")
        lines.append(f"- 类型: {message.get('messageType', '-')}")
        text = message.get("text") or ("[图片消息]" if message.get("messageType") == "image" else "[空内容]")
        lines.append(f"- 文本: {text}")
        attachments = message.get("attachments") or []
        if attachments:
            lines.append("- 附件: " + ", ".join(x.get("src", "") for x in attachments if x.get("src")))
        lines.append("")
    return "\n".join(lines)


def export_structured(
    driver: WebDriver,
    repo_root: Path,
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
    detail_script = str(repo_root / "detail-page.js")

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
            log(f"[{i}/{len(sessions)}] 结构化: {sess.session_id}")
            driver.get(sess.detail_url)
            time.sleep(1.8)
            execute_js(driver, f"() => {{ {Path(detail_script).read_text(encoding='utf-8')} ; return true; }}")
            data = execute_js_async(
                driver,
                """
                async (meta) => {
                    const dp = window.__IM_ARCHIVE_DETAIL_PAGE__;
                    await dp.loadAllMessages({ settleMs: 400, stableRounds: 3 });
                    return await dp.extractConversationStructured(meta);
                }
                """,
                {"sessionId": sess.session_id, "csName": sess.cs_name},
            )
            messages = data.get("messages", [])
            if "json" in formats:
                json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            if "markdown" in formats:
                md_path.write_text(_create_markdown(sess, messages), encoding="utf-8")
            success += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            append_failure(
                failures_file,
                {
                    "kind": "structured",
                    "session_id": sess.session_id,
                    "cs_name": sess.cs_name,
                    "error": str(exc),
                },
            )
            log(f"  失败: {sess.session_id} - {exc}")
        finally:
            time.sleep(interval)

    return success, failed
