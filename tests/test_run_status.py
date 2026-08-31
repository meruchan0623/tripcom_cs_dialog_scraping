from __future__ import annotations

import json
from pathlib import Path

import pytest

import im_archive_cli.imx_cli as imx_cli
from im_archive_cli.config import AppConfig
from im_archive_cli.imx_cli import cmd_run_export, cmd_run_retry_failures
from im_archive_cli.models import SessionRecord
from im_archive_cli.state import StateStore


class DummyLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)


def _cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        state_file=str(tmp_path / "state.json"),
        output_dir=str(tmp_path / "output"),
        failures_file=str(tmp_path / "private" / "failures.jsonl"),
        profile_dir=str(tmp_path / "profile"),
    )


def _seed(cfg: AppConfig) -> StateStore:
    store = StateStore(Path(cfg.state_file))
    store.set_sessions(
        [
            SessionRecord(session_id="secret-session-1", cs_name="Alice", create_time="2026-08-03 08:00:00"),
            SessionRecord(session_id="secret-session-2", cs_name="Bob", create_time="2026-08-03 09:00:00"),
        ],
        auto_select_all=True,
    )
    store.set_run_metadata({"start_date": "2026-08-03", "end_date": "2026-08-04"})
    return store


def _summary_files(cfg: AppConfig) -> list[Path]:
    return sorted(Path(cfg.output_dir).glob("*.summary.json"))


def test_structured_export_writes_atomic_reporting_compatible_partial_summary(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    monkeypatch.setattr(imx_cli, "CtripImDetailHttpClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(imx_cli, "export_structured_via_http", lambda *args, **kwargs: (1, 1))

    assert cmd_run_export(cfg, DummyLogger(), "structured") == 0

    summaries = _summary_files(cfg)
    assert len(summaries) == 1
    assert "run_summary_20260803_20260804_" in summaries[0].name
    payload = json.loads(summaries[0].read_text(encoding="utf-8"))
    assert payload == {
        "schemaVersion": "1.0",
        "platform": "trip",
        "kind": "structured",
        "status": "partial",
        "ok": False,
        "exitCode": 1,
        "total": 2,
        "success": 1,
        "failed": 1,
        "startedAt": payload["startedAt"],
        "finishedAt": payload["finishedAt"],
        "errors": [{"reference": "failures.jsonl", "count": 1, "redacted": True}],
        "failure": {"reference": "failures.jsonl", "count": 1, "redacted": True},
        "start_date": "2026-08-03",
        "end_date": "2026-08-04",
    }
    serialized = json.dumps(payload)
    assert "secret-session" not in serialized
    assert str(Path(cfg.failures_file).parent) not in serialized
    assert not list(Path(cfg.output_dir).glob("*.tmp"))


def test_structured_export_failure_path_writes_failed_summary_with_cli_dates(tmp_path) -> None:
    cfg = _cfg(tmp_path)

    with pytest.raises(RuntimeError, match="不能为负数"):
        cmd_run_export(
            cfg,
            DummyLogger(),
            "structured",
            request_budget=-1,
            start_date="2026-08-05",
            end_date="2026-08-05",
        )

    payload = json.loads(_summary_files(cfg)[0].read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["ok"] is False
    assert payload["exitCode"] == 1
    assert payload["start_date"] == "2026-08-05"
    assert payload["end_date"] == "2026-08-05"
    assert payload["failed"] == 1
    assert payload["errors"][0]["count"] == 1


def test_retry_failures_writes_complete_summary_and_uses_collected_metadata(tmp_path, monkeypatch) -> None:
    cfg = _cfg(tmp_path)
    _seed(cfg)
    failure_path = Path(cfg.failures_file)
    failure_path.parent.mkdir(parents=True, exist_ok=True)
    failure_path.write_text(
        json.dumps({"kind": "structured_http", "session_id": "secret-session-1", "retryable": True}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(imx_cli, "CtripImDetailHttpClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(imx_cli, "export_structured_via_http", lambda *args, **kwargs: (1, 0))

    assert cmd_run_retry_failures(cfg, DummyLogger(), "structured", retryable_only=True) == 0

    payload = json.loads(_summary_files(cfg)[0].read_text(encoding="utf-8"))
    assert payload["kind"] == "structured_retry"
    assert payload["status"] == "complete"
    assert payload["ok"] is True
    assert payload["exitCode"] == 0
    assert (payload["total"], payload["success"], payload["failed"]) == (1, 1, 0)
    assert payload["errors"] == []
    assert payload["start_date"] == "2026-08-03"
    assert payload["end_date"] == "2026-08-04"


def test_export_and_retry_date_controls_are_optional() -> None:
    parser = imx_cli.build_parser()

    export = parser.parse_args(["run", "export", "--kind", "structured"])
    retry = parser.parse_args(["run", "retry-failures", "--kind", "structured"])

    assert (export.start_date, export.end_date) == (None, None)
    assert (retry.start_date, retry.end_date) == (None, None)
