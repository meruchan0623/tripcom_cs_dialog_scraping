from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from selenium.webdriver.remote.webdriver import WebDriver

from .browser import execute_js, execute_js_async
from .config import AppConfig
from .models import SessionRecord
from .utils import append_failure, safe_name


def export_singlefile(
    driver: WebDriver,
    repo_root: Path,
    config: AppConfig,
    sessions: list[SessionRecord],
    log: Callable[[str], None],
    resume_from_state: bool = True,
) -> tuple[int, int]:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    failures_file = Path(config.failures_file)
    interval = max(0.05, float(config.window_sec) / max(1, int(config.concurrency)))

    sf_core = str(repo_root / "lib" / "singlefile" / "single-file.js")
    sf_runner = str(repo_root / "singlefile-runner.js")

    success = 0
    failed = 0
    for i, sess in enumerate(sessions, start=1):
        cs_safe = safe_name(sess.cs_name)
        filename = f"{config.output_prefix}_{cs_safe}_{sess.session_id}_{str(i).zfill(3)}.html"
        path = output_dir / cs_safe / filename
        path.parent.mkdir(parents=True, exist_ok=True)

        if resume_from_state and path.exists() and path.stat().st_size > 0:
            log(f"[{i}/{len(sessions)}] 跳过已存在: {path.name}")
            success += 1
            continue

        try:
            log(f"[{i}/{len(sessions)}] SingleFile: {sess.session_id}")
            driver.get(sess.detail_url)
            time.sleep(1.8)
            execute_js(driver, f"() => {{ {Path(sf_core).read_text(encoding='utf-8')} ; return true; }}")
            execute_js(driver, f"() => {{ {Path(sf_runner).read_text(encoding='utf-8')} ; return true; }}")
            html_content = execute_js_async(driver, "() => window.__IM_ARCHIVE_SINGLEFILE_GET_CONTENT__({})")
            path.write_text(html_content, encoding="utf-8")
            success += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            append_failure(
                failures_file,
                {
                    "kind": "singlefile",
                    "session_id": sess.session_id,
                    "cs_name": sess.cs_name,
                    "error": str(exc),
                },
            )
            log(f"  失败: {sess.session_id} - {exc}")
        finally:
            time.sleep(interval)
    return success, failed
