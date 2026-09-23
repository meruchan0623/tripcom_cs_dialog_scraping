from __future__ import annotations

from pathlib import Path

from im_archive_cli.config import AppConfig, save_config
import im_archive_cli.imx_cli as imx_cli


def test_bad_review_cli_dispatch_is_http_only_does_not_touch_state_or_browser(monkeypatch, tmp_path: Path) -> None:
    cfg = AppConfig(state_file=str(tmp_path / "state.json"), output_dir=str(tmp_path / "out"))
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg_path, cfg)
    captured: dict = {}

    def fake_run(config, **kwargs):
        captured.update(kwargs)
        return {"status": "success", "summary_path": str(tmp_path / "summary.json"), "xlsx_path": str(tmp_path / "report.xlsx")}

    monkeypatch.setattr(imx_cli, "run_bad_review_export", fake_run)
    monkeypatch.setattr(imx_cli.CtripImCdpFetchClient, "__init__", lambda self, *a, **k: (_ for _ in ()).throw(AssertionError("browser/CDP must not be used")))
    monkeypatch.setattr(imx_cli, "_make_controller", lambda *a, **k: (_ for _ in ()).throw(AssertionError("browser controller must not be used")))

    rc = imx_cli.main([
        "--config", str(cfg_path), "run", "bad-reviews",
        "--start-date", "2026-08-01", "--end-date", "2026-08-01",
        "--output-dir", str(tmp_path / "custom"), "--page-size", "100",
        "--max-pages", "5", "--request-interval-sec", "0", "--no-chat", "--mark-landed", "--strict",
    ])

    assert rc == 0
    assert captured["start_date"] == "2026-08-01"
    assert captured["end_date"] == "2026-08-01"
    assert captured["output_dir"] == Path(tmp_path / "custom")
    assert captured["no_chat"] is True
    assert captured["mark_landed"] is True
    assert captured["strict"] is True
    assert not Path(cfg.state_file).exists()


def test_bad_review_cli_strict_returns_nonzero_for_partial(monkeypatch, tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yaml"
    save_config(cfg_path, AppConfig(output_dir=str(tmp_path / "out")))
    monkeypatch.setattr(imx_cli, "run_bad_review_export", lambda *a, **k: {"status": "partial", "summary_path": "x", "xlsx_path": "y"})

    rc = imx_cli.main(["--config", str(cfg_path), "run", "bad-reviews", "--start-date", "2026-08-01", "--end-date", "2026-08-01", "--strict"])

    assert rc == 1
