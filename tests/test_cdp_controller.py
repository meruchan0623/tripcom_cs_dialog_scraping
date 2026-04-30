from im_archive_cli.cdp_plugin_controller import CDPPluginController, ChromeRuntime, parse_extension_id_from_target_url


def test_parse_extension_id_from_target_url() -> None:
    url = "chrome-extension://abcdefghijklmnopabcdefghijklmnop/background.js"
    ext_id = parse_extension_id_from_target_url(url)
    assert ext_id == "abcdefghijklmnopabcdefghijklmnop"


def test_ensure_chrome_does_not_force_restart_unknown_cdp_owner(tmp_path, monkeypatch) -> None:
    controller = object.__new__(CDPPluginController)
    controller.cfg = type("Cfg", (), {"cdp_port": 9222})()

    monkeypatch.setattr(controller, "_load_runtime", lambda: ChromeRuntime(pid=-1, port=9222, started_at=0.0))
    monkeypatch.setattr(controller, "_is_cdp_alive", lambda: True)
    monkeypatch.setattr(controller, "_wait_extension_ready", lambda timeout_sec=5.0: (_ for _ in ()).throw(RuntimeError("未发现扩展 target")))

    called = {"terminate": False, "restart": False}

    monkeypatch.setattr(controller, "_terminate_pid", lambda pid: called.__setitem__("terminate", True))
    monkeypatch.setattr(controller, "_wait_cdp_down", lambda timeout_sec=8.0: None)

    def _start_chrome(*args, **kwargs):
        called["restart"] = True
        return ChromeRuntime(pid=1, port=9222, started_at=0.0)

    monkeypatch.setattr(controller, "start_chrome", _start_chrome)
    monkeypatch.setattr(controller, "_is_pid_alive", lambda pid: False)

    try:
        controller.ensure_chrome(headed=False)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "已有 Chrome" in str(exc)

    assert called["terminate"] is False
    assert called["restart"] is False

