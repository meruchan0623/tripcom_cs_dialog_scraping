#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo

_REPO_DIR = Path(__file__).resolve().parents[1]
# This watcher is invoked by absolute path from outside the repository.  Make
# the checkout win over any similarly named package installed for another job.
_repo_source_root = str(_REPO_DIR)
sys.path[:] = [path for path in sys.path if path != _repo_source_root]
sys.path.insert(0, _repo_source_root)

import yaml

from im_archive_cli.config import AppConfig
from im_archive_cli.ctrip_http import CtripImHttpClient

_SECRET_RE = re.compile(r"(?i)\b(cookie|authorization|cticket|token|password|secret|sid)\b\s*[:=]\s*[^\s,;]+")


def _sanitize(value: str, limit: int = 180) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    text = _SECRET_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    if not text:
        return "未知错误"
    if len(text) <= limit:
        return text
    head = max(60, limit // 2)
    tail = max(40, limit - head - 1)
    return f"{text[:head]}…{text[-tail:]}"


def _today(env: Mapping[str, str]) -> date:
    fixed = env.get("BAD_REVIEW_NOW", "").strip()
    if fixed:
        return datetime.strptime(fixed, "%Y-%m-%d").date()
    timezone = env.get("BAD_REVIEW_TIMEZONE", "Asia/Shanghai")
    return datetime.now(ZoneInfo(timezone)).date()


def _is_penultimate_day(value: date) -> bool:
    probe = value.replace(day=28) + timedelta(days=4)
    last_day = probe.replace(day=1) - timedelta(days=1)
    return value == last_day - timedelta(days=1)


def _eligible(env: Mapping[str, str]) -> bool:
    guard = env.get("BAD_REVIEW_RUN_GUARD", "").strip().lower()
    if not guard:
        return True
    today = _today(env)
    if guard == "monday_or_penultimate":
        return today.weekday() == 0 or _is_penultimate_day(today)
    raise ValueError(f"未知 BAD_REVIEW_RUN_GUARD：{guard}")


def _dates(env: Mapping[str, str]) -> tuple[str, str]:
    start = env.get("BAD_REVIEW_START_DATE", "").strip()
    end = env.get("BAD_REVIEW_END_DATE", "").strip()
    if bool(start) != bool(end):
        raise ValueError("BAD_REVIEW_START_DATE 和 BAD_REVIEW_END_DATE 必须同时设置")
    if start:
        for value in (start, end):
            datetime.strptime(value, "%Y-%m-%d")
        if start > end:
            raise ValueError("开始日期不能晚于结束日期")
        return start, end
    today = _today(env)
    mode = env.get("BAD_REVIEW_DATE_MODE", "yesterday").strip().lower()
    if mode == "month_to_date":
        return today.replace(day=1).isoformat(), today.isoformat()
    if mode == "yesterday":
        target = today - timedelta(days=1)
    elif mode == "today":
        target = today
    else:
        raise ValueError(f"未知 BAD_REVIEW_DATE_MODE：{mode}")
    value = target.isoformat()
    return value, value


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _current_month_metrics(env: Mapping[str, str], repo_dir: Path) -> tuple[str, str, int, float]:
    """Fetch only the metric card for the natural month containing today."""
    today = _today(env)
    start_date = today.replace(day=1).isoformat()
    end_date = today.isoformat()
    config_path = repo_dir / "config.yaml"
    if not config_path.is_file():
        raise RuntimeError("未找到 config.yaml，无法读取自然月指标配置")
    config_data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(config_data, dict):
        raise RuntimeError("config.yaml 格式无效，无法读取自然月指标配置")
    cfg = AppConfig.from_mapping(config_data)
    metrics = CtripImHttpClient(cfg, request_interval_sec=0).get_bad_review_period_metrics(start_date, end_date)
    return start_date, end_date, int(metrics["total_evaluations"]), float(metrics["bad_review_rate"])


def _print_current_month_metrics(env: Mapping[str, str], repo_dir: Path) -> int:
    today = _today(env)
    start_date = today.replace(day=1).isoformat()
    end_date = today.isoformat()
    try:
        start_date, end_date, total, rate = _current_month_metrics(env, repo_dir)
    except Exception as exc:  # The daily result must remain visible if this extra call fails.
        print(f"携程客服自然月指标：{start_date}至{end_date}暂不可用，原因：{_sanitize(exc)}。")
        return 1
    print(f"携程客服自然月指标：{start_date}至{end_date}，总客服评价{total}，差评率{rate * 100:.2f}%。")
    return 0


def main(environ: Mapping[str, str] | None = None) -> int:
    env = dict(os.environ if environ is None else environ)
    try:
        if not _eligible(env):
            return 0
        start_date, end_date = _dates(env)
        threshold = int(env.get("BAD_REVIEW_ALERT_THRESHOLD", "0"))
        if threshold < 0:
            raise ValueError("BAD_REVIEW_ALERT_THRESHOLD 不能为负数")
        script_path = Path(__file__).resolve()
        repo_dir = Path(env.get("BAD_REVIEW_REPO_DIR", str(script_path.parents[1]))).expanduser().resolve()
        output_root = Path(env.get("BAD_REVIEW_OUTPUT_ROOT", str(repo_dir / ".im_archive" / "output"))).expanduser().resolve()
        page_size = int(env.get("BAD_REVIEW_PAGE_SIZE", "100"))
        max_pages = int(env.get("BAD_REVIEW_MAX_PAGES", "50"))
        interval = env.get("BAD_REVIEW_REQUEST_INTERVAL_SEC", "0")
        command = [
            sys.executable, "-m", "im_archive_cli.imx_cli", "run", "bad-reviews",
            "--start-date", start_date, "--end-date", end_date,
            "--output-dir", str(output_root), "--page-size", str(page_size),
            "--max-pages", str(max_pages), "--request-interval-sec", str(interval), "--strict",
        ]
        if _truthy(env.get("BAD_REVIEW_MARK_LANDED"), default=True):
            command.append("--mark-landed")
        completed = subprocess.run(
            command,
            cwd=repo_dir,
            text=True,
            capture_output=True,
            timeout=int(env.get("BAD_REVIEW_TIMEOUT_SEC", "165")),
            check=False,
        )
        if completed.returncode != 0:
            reason = _sanitize(completed.stderr or completed.stdout)
            print(f"携程客服差评预警执行失败了，原因：{reason}")
            return completed.returncode or 1
        run_key = f"{start_date.replace('-', '')}-{end_date.replace('-', '')}"
        summary_path = output_root / "bad-reviews" / run_key / "summary.json"
        if not summary_path.is_file():
            print("携程客服差评预警执行失败了，原因：未生成 summary.json")
            return 1
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        if summary.get("status") != "success":
            print(f"携程客服差评预警执行失败了，原因：结果状态为 {_sanitize(summary.get('status'))}")
            return 1
        count = int(summary.get("total_bad_reviews") or 0)
        month_mode = env.get("BAD_REVIEW_DATE_MODE", "").strip().lower() == "month_to_date"
        if month_mode:
            total = int(summary.get("total_evaluations") or 0)
            rate = float(summary.get("bad_review_rate") or 0.0)
            rate_threshold = float(env.get("BAD_REVIEW_RATE_ALERT_THRESHOLD", "0.30"))
            if not 0.0 <= rate_threshold <= 1.0:
                raise ValueError("BAD_REVIEW_RATE_ALERT_THRESHOLD 必须在 0-1 之间")
            if rate < rate_threshold:
                print(
                    f"携程客服自然月情况：{start_date}至{end_date}，总客服评价{total}，"
                    f"差评{count}，差评率{rate * 100:.2f}%，低于{rate_threshold * 100:.2f}%预警线。"
                )
                return 0

            xlsx_path = Path(str(summary.get("xlsx_path") or "")).expanduser().resolve()
            if not xlsx_path.is_file():
                print("携程客服差评预警执行失败了，原因：summary 中的 XLSX 不存在")
                return 1
            sessions = summary.get("sessions") or []
            chat_success = sum(1 for item in sessions if item.get("chat_status") == "success")
            chat_failed = sum(1 for item in sessions if item.get("chat_status") == "failed")
            landing = summary.get("landing") or {}
            landing_text = (
                f"增量去重落盘：新标记{int(landing.get('new_marker_count') or 0)}，"
                f"已有标记{int(landing.get('existing_marker_count') or 0)}，"
                f"补充专门落盘{int(landing.get('special_landed_count') or 0)}，"
                f"未落盘{int(landing.get('not_landed_count') or 0)}"
            )
            text = (
                f"⚠️ 携程客服差评率预警：{start_date}至{end_date}，总客服评价{total}，"
                f"差评{count}，差评率{rate * 100:.2f}%达到或超过{rate_threshold * 100:.2f}%预警线；"
                f"当月差评对话{int(summary.get('unique_session_count') or 0)}个，"
                f"聊天导出成功{chat_success}、失败{chat_failed}；{landing_text}。"
            )
            print(f"{text}\nMEDIA:{xlsx_path}")
            return 0

        if count > threshold:
            xlsx_path = Path(str(summary.get("xlsx_path") or "")).expanduser().resolve()
            if not xlsx_path.is_file():
                print("携程客服差评预警执行失败了，原因：summary 中的 XLSX 不存在")
                return 1
            landing = summary.get("landing") or {}
            landing_text = (
                f"增量去重落盘：新标记{int(landing.get('new_marker_count') or 0)}，"
                f"已有标记{int(landing.get('existing_marker_count') or 0)}，"
                f"补充专门落盘{int(landing.get('special_landed_count') or 0)}，"
                f"未落盘{int(landing.get('not_landed_count') or 0)}"
            )
            text = f"携程客服差评预警：{start_date}共{count}条；{landing_text}。"
            print(f"{text}\nMEDIA:{xlsx_path}")
        if _truthy(env.get("BAD_REVIEW_INCLUDE_MTD_METRICS")):
            return _print_current_month_metrics(env, repo_dir)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        print(f"携程客服差评预警执行失败了，原因：{_sanitize(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
