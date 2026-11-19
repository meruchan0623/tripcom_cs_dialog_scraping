from __future__ import annotations

import json
import subprocess
from pathlib import Path

import scripts.hermes_bad_review_watch as watch


def make_summary(root: Path, *, count: int, status: str = "success", date_key: str = "20260801-20260801") -> Path:
    run_dir = root / "bad-reviews" / date_key
    run_dir.mkdir(parents=True)
    xlsx = run_dir / f"bad_reviews_{date_key}.xlsx"
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


def test_direct_script_imports_ctrip_client_from_this_repository() -> None:
    """Absolute-path production execution must not import another checkout."""
    repo_dir = Path(__file__).resolve().parents[1]
    script_path = repo_dir / "scripts" / "hermes_bad_review_watch.py"
    code = (
        "import importlib, runpy\n"
        f"namespace = runpy.run_path({str(script_path)!r})\n"
        "client_module = importlib.import_module(namespace['CtripImHttpClient'].__module__)\n"
        "print(client_module.__file__)\n"
    )

    completed = subprocess.run(
        ["/usr/local/bin/python3", "-c", code],
        cwd="/Users/tsimclaw",
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert Path(completed.stdout.strip()).resolve() == (repo_dir / "im_archive_cli" / "ctrip_http.py").resolve()


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


def _install_month_metrics(monkeypatch, *, result=None, error: Exception | None = None):
    calls = []

    class MetricsClient:
        def __init__(self, cfg, *, request_interval_sec):
            assert request_interval_sec == 0
            self.cfg = cfg

        def get_bad_review_period_metrics(self, start_date, end_date):
            calls.append((start_date, end_date))
            if error:
                raise error
            return result

    monkeypatch.setattr(watch, "CtripImHttpClient", MetricsClient)
    return calls


def test_opt_in_daily_zero_prints_current_month_metrics_across_year_boundary(monkeypatch, tmp_path, capsys) -> None:
    make_summary(tmp_path, count=0, date_key="20261231-20261231")
    calls = _install_month_metrics(monkeypatch, result={"total_evaluations": 42, "bad_review_rate": 0.125})
    command_calls = []
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kwargs: command_calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    env = {
        "BAD_REVIEW_NOW": "2027-01-01",
        "BAD_REVIEW_INCLUDE_MTD_METRICS": "1",
        "BAD_REVIEW_OUTPUT_ROOT": str(tmp_path),
    }

    assert watch.main(env) == 0

    assert "--start-date" in command_calls[0]
    assert command_calls[0][command_calls[0].index("--start-date") + 1] == "2026-12-31"
    assert calls == [("2027-01-01", "2027-01-01")]
    out = capsys.readouterr().out.strip()
    assert out == "携程客服自然月指标：2027-01-01至2027-01-01，总客服评价42，差评率12.50%。"
    assert "MEDIA:" not in out


def test_opt_in_daily_alert_keeps_media_and_appends_current_month_metrics(monkeypatch, tmp_path, capsys) -> None:
    summary = make_summary(tmp_path, count=3)
    xlsx = json.loads(summary.read_text())["xlsx_path"]
    calls = _install_month_metrics(monkeypatch, result={"total_evaluations": 99, "bad_review_rate": 0.03})
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""))
    env = env_for(tmp_path) | {"BAD_REVIEW_NOW": "2026-09-01", "BAD_REVIEW_INCLUDE_MTD_METRICS": "true"}

    assert watch.main(env) == 0

    assert calls == [("2026-09-01", "2026-09-01")]
    out = capsys.readouterr().out.strip()
    assert f"MEDIA:{xlsx}" in out
    assert out.endswith("携程客服自然月指标：2026-09-01至2026-09-01，总客服评价99，差评率3.00%。")


def test_opt_in_metric_failure_is_nonzero_and_preserves_daily_alert(monkeypatch, tmp_path, capsys) -> None:
    summary = make_summary(tmp_path, count=3)
    xlsx = json.loads(summary.read_text())["xlsx_path"]
    _install_month_metrics(monkeypatch, error=RuntimeError("HTTP 403 Authorization=secret token=hidden"))
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""))
    env = env_for(tmp_path) | {"BAD_REVIEW_NOW": "2026-09-02", "BAD_REVIEW_INCLUDE_MTD_METRICS": "1"}

    assert watch.main(env) != 0

    out = capsys.readouterr().out.strip()
    assert f"MEDIA:{xlsx}" in out
    assert "自然月指标：2026-09-01至2026-09-02暂不可用" in out
    assert "secret" not in out
    assert "hidden" not in out


def test_opt_in_missing_metric_is_unavailable_not_zero(monkeypatch, tmp_path, capsys) -> None:
    make_summary(tmp_path, count=0)
    _install_month_metrics(monkeypatch, result={"total_evaluations": 12})
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""))

    assert watch.main(env_for(tmp_path) | {"BAD_REVIEW_NOW": "2026-10-14", "BAD_REVIEW_INCLUDE_MTD_METRICS": "1"}) != 0

    out = capsys.readouterr().out.strip()
    assert "2026-10-01至2026-10-14暂不可用" in out
    assert "总客服评价0" not in out


def test_non_opt_in_daily_and_month_monitor_do_not_fetch_extra_metrics(monkeypatch, tmp_path, capsys) -> None:
    make_summary(tmp_path, count=0)

    class UnexpectedMetricsClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("extra current-month metric call is opt-in daily only")

    monkeypatch.setattr(watch, "CtripImHttpClient", UnexpectedMetricsClient)
    monkeypatch.setattr(watch.subprocess, "run", lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "", ""))

    assert watch.main(env_for(tmp_path)) == 0
    assert capsys.readouterr().out == ""

    month_env = _make_month_summary(tmp_path / "month", rate=0.29)
    assert watch.main(month_env) == 0
    assert "差评率29.00%" in capsys.readouterr().out


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
