from __future__ import annotations

import importlib.util
import json
import threading
import time
from typing import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from im_archive_cli.config import AppConfig
from im_archive_cli.ctrip_http import CtripRequestBudgetExceeded
from im_archive_cli.http_export import export_structured_via_http
from im_archive_cli.imx_cli import cmd_run_export, main
from im_archive_cli.models import SessionRecord
from im_archive_cli.selftest import run_http_export_selftest
from im_archive_cli.state import StateStore


class DummyLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str) -> None:
        self.lines.append(message)


class FakeDetailClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_conversation(self, session: SessionRecord) -> dict:
        self.calls.append(session.session_id)
        return {
            "sessionId": session.session_id,
            "csName": session.cs_name,
            "detailUrl": session.detail_url,
            "title": "",
            "createTime": session.create_time,
            "exportedAt": "2026-06-18T00:00:00Z",
            "messages": [
                {
                    "sessionId": session.session_id,
                    "csName": session.cs_name,
                    "detailUrl": session.detail_url,
                    "sequence": 1,
                    "timestampText": "2026-06-16 09:00:00",
                    "senderRole": "buyer",
                    "senderName": "Guest",
                    "messageType": "text",
                    "text": "hello",
                    "rawHtml": "",
                    "attachments": [],
                }
            ],
        }


def test_http_defaults_use_max_probed_page_size_and_batch_throttle() -> None:
    cfg = AppConfig()

    assert cfg.page_size == 1000
    assert cfg.concurrency == 4
    assert cfg.window_sec == 2


def test_export_structured_via_http_batches_four_requests_per_half_second(tmp_path: Path) -> None:
    cfg = AppConfig(output_dir=str(tmp_path / "out"), failures_file=str(tmp_path / "failures.jsonl"))
    sessions = [
        SessionRecord(session_id=f"s{i}", cs_name="Alice", create_time="2026-06-16 09:00:00").normalized()
        for i in range(1, 6)
    ]

    class TimedClient:
        def __init__(self) -> None:
            self.calls: list[float] = []

        def fetch_conversation(self, session: SessionRecord) -> dict:
            self.calls.append(time.monotonic())
            return {
                "sessionId": session.session_id,
                "csName": session.cs_name,
                "detailUrl": session.detail_url,
                "createTime": session.create_time,
                "messages": [
                    {
                        "sequence": 1,
                        "timestampText": "2026-06-16 09:00:00",
                        "senderRole": "buyer",
                        "senderName": "Guest",
                        "messageType": "text",
                        "text": "hello",
                        "rawHtml": "",
                        "attachments": [],
                    }
                ],
            }

    client = TimedClient()

    success, failed = export_structured_via_http(client, cfg, sessions, ["json"], lambda _msg: None)

    assert (success, failed) == (5, 0)
    assert len(client.calls) == 5
    assert max(client.calls[:4]) - min(client.calls[:4]) < 0.5
    assert client.calls[4] - min(client.calls[:4]) >= 0.35


def test_export_structured_via_http_uses_dedicated_clients_for_http_detail_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = AppConfig(output_dir=str(tmp_path / "out"), failures_file=str(tmp_path / "failures.jsonl"), window_sec=0, concurrency=2)
    sessions = [
        SessionRecord(session_id=f"s{i}", cs_name="Alice", create_time="2026-06-16 09:00:00").normalized()
        for i in range(1, 5)
    ]

    class TrackingDetailClient:
        init_records: list[float] = []
        request_touched: list[int] = []

        def __init__(
            self,
            cfg: AppConfig,
            log: Callable[[str], None] | None = None,
            request_interval_sec: float = 0.5,
            request_budget: object | None = None,
        ) -> None:
            self.cfg = cfg
            self.log = log
            self.request_budget = request_budget
            TrackingDetailClient.init_records.append(float(request_interval_sec))

        def fetch_conversation(self, session: SessionRecord) -> dict:
            TrackingDetailClient.request_touched.append(id(self))
            return {
                "sessionId": session.session_id,
                "csName": session.cs_name,
                "detailUrl": session.detail_url,
                "messages": [
                    {
                        "sequence": 1,
                        "timestampText": "2026-06-16 09:00:00",
                        "senderRole": "buyer",
                        "senderName": "Guest",
                        "messageType": "text",
                        "text": "hello",
                        "rawHtml": "",
                        "attachments": [],
                    }
                ],
            }

    monkeypatch.setattr("im_archive_cli.http_export.CtripImDetailHttpClient", TrackingDetailClient)

    seed_client = TrackingDetailClient(cfg)
    success, failed = export_structured_via_http(seed_client, cfg, sessions, ["json"], lambda _msg: None)

    assert (success, failed) == (4, 0)
    assert len(set(TrackingDetailClient.request_touched)) == len(sessions)
    assert all(interval == 0 for interval in TrackingDetailClient.init_records[1:])


def _start_local_detail_server(requests_seen: list[dict]) -> HTTPServer:
    class DetailHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            requests_seen.append({"path": self.path, "body": body, "cookie": self.headers.get("cookie")})
            payload = {
                "ResponseStatus": {"Ack": "Success"},
                "total": 1,
                "messageList": [
                    {
                        "msgContent": "hello from local endpoint",
                        "sendTime": "2026-06-16 09:00:00",
                        "senderType": "customer",
                        "sendName": "Guest",
                    }
                ],
            }
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), DetailHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_export_structured_via_http_writes_json_and_markdown(tmp_path: Path) -> None:
    cfg = AppConfig(output_dir=str(tmp_path / "out"), failures_file=str(tmp_path / "failures.jsonl"), window_sec=0)
    session = SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 09:00:00").normalized()
    logger = DummyLogger()
    client = FakeDetailClient()

    success, failed = export_structured_via_http(client, cfg, [session], ["json", "markdown"], logger.info)

    assert (success, failed) == (1, 0)
    json_files = list((tmp_path / "out").rglob("*.json"))
    md_files = list((tmp_path / "out").rglob("*.md"))
    assert len(json_files) == 1
    assert len(md_files) == 1
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert data["sessionId"] == "s1"
    assert data["messages"][0]["text"] == "hello"
    assert "# 会话 s1" in md_files[0].read_text(encoding="utf-8")


def test_export_structured_via_http_downloads_images_and_renders_markdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = AppConfig(
        output_dir=str(tmp_path / "out"),
        failures_file=str(tmp_path / "failures.jsonl"),
        window_sec=0,
        download_images=True,
        image_max_workers=1,
        image_request_interval_sec=0,
    )
    session = SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 09:00:00").normalized()

    class ImageMessageClient:
        def fetch_conversation(self, session: SessionRecord) -> dict:
            return {
                "sessionId": session.session_id,
                "csName": session.cs_name,
                "detailUrl": session.detail_url,
                "messages": [
                    {
                        "sequence": 1,
                        "timestampText": "2026-06-16 09:00:00",
                        "senderRole": "buyer",
                        "senderName": "Guest",
                        "messageType": "image",
                        "text": "",
                        "rawHtml": "",
                        "attachments": [
                            {
                                "src": "https://example.com/placeholder.png",
                                "thumbSrc": "https://example.com/placeholder-thumb.png",
                                "source": "messageBody",
                            }
                        ],
                    }
                ],
            }

    def fake_download_conversation_images(data: dict, conversation_dir: str | Path, base_name: str, _config: AppConfig, _log) -> None:
        attachments = data["messages"][0]["attachments"]
        asset_dir = Path(conversation_dir) / f"{base_name}_assets"
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset_path = asset_dir / "seq0001_test.jpg"
        asset_path.write_bytes(b"fake")
        attachments[0]["localPath"] = str(asset_path)
        attachments[0]["relativePath"] = f"{base_name}_assets/seq0001_test.jpg"
        attachments[0]["downloadStatus"] = "downloaded"

    monkeypatch.setattr(
        "im_archive_cli.http_export.download_conversation_images",
        fake_download_conversation_images,
    )

    success, failed = export_structured_via_http(ImageMessageClient(), cfg, [session], ["json", "markdown"], lambda _msg: None)

    assert (success, failed) == (1, 0)
    json_files = list((tmp_path / "out").rglob("*.json"))
    md_files = list((tmp_path / "out").rglob("*.md"))
    assert len(json_files) == 1
    assert len(md_files) == 1
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    rel_path = data["messages"][0]["attachments"][0]["relativePath"]
    assert rel_path == "IMChatlogExport_20260616090000_s1_Alice_assets/seq0001_test.jpg"
    assert rel_path.endswith("_assets/seq0001_test.jpg")
    markdown = md_files[0].read_text(encoding="utf-8")
    assert f"![图片]({rel_path})" in markdown


def test_export_structured_via_http_aborts_on_budget_exhaustion(tmp_path: Path) -> None:
    cfg = AppConfig(output_dir=str(tmp_path / "out"), failures_file=str(tmp_path / "failures.jsonl"), window_sec=0, concurrency=1)
    sessions = [
        SessionRecord(session_id="s1", cs_name="Alice").normalized(),
        SessionRecord(session_id="s2", cs_name="Alice").normalized(),
    ]

    class BudgetClient:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_conversation(self, session: SessionRecord) -> dict:
            self.calls += 1
            raise CtripRequestBudgetExceeded("limit=1")

    client = BudgetClient()

    try:
        export_structured_via_http(client, cfg, sessions, ["json"], lambda _msg: None)
    except CtripRequestBudgetExceeded:
        pass
    else:
        raise AssertionError("budget exhaustion should abort export")

    assert client.calls == 1


def test_export_structured_via_http_treats_empty_messages_as_failure(tmp_path: Path) -> None:
    cfg = AppConfig(output_dir=str(tmp_path / "out"), failures_file=str(tmp_path / "failures.jsonl"), window_sec=0)
    session = SessionRecord(session_id="s1", cs_name="Alice").normalized()

    class EmptyClient:
        def fetch_conversation(self, session: SessionRecord) -> dict:
            return {
                "sessionId": session.session_id,
                "csName": session.cs_name,
                "detailUrl": session.detail_url,
                "messages": [],
            }

    success, failed = export_structured_via_http(EmptyClient(), cfg, [session], ["json"], lambda _msg: None)

    assert (success, failed) == (0, 1)
    assert "messages 为空" in (tmp_path / "failures.jsonl").read_text(encoding="utf-8")
    assert not list((tmp_path / "out").rglob("*.json"))


def test_structured_export_via_cdp_is_rejected_by_parser(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cfg = AppConfig(
        state_file=str(tmp_path / "state.json"),
        output_dir=str(tmp_path / "out"),
        log_dir=str(tmp_path / "logs"),
        failures_file=str(tmp_path / "failures.jsonl"),
        window_sec=0,
    )
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg.__dict__, sort_keys=False, allow_unicode=True), encoding="utf-8")
    StateStore(Path(cfg.state_file)).set_sessions(
        [SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 09:00:00")],
        auto_select_all=True,
    )

    with pytest.raises(SystemExit) as exc:
        main(
            [
                "--config",
                str(cfg_path),
                "run",
                "export",
                "--kind",
                "structured",
                "--via",
                "cdp",
            ]
        )

    captured = capsys.readouterr()
    assert exc.value.code == 2
    assert "invalid choice: 'cdp'" in captured.err


def test_dom_structured_export_modules_are_removed() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    removed_dom_script = "detail" + "-page.js"

    assert importlib.util.find_spec("im_archive_cli.export_structured") is None
    assert not (repo_root / removed_dom_script).exists()


def test_cli_http_export_uses_real_requests_client_against_local_endpoint(tmp_path: Path) -> None:
    requests_seen: list[dict] = []
    server = _start_local_detail_server(requests_seen)
    try:
        cookie_file = tmp_path / "cookie.txt"
        cookie_file.write_text("foo=bar", encoding="utf-8")
        cfg = AppConfig(
            state_file=str(tmp_path / "state.json"),
            output_dir=str(tmp_path / "out"),
            failures_file=str(tmp_path / "failures.jsonl"),
            ctrip_cookie_header_file=str(cookie_file),
            ctrip_auth_json=str(tmp_path / "missing.json"),
            ctrip_im_detail_messages_url=f"http://127.0.0.1:{server.server_port}/detail",
            window_sec=0,
        )
        StateStore(Path(cfg.state_file)).set_sessions(
            [SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 09:00:00")],
            auto_select_all=True,
        )
        logger = DummyLogger()

        rc = cmd_run_export(cfg, logger, "structured", formats="json,markdown", via="http", request_budget=1)

        assert rc == 0
        assert len(requests_seen) == 1
        assert requests_seen[0]["body"]["sessionId"] == "s1"
        assert requests_seen[0]["body"]["head"]["cver"] == "2"
        assert {"name": "amp-account-source", "value": "vbk"} in requests_seen[0]["body"]["head"]["extension"]
        assert requests_seen[0]["cookie"] == "foo=bar"
        assert any("携程接口请求计数: used=1, limit=1" in line for line in logger.lines)

        json_files = list((tmp_path / "out").rglob("*.json"))
        md_files = list((tmp_path / "out").rglob("*.md"))
        assert len(json_files) == 1
        assert len(md_files) == 1
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert data["messages"][0]["text"] == "hello from local endpoint"
        assert "hello from local endpoint" in md_files[0].read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_main_http_export_cli_entrypoint_against_local_endpoint(tmp_path: Path) -> None:
    requests_seen: list[dict] = []
    server = _start_local_detail_server(requests_seen)
    try:
        cookie_file = tmp_path / "cookie.txt"
        cookie_file.write_text("foo=bar", encoding="utf-8")
        cfg = AppConfig(
            state_file=str(tmp_path / "state.json"),
            output_dir=str(tmp_path / "out"),
            log_dir=str(tmp_path / "logs"),
            failures_file=str(tmp_path / "failures.jsonl"),
            ctrip_cookie_header_file=str(cookie_file),
            ctrip_auth_json=str(tmp_path / "missing.json"),
            ctrip_im_detail_messages_url=f"http://127.0.0.1:{server.server_port}/detail",
            window_sec=0,
        )
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg.__dict__, sort_keys=False, allow_unicode=True), encoding="utf-8")
        StateStore(Path(cfg.state_file)).set_sessions(
            [SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 09:00:00")],
            auto_select_all=True,
        )

        rc = main(
            [
                "--config",
                str(cfg_path),
                "run",
                "export",
                "--kind",
                "structured",
                "--via",
                "http",
                "--formats",
                "json,markdown",
                "--request-budget",
                "1",
            ]
        )

        assert rc == 0
        assert len(requests_seen) == 1
        assert requests_seen[0]["body"]["sessionId"] == "s1"
        assert list((tmp_path / "out").rglob("*.json"))
        assert list((tmp_path / "out").rglob("*.md"))
    finally:
        server.shutdown()
        server.server_close()


def test_main_http_export_updates_request_ledger(tmp_path: Path) -> None:
    requests_seen: list[dict] = []
    server = _start_local_detail_server(requests_seen)
    try:
        cookie_file = tmp_path / "cookie.txt"
        cookie_file.write_text("foo=bar", encoding="utf-8")
        ledger_path = tmp_path / "request-ledger.json"
        ledger_path.write_text(json.dumps({"limit": 3, "used": 1, "remaining": 2}), encoding="utf-8")
        cfg = AppConfig(
            state_file=str(tmp_path / "state.json"),
            output_dir=str(tmp_path / "out"),
            log_dir=str(tmp_path / "logs"),
            failures_file=str(tmp_path / "failures.jsonl"),
            ctrip_cookie_header_file=str(cookie_file),
            ctrip_auth_json=str(tmp_path / "missing.json"),
            ctrip_im_detail_messages_url=f"http://127.0.0.1:{server.server_port}/detail",
            window_sec=0,
        )
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg.__dict__, sort_keys=False, allow_unicode=True), encoding="utf-8")
        StateStore(Path(cfg.state_file)).set_sessions(
            [SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 09:00:00")],
            auto_select_all=True,
        )

        rc = main(
            [
                "--config",
                str(cfg_path),
                "run",
                "export",
                "--kind",
                "structured",
                "--via",
                "http",
                "--request-budget",
                "3",
                "--request-ledger",
                str(ledger_path),
            ]
        )

        assert rc == 0
        assert len(requests_seen) == 1
        assert json.loads(ledger_path.read_text(encoding="utf-8")) == {"limit": 3, "used": 2, "remaining": 1, "exceeded": False}
    finally:
        server.shutdown()
        server.server_close()


def test_main_http_export_stops_before_request_when_budget_less_than_selected_sessions(tmp_path: Path, capsys) -> None:
    requests_seen: list[dict] = []
    server = _start_local_detail_server(requests_seen)
    try:
        cookie_file = tmp_path / "cookie.txt"
        cookie_file.write_text("foo=bar", encoding="utf-8")
        ledger_path = tmp_path / "request-ledger.json"
        ledger_path.write_text(json.dumps({"limit": 2, "used": 1, "remaining": 1}), encoding="utf-8")
        cfg = AppConfig(
            state_file=str(tmp_path / "state.json"),
            output_dir=str(tmp_path / "out"),
            log_dir=str(tmp_path / "logs"),
            failures_file=str(tmp_path / "failures.jsonl"),
            ctrip_cookie_header_file=str(cookie_file),
            ctrip_auth_json=str(tmp_path / "missing.json"),
            ctrip_im_detail_messages_url=f"http://127.0.0.1:{server.server_port}/detail",
            window_sec=0,
        )
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg.__dict__, sort_keys=False, allow_unicode=True), encoding="utf-8")
        StateStore(Path(cfg.state_file)).set_sessions(
            [
                SessionRecord(session_id="s1", cs_name="Alice", create_time="2026-06-16 09:00:00"),
                SessionRecord(session_id="s2", cs_name="Alice", create_time="2026-06-16 09:05:00"),
            ],
            auto_select_all=True,
        )

        rc = main(
            [
                "--config",
                str(cfg_path),
                "run",
                "export",
                "--kind",
                "structured",
                "--via",
                "http",
                "--request-budget",
                "2",
                "--request-ledger",
                str(ledger_path),
            ]
        )

        captured = capsys.readouterr()
        assert rc == 1
        assert "剩余额度不足" in captured.err
        assert len(requests_seen) == 0
        assert json.loads(ledger_path.read_text(encoding="utf-8")) == {"limit": 2, "used": 1, "remaining": 1}
    finally:
        server.shutdown()
        server.server_close()


def test_http_export_selftest_uses_local_endpoint_only(tmp_path: Path) -> None:
    payload = run_http_export_selftest(tmp_path / "selftest", request_budget=1)

    assert payload["ok"] is True
    assert payload["localEndpoint"].startswith("http://127.0.0.1:")
    assert payload["localRequests"] == 1
    assert payload["mockBudget"]["used"] == 1
    assert payload["mockBudget"]["remaining"] == 0
    assert len(payload["outputs"]["json"]) == 1
    assert len(payload["outputs"]["markdown"]) == 1


def test_main_selftest_http_export_entrypoint(tmp_path: Path, capsys) -> None:
    rc = main(["self-test", "http-export", "--output-dir", str(tmp_path / "selftest"), "--request-budget", "1"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["localEndpoint"].startswith("http://127.0.0.1:")
    assert payload["localRequests"] == 1


def test_main_selftest_rejects_budget_over_30(tmp_path: Path, capsys) -> None:
    rc = main(["self-test", "http-export", "--output-dir", str(tmp_path / "selftest"), "--request-budget", "31"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "self-test request-budget 不能超过 30" in captured.err
    assert "Traceback" not in captured.err
