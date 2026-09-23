from __future__ import annotations

import json
import subprocess
from pathlib import Path

import scripts.hermes_bad_review_watch as watch


def make_summary(root: Path, *, count: int, status: str = "success") -> Path:
    run_dir = root / "bad-reviews" / "20260801-20260801"
    run_dir.mkdir(parents=True)
    xlsx = run_dir / "bad_reviews_20260801-20260801.xlsx"
    xlsx.write_bytes(b"xlsx")
    summary = run_dir / "summary.json"
    summary.write_text(json.dumps({"status": status, "total_bad_reviews": count, "xlsx_path": str(xlsx.resolve())}), encoding="utf-8")
    return summary


def env_for(root: Path) -> dict[str, str]:
    return {
        "BAD_REVIEW_START_DATE": "2026-08-01",
        "BAD_REVIEW_END_DATE": "2026-08-01",
        "BAD_REVIEW_ALERT_THRESHOLD": "2",
        "BAD_REVIEW_OUTPUT_ROOT": str(root),
    }


def test_watch_is_silent_below_threshold_and_uses_only_http_cli(monkeypatch, tmp_path, capsys) -> None:
    make_summary(tmp_path, count=2)
    calls = []
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kwargs: calls.append((cmd, kwargs)) or subprocess.CompletedProcess(cmd, 0, "progress noise", ""))

    rc = watch.main(env_for(tmp_path))

    assert rc == 0
    assert capsys.readouterr().out == ""
    cmd = calls[0][0]
    assert cmd[1:4] == ["-m", "im_archive_cli.imx_cli", "run"]
    assert "bad-reviews" in cmd
    assert "--strict" in cmd
    assert "--mark-landed" in cmd
    assert all(token not in cmd for token in ["browser", "cdp", "ego"])
    assert calls[0][1]["capture_output"] is True


def test_watch_alert_contains_native_xlsx_media_path(monkeypatch, tmp_path, capsys) -> None:
    summary = make_summary(tmp_path, count=3)
    xlsx = json.loads(summary.read_text())["xlsx_path"]
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "noise", ""))

    assert watch.main(env_for(tmp_path)) == 0

    out = capsys.readouterr().out.strip()
    assert "携程客服差评预警" in out
    assert "3" in out
    assert f"MEDIA:{xlsx}" in out
    assert "noise" not in out


def test_watch_failure_is_concise_redacted_and_nonzero(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        watch.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 7, "progress", "HTTP 403 Authorization=secret token=hidden"),
    )

    rc = watch.main(env_for(tmp_path))

    out = capsys.readouterr().out.strip()
    assert rc != 0
    assert "携程客服差评预警执行失败了，原因" in out
    assert "403" in out
    assert "secret" not in out
    assert "hidden" not in out


def test_month_to_date_and_dynamic_schedule_guard() -> None:
    assert watch._dates({"BAD_REVIEW_NOW": "2026-09-29", "BAD_REVIEW_DATE_MODE": "month_to_date"}) == (
        "2026-09-01", "2026-09-29"
    )
    assert watch._eligible({"BAD_REVIEW_NOW": "2026-08-31", "BAD_REVIEW_RUN_GUARD": "monday_or_penultimate"})
    assert watch._eligible({"BAD_REVIEW_NOW": "2026-09-29", "BAD_REVIEW_RUN_GUARD": "monday_or_penultimate"})
    assert not watch._eligible({"BAD_REVIEW_NOW": "2026-09-24", "BAD_REVIEW_RUN_GUARD": "monday_or_penultimate"})

def _make_month_summary(root: Path, *, rate: float) -> dict[str, str]:
    run_dir = root / "bad-reviews" / "20260801-20260831"
    run_dir.mkdir(parents=True)
    xlsx = run_dir / "bad_reviews_20260801-20260831.xlsx"
    xlsx.write_bytes(b"xlsx")
    summary = {
        "status": "success",
        "total_evaluations": 100,
        "total_bad_reviews": int(round(rate * 100)),
        "bad_review_rate": rate,
        "unique_session_count": int(round(rate * 100)),
        "sessions": [
            {"chat_status": "success"} for _ in range(int(round(rate * 100)))
        ],
        "landing": {
            "new_marker_count": 0,
            "existing_marker_count": int(round(rate * 100)),
            "special_landed_count": 0,
            "not_landed_count": 0,
        },
        "xlsx_path": str(xlsx.resolve()),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return {
        "BAD_REVIEW_START_DATE": "2026-08-01",
        "BAD_REVIEW_END_DATE": "2026-08-31",
        "BAD_REVIEW_DATE_MODE": "month_to_date",
        "BAD_REVIEW_RATE_ALERT_THRESHOLD": "0.30",
        "BAD_REVIEW_OUTPUT_ROOT": str(root),
    }


def test_month_rate_below_30_percent_is_exactly_one_sentence_without_media(monkeypatch, tmp_path, capsys) -> None:
    env = _make_month_summary(tmp_path, rate=0.29)
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert watch.main(env) == 0
    out = capsys.readouterr().out.strip()
    assert out.count("\n") == 0
    assert out.endswith("预警线。")
    assert "差评率29.00%" in out
    assert "MEDIA:" not in out
    assert "⚠" not in out


def test_month_rate_at_or_above_30_percent_is_explicit_alert_with_dialogue_and_media(monkeypatch, tmp_path, capsys) -> None:
    env = _make_month_summary(tmp_path, rate=0.30)
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""))
    assert watch.main(env) == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("⚠️ 携程客服差评率预警")
    assert "差评率30.00%达到或超过30.00%预警线" in out
    assert "当月差评对话30个" in out
    assert "聊天导出成功30、失败0" in out
    assert "已有标记30" in out
    assert "MEDIA:" in out
