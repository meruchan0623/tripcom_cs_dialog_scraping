from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from .config import AppConfig
from .ctrip_http import CtripImDetailHttpClient, CtripRequestBudget
from .http_export import export_structured_via_http
from .models import SessionRecord
from .state import StateStore


class _SelfTestDetailHandler(BaseHTTPRequestHandler):
    requests_seen: list[dict[str, Any]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.requests_seen.append({"path": self.path, "body": body, "cookie": self.headers.get("cookie")})
        payload = {
            "ResponseStatus": {"Ack": "Success"},
            "total": 1,
            "messageList": [
                {
                    "msgContent": "hello from local self-test endpoint",
                    "sendTime": "2026-06-18 09:00:00",
                    "senderType": "customer",
                    "sendName": "Local Guest",
                }
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def run_http_export_selftest(base_dir: Path, request_budget: int = 1) -> dict[str, Any]:
    base_dir.mkdir(parents=True, exist_ok=True)
    run_dir = base_dir / time.strftime("http_export_%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    _SelfTestDetailHandler.requests_seen = []
    server = HTTPServer(("127.0.0.1", 0), _SelfTestDetailHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        cookie_file = run_dir / "cookie.txt"
        cookie_file.write_text("selftest=local", encoding="utf-8")
        ledger_path = run_dir / "request-ledger.json"
        cfg = AppConfig(
            state_file=str(run_dir / "state.json"),
            output_dir=str(run_dir / "out"),
            log_dir=str(run_dir / "logs"),
            failures_file=str(run_dir / "failures.jsonl"),
            ctrip_cookie_header_file=str(cookie_file),
            ctrip_auth_json=str(run_dir / "missing-auth.json"),
            ctrip_im_detail_messages_url=f"http://127.0.0.1:{server.server_port}/detail",
            window_sec=0,
        )
        session = SessionRecord(session_id="selftest-session-1", cs_name="SelfTest", create_time="2026-06-18 09:00:00")
        StateStore(Path(cfg.state_file)).set_sessions([session], auto_select_all=True)
        budget = CtripRequestBudget(request_budget, ledger_path=ledger_path)
        client = CtripImDetailHttpClient(cfg, request_interval_sec=0, request_budget=budget)
        success, failed = export_structured_via_http(client, cfg, [session.normalized()], ["json", "markdown"], lambda _msg: None)
        json_files = [str(path) for path in Path(cfg.output_dir).rglob("*.json") if not path.name.endswith(".image-index.json")]
        image_index_files = [str(path) for path in Path(cfg.output_dir).rglob("*.image-index.json")]
        markdown_files = [str(path) for path in Path(cfg.output_dir).rglob("*.md")]
        return {
            "ok": success == 1 and failed == 0 and len(_SelfTestDetailHandler.requests_seen) == 1,
            "runDir": str(run_dir),
            "localEndpoint": cfg.ctrip_im_detail_messages_url,
            "localRequests": len(_SelfTestDetailHandler.requests_seen),
            "success": success,
            "failed": failed,
            "mockBudget": {
                "limit": budget.limit,
                "used": budget.used,
                "remaining": budget.remaining,
                "ledger": str(ledger_path),
            },
            "outputs": {
                "json": json_files,
                "imageIndex": image_index_files,
                "markdown": markdown_files,
            },
        }
    finally:
        server.shutdown()
        server.server_close()
