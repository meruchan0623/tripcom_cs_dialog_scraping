from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_scan_im_module():
    script_path = Path(__file__).resolve().parents[1] / "skills" / "ctrip-im-parser" / "scripts" / "scan_im.py"
    spec = importlib.util.spec_from_file_location("ctrip_im_parser_scan_im", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_load_all_sessions_skips_image_index_sidecars(tmp_path: Path) -> None:
    scan_im = _load_scan_im_module()
    conversation = {
        "sessionId": "s1",
        "csName": "Alice",
        "messages": [
            {
                "sequence": 1,
                "senderRole": "buyer",
                "messageType": "text",
                "text": "hello",
                "timestampText": "2026-06-16 09:00:00",
                "attachments": [],
            }
        ],
    }
    (tmp_path / "IMChatlogExport_20260616090000_s1_Alice.json").write_text(
        json.dumps(conversation),
        encoding="utf-8",
    )
    (tmp_path / "IMChatlogExport_20260616090000_s1_Alice.image-index.json").write_text(
        json.dumps({"sessionId": "s1", "jsonPath": "ignored", "images": []}),
        encoding="utf-8",
    )

    sessions = scan_im.load_all_sessions(str(tmp_path))

    assert len(sessions) == 1
    assert sessions[0]["session_id"] == "s1"
    assert sessions[0]["message_count"] == 1


def test_load_all_sessions_exposes_inline_image_refs(tmp_path: Path) -> None:
    scan_im = _load_scan_im_module()
    conversation = {
        "sessionId": "s2",
        "csName": "Bob",
        "messages": [
            {
                "sequence": 3,
                "senderRole": "buyer",
                "messageType": "image",
                "text": "[图片]",
                "timestampText": "2026-06-16 09:01:00",
                "attachments": [
                    {
                        "source": "messageBody",
                        "src": "https://cdn.example.com/a.jpg",
                        "relativePath": "IMChatlogExport_20260616090100_s2_Bob_assets/seq0003_a.jpg",
                        "downloadStatus": "downloaded",
                    },
                    {
                        "source": "card",
                        "src": "https://cdn.example.com/card.jpg",
                    },
                ],
            }
        ],
    }
    (tmp_path / "IMChatlogExport_20260616090100_s2_Bob.json").write_text(
        json.dumps(conversation),
        encoding="utf-8",
    )

    sessions = scan_im.load_all_sessions(str(tmp_path))

    message = sessions[0]["messages"][0]
    assert message["has_attachments"] is True
    assert message["inline_images"] == [
        {
            "source": "messageBody",
            "src": "https://cdn.example.com/a.jpg",
            "local_path": "",
            "relative_path": "IMChatlogExport_20260616090100_s2_Bob_assets/seq0003_a.jpg",
            "download_status": "downloaded",
        }
    ]
    assert sessions[0]["inline_image_count"] == 1
    assert sessions[0]["failed_inline_image_count"] == 0
