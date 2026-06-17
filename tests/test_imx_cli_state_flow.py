from __future__ import annotations

from pathlib import Path

from im_archive_cli.config import AppConfig
from im_archive_cli.imx_cli import cmd_import_links, cmd_roles_list, cmd_roles_select, cmd_run_export, cmd_state_watch
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
