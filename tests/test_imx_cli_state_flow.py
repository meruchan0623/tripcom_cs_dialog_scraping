from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from im_archive_cli.config import AppConfig
import im_archive_cli.imx_cli as imx_cli
from im_archive_cli.imx_cli import cmd_import_links, cmd_roles_list, cmd_roles_select, cmd_run_collect_http, cmd_run_export, cmd_state_watch
from im_archive_cli.models import SessionRecord
from im_archive_cli.state import StateStore
from im_archive_cli.xlsx_io import export_links_xlsx


class DummyLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)


def make_cfg(tmp_path: Path) -> AppConfig:
    return AppConfig(
        state_file=str(tmp_path / "state.json"),
        output_dir=str(tmp_path / "out"),
        failures_file=str(tmp_path / "failures.jsonl"),
        profile_dir=str(tmp_path / "profile"),
    )


def seed_state(cfg: AppConfig) -> None:
    StateStore(Path(cfg.state_file)).set_sessions(
        [
            SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 08:00:00"),
            SessionRecord(session_id="s2", cs_name="Bob", create_time="2026-06-16 09:00:00"),
        ],
        auto_select_all=True,
    )


def test_roles_and_state_use_python_store(tmp_path, capsys) -> None:
    cfg = make_cfg(tmp_path)
    seed_state(cfg)

    assert cmd_roles_list(cfg) == 0
    assert "[*] Alice" in capsys.readouterr().out

    assert cmd_roles_select(cfg, all_roles=False, include="Bob") == 0
    selected_output = capsys.readouterr().out
    assert "- Bob" in selected_output

    assert cmd_state_watch(cfg, interval_sec=0.01, once=True) == 0
    state_output = capsys.readouterr().out
    assert "collected=2" in state_output
    assert "selectedRoles=1" in state_output


def test_links_export_uses_selected_python_store(tmp_path) -> None:
    cfg = make_cfg(tmp_path)
    seed_state(cfg)
    StateStore(Path(cfg.state_file)).set_selected_roles(["Bob"])
    out = tmp_path / "links.xlsx"

    assert cmd_run_export(cfg, DummyLogger(), "links", output=str(out)) == 0

    assert out.exists()
    imported = __import__("im_archive_cli.xlsx_io", fromlist=["import_links_xlsx"]).import_links_xlsx(out)
    assert [item.session_id for item in imported] == ["s2"]


def test_import_links_writes_python_store(tmp_path) -> None:
    cfg = make_cfg(tmp_path)
    source = tmp_path / "source.xlsx"
    export_links_xlsx(source, [SessionRecord(session_id="s1", cs_name="Alice")])

    assert cmd_import_links(cfg, source, preview_only=False, confirm=True) == 0

    sessions = StateStore(Path(cfg.state_file)).get_sessions()
    assert [item.session_id for item in sessions] == ["s1"]


def test_http_export_logs_budget_when_request_fails(monkeypatch, tmp_path) -> None:
    from im_archive_cli import imx_cli

    cfg = make_cfg(tmp_path)
    cfg.ctrip_cookie_header_file = str(tmp_path / "cookie.txt")
    Path(cfg.ctrip_cookie_header_file).write_text("foo=bar", encoding="utf-8")
    cfg.ctrip_auth_json = str(tmp_path / "missing.json")
    seed_state(cfg)
    logger = DummyLogger()

    class FailingClient:
        def __init__(self, cfg, log=None, request_budget=None):
            self.request_budget = request_budget

        def fetch_conversation(self, session):
            self.request_budget.consume("detail")
            raise RuntimeError("HTTP 403")

    monkeypatch.setattr(imx_cli, "CtripImDetailHttpClient", FailingClient)

    assert cmd_run_export(cfg, logger, "structured", via="http", request_budget=3) == 0

    assert any("携程接口请求计数: used=2, limit=3" in line for line in logger.lines)


def test_collect_rejects_request_budget_over_30_before_client_setup(tmp_path) -> None:
    cfg = make_cfg(tmp_path)

    with pytest.raises(RuntimeError, match="collect request-budget 不能超过 30"):
        cmd_run_collect_http(
            cfg,
            DummyLogger(),
            page_size=1,
            max_pages=1,
            start_date="2026-06-16",
            end_date="2026-06-16",
            include=None,
            via="http",
            request_budget=31,
        )


def test_collect_uses_configured_request_budget_max(tmp_path) -> None:
    cfg = make_cfg(tmp_path)
    cfg.ctrip_request_budget_max = 10

    with pytest.raises(RuntimeError, match="collect request-budget 不能超过 10"):
        cmd_run_collect_http(
            cfg,
            DummyLogger(),
            page_size=1,
            max_pages=1,
            start_date="2026-06-16",
            end_date="2026-06-16",
            include=None,
            via="http",
            request_budget=11,
        )


def test_http_export_rejects_negative_request_budget_before_state_read(tmp_path) -> None:
    cfg = make_cfg(tmp_path)

    with pytest.raises(RuntimeError, match="export request-budget 不能为负数"):
        cmd_run_export(cfg, DummyLogger(), "structured", via="http", request_budget=-1)


def test_collect_rejects_request_ledger_without_request_budget(tmp_path) -> None:
    cfg = make_cfg(tmp_path)

    with pytest.raises(RuntimeError, match="collect request-ledger 必须配合 request-budget 使用"):
        cmd_run_collect_http(
            cfg,
            DummyLogger(),
            page_size=1,
            max_pages=1,
            start_date="2026-06-16",
            end_date="2026-06-16",
            include=None,
            via="http",
            request_ledger=str(tmp_path / "ledger.json"),
        )


def test_export_rejects_request_ledger_without_request_budget(tmp_path) -> None:
    cfg = make_cfg(tmp_path)

    with pytest.raises(RuntimeError, match="export request-ledger 必须配合 request-budget 使用"):
        cmd_run_export(cfg, DummyLogger(), "structured", via="http", request_ledger=str(tmp_path / "ledger.json"))


@pytest.mark.parametrize(
    ("kind", "via"),
    [
        ("links", "cdp"),
        ("singlefile", "cdp"),
        ("structured", "cdp"),
    ],
)
def test_export_rejects_budget_on_paths_that_cannot_count_requests(tmp_path, kind, via) -> None:
    cfg = make_cfg(tmp_path)

    with pytest.raises(RuntimeError, match="只支持 structured --via http"):
        cmd_run_export(cfg, DummyLogger(), kind, via=via, request_budget=30)


def test_main_prints_clean_error_for_request_budget_over_30(tmp_path, capsys) -> None:
    from im_archive_cli.imx_cli import main

    cfg_path = tmp_path / "config.yaml"

    rc = main(
        [
            "--config",
            str(cfg_path),
            "run",
            "collect",
            "--via",
            "http",
            "--start-date",
            "2026-06-16",
            "--end-date",
            "2026-06-16",
            "--request-budget",
            "31",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "collect request-budget 不能超过 30" in captured.err
    assert "Traceback" not in captured.err


def test_collect_cli_overrides_request_interval(tmp_path, monkeypatch) -> None:
    from im_archive_cli.imx_cli import main

    cfg = make_cfg(tmp_path)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg.__dict__, sort_keys=False, allow_unicode=True), encoding="utf-8")
    captured: dict[str, float] = {}

    class FakeClient:
        def __init__(self, cfg: AppConfig, log=None, request_interval_sec=None, request_budget=None) -> None:
            captured["interval"] = float(request_interval_sec)

        def collect_sessions(self, *args, **kwargs):
            return []

    monkeypatch.setattr(imx_cli, "CtripImHttpClient", FakeClient)

    rc = main(
        [
            "--config",
            str(cfg_path),
            "run",
            "collect",
            "--via",
            "http",
            "--start-date",
            "2026-06-16",
            "--end-date",
            "2026-06-16",
            "--request-interval-sec",
            "2.5",
        ]
    )

    assert rc == 0
    assert captured == {"interval": 2.5}


def test_main_auth_status_reports_sources_without_cookie_values(tmp_path, capsys) -> None:
    from im_archive_cli.config import save_config
    from im_archive_cli.imx_cli import main

    cfg = make_cfg(tmp_path)
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=secret; bar=value", encoding="utf-8")
    cfg.ctrip_cookie_header_file = str(cookie_file)
    cfg.ctrip_auth_json = str(tmp_path / "missing.json")
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg_path, cfg)

    rc = main(["--config", str(cfg_path), "auth", "status"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["selected"] == str(cookie_file)
    assert payload["sources"][0]["cookieNames"] == ["foo", "bar"]
    assert "secret" not in json.dumps(payload)
    assert "value" not in json.dumps(payload)


def test_main_rejects_browser_collect_with_request_budget(tmp_path, capsys) -> None:
    from im_archive_cli.imx_cli import main

    cfg_path = tmp_path / "config.yaml"

    rc = main(
        [
            "--config",
            str(cfg_path),
            "run",
            "collect",
            "--via",
            "browser",
            "--request-budget",
            "30",
            "--request-ledger",
            str(tmp_path / "ledger.json"),
        ]
    )

    captured = capsys.readouterr()
    assert rc == 1
    assert "collect --via browser 旧插件路径无法精确执行 request-budget/request-ledger" in captured.err
    assert "Traceback" not in captured.err


def test_main_request_budget_status_reports_missing_ledger_as_zero(tmp_path, capsys) -> None:
    from im_archive_cli.imx_cli import main

    ledger = tmp_path / "ledger.json"

    rc = main(["request-budget", "status", "--request-budget", "30", "--request-ledger", str(ledger)])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload == {"ledger": str(ledger), "limit": 30, "used": 0, "remaining": 30, "exceeded": False}
    assert not ledger.exists()


def test_main_request_budget_status_reads_existing_ledger(tmp_path, capsys) -> None:
    from im_archive_cli.imx_cli import main

    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"limit": 30, "used": 7, "remaining": 23}), encoding="utf-8")

    rc = main(["request-budget", "status", "--request-budget", "30", "--request-ledger", str(ledger)])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["used"] == 7
    assert payload["remaining"] == 23
    assert payload["exceeded"] is False


def test_main_request_budget_status_marks_exceeded_ledger(tmp_path, capsys) -> None:
    from im_archive_cli.imx_cli import main

    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"limit": 30, "used": 31, "remaining": 0}), encoding="utf-8")

    rc = main(["request-budget", "status", "--request-budget", "30", "--request-ledger", str(ledger)])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["used"] == 31
    assert payload["remaining"] == 0
    assert payload["exceeded"] is True


def test_main_preflight_reports_missing_ctrip_targets(monkeypatch, tmp_path, capsys) -> None:
    from im_archive_cli import imx_cli
    from im_archive_cli.config import save_config
    from im_archive_cli.imx_cli import main

    def fake_proxy_status(_base_url):
        return {
            "via": "proxy",
            "targetCount": 1,
            "vbookingTargetCount": 0,
            "detailTargetCount": 0,
            "readyForPageContextCollect": False,
            "readyForDetailPageInspection": False,
            "targets": [],
        }

    monkeypatch.setattr(imx_cli, "inspect_proxy_status", fake_proxy_status)
    ledger = tmp_path / "ledger.json"
    cfg = make_cfg(tmp_path)
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=secret", encoding="utf-8")
    cfg.ctrip_cookie_header_file = str(cookie_file)
    cfg.ctrip_auth_json = str(tmp_path / "missing.json")
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg_path, cfg)

    rc = main(["--config", str(cfg_path), "preflight", "--request-budget", "30", "--request-ledger", str(ledger), "--via", "proxy"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ready"] is False
    assert payload["requestBudget"]["remaining"] == 30
    assert payload["auth"]["selected"] == str(cookie_file)
    assert "secret" not in json.dumps(payload)
    assert "当前浏览器没有 vbooking.ctrip.com 或 imvendor.ctrip.com target" in payload["issues"]


def test_main_preflight_reports_exhausted_budget(monkeypatch, tmp_path, capsys) -> None:
    from im_archive_cli import imx_cli
    from im_archive_cli.config import save_config
    from im_archive_cli.imx_cli import main

    def fake_proxy_status(_base_url):
        return {
            "via": "proxy",
            "targetCount": 1,
            "vbookingTargetCount": 1,
            "detailTargetCount": 0,
            "readyForPageContextCollect": True,
            "readyForDetailPageInspection": False,
            "targets": [],
        }

    monkeypatch.setattr(imx_cli, "inspect_proxy_status", fake_proxy_status)
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"limit": 30, "used": 30, "remaining": 0}), encoding="utf-8")
    cfg = make_cfg(tmp_path)
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=secret", encoding="utf-8")
    cfg.ctrip_cookie_header_file = str(cookie_file)
    cfg.ctrip_auth_json = str(tmp_path / "missing.json")
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg_path, cfg)

    rc = main(["--config", str(cfg_path), "preflight", "--request-budget", "30", "--request-ledger", str(ledger), "--via", "proxy"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ready"] is False
    assert payload["requestBudget"]["remaining"] == 0
    assert payload["requestBudget"]["exceeded"] is False
    assert "携程接口请求账本剩余额度为 0" in payload["issues"]


def test_main_preflight_reports_exceeded_budget(monkeypatch, tmp_path, capsys) -> None:
    from im_archive_cli import imx_cli
    from im_archive_cli.config import save_config
    from im_archive_cli.imx_cli import main

    def fake_proxy_status(_base_url):
        return {
            "via": "proxy",
            "targetCount": 1,
            "vbookingTargetCount": 1,
            "detailTargetCount": 0,
            "readyForPageContextCollect": True,
            "readyForDetailPageInspection": False,
            "targets": [],
        }

    monkeypatch.setattr(imx_cli, "inspect_proxy_status", fake_proxy_status)
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"limit": 30, "used": 31, "remaining": 0}), encoding="utf-8")
    cfg = make_cfg(tmp_path)
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("foo=secret", encoding="utf-8")
    cfg.ctrip_cookie_header_file = str(cookie_file)
    cfg.ctrip_auth_json = str(tmp_path / "missing.json")
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg_path, cfg)

    rc = main(["--config", str(cfg_path), "preflight", "--request-budget", "30", "--request-ledger", str(ledger), "--via", "proxy"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["requestBudget"]["exceeded"] is True
    assert "携程接口请求账本已超过上限，必须停止目标实现" in payload["issues"]


def test_main_preflight_reports_missing_auth_sources(monkeypatch, tmp_path, capsys) -> None:
    from im_archive_cli import imx_cli
    from im_archive_cli.config import save_config
    from im_archive_cli.imx_cli import main

    def fake_proxy_status(_base_url):
        return {
            "via": "proxy",
            "targetCount": 1,
            "vbookingTargetCount": 1,
            "detailTargetCount": 0,
            "readyForPageContextCollect": True,
            "readyForDetailPageInspection": False,
            "targets": [],
        }

    monkeypatch.setattr(imx_cli, "inspect_proxy_status", fake_proxy_status)
    cfg = make_cfg(tmp_path)
    cfg.ctrip_cookie_header_file = str(tmp_path / "missing-cookie.txt")
    cfg.ctrip_auth_json = str(tmp_path / "missing-auth.json")
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg_path, cfg)
    ledger = tmp_path / "ledger.json"

    rc = main(["--config", str(cfg_path), "preflight", "--request-budget", "30", "--request-ledger", str(ledger), "--via", "proxy"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["auth"]["selected"] is None
    assert "未找到可用 ctrip-cli-sessions 请求头/登录态文件" in payload["issues"]
