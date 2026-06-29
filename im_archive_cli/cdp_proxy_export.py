from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .config import AppConfig
from .models import SessionRecord
from .utils import append_failure, normalize_create_time_parts, safe_name


class CdpProxyClient:
    def __init__(self, base_url: str):
        self.base_url = str(base_url).rstrip("/")

    def new_tab(self, url: str) -> str:
        encoded = urllib.parse.quote(url, safe="")
        with urllib.request.urlopen(f"{self.base_url}/new?url={encoded}", timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        target_id = str(data.get("targetId") or "")
        if not target_id:
            raise RuntimeError(f"CDP proxy 未返回 targetId: {data}")
        return target_id

    def close(self, target_id: str) -> None:
        try:
            with urllib.request.urlopen(f"{self.base_url}/close?target={urllib.parse.quote(target_id)}", timeout=5):  # noqa: S310
                pass
        except Exception:  # noqa: BLE001
            pass

    def eval(self, target_id: str, expression: str, timeout: int = 120) -> Any:
        request = urllib.request.Request(
            f"{self.base_url}/eval?target={urllib.parse.quote(target_id)}",
            data=expression.encode("utf-8"),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return data.get("value")


def export_singlefile_via_cdp_proxy(
    proxy: CdpProxyClient,
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
    sf_core = (repo_root / "lib" / "singlefile" / "single-file.js").read_text(encoding="utf-8")
    sf_runner = (repo_root / "singlefile-runner.js").read_text(encoding="utf-8")

    success = 0
    failed = 0
    for i, sess in enumerate(sessions, start=1):
        cs_safe = safe_name(sess.cs_name)
        create_stamp, create_date = normalize_create_time_parts(sess.create_time)
        filename = f"IMChatlogExport_{create_stamp}_{sess.session_id}_{cs_safe}.html"
        path = output_dir / create_date / cs_safe / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        if resume_from_state and path.exists() and path.stat().st_size > 0:
            log(f"[{i}/{len(sessions)}] 跳过已存在: {path.name}")
            success += 1
            continue

        target_id = ""
        try:
            log(f"[{i}/{len(sessions)}] SingleFile(CDP): {sess.session_id}")
            target_id = proxy.new_tab(sess.detail_url)
            proxy.eval(target_id, f"(() => {{ {sf_core}\nreturn true; }})()", timeout=60)
            proxy.eval(target_id, f"(() => {{ {sf_runner}\nreturn true; }})()", timeout=60)
            html_content = proxy.eval(target_id, "window.__IM_ARCHIVE_SINGLEFILE_GET_CONTENT__({})", timeout=240)
            if not isinstance(html_content, str) or "<html" not in html_content.lower():
                raise RuntimeError("SingleFile 返回非 HTML 内容")
            path.write_text(html_content, encoding="utf-8")
            success += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            append_failure(
                failures_file,
                {"kind": "singlefile", "session_id": sess.session_id, "cs_name": sess.cs_name, "error": str(exc)},
            )
            log(f"  失败: {sess.session_id} - {exc}")
        finally:
            if target_id:
                proxy.close(target_id)
            time.sleep(interval)
    return success, failed
