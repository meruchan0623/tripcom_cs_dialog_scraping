from __future__ import annotations

import json
from pathlib import Path

from im_archive_cli.image_index import build_conversation_image_index, write_conversation_image_index


def test_build_conversation_image_index_prefers_local_and_relative_paths(tmp_path: Path) -> None:
    conversation_dir = tmp_path / "conversation"
    conversation_dir.mkdir()
    asset_dir = conversation_dir / "IMChatlogExport_20260616090000_s1_Alice_assets"
    asset_dir.mkdir()
    image_path = asset_dir / "seq0001_abc123.jpg"
    image_path.write_bytes(b"fake")

    conversation = {
        "sessionId": "s1",
        "messages": [
            {
                "sequence": 1,
                "messageType": "image",
                "attachments": [
                    {
                        "source": "messageBody",
                        "src": "https://cdn.example.com/a.jpg",
                        "localPath": str(image_path.resolve()),
                        "relativePath": "IMChatlogExport_20260616090000_s1_Alice_assets/seq0001_abc123.jpg",
                        "downloadStatus": "downloaded",
                    }
                ],
            }
        ],
    }

    records = build_conversation_image_index(
        conversation,
        json_path=conversation_dir / "IMChatlogExport_20260616090000_s1_Alice.json",
    )

    assert records == [
        {
            "sessionId": "s1",
            "sequence": 1,
            "messageType": "image",
            "source": "messageBody",
            "downloadStatus": "downloaded",
            "src": "https://cdn.example.com/a.jpg",
            "localPath": str(image_path.resolve()),
            "relativePath": "IMChatlogExport_20260616090000_s1_Alice_assets/seq0001_abc123.jpg",
            "resolvedPath": str(image_path.resolve()),
        }
    ]


def test_build_conversation_image_index_skips_non_message_body_attachments(tmp_path: Path) -> None:
    json_path = tmp_path / "conversation.json"
    conversation = {
        "sessionId": "s2",
        "messages": [
            {
                "sequence": 2,
                "messageType": "image",
                "attachments": [
                    {"source": "card", "src": "https://cdn.example.com/card.jpg"},
                    {"source": "messageBody", "thumbSrc": "https://cdn.example.com/thumb.jpg"},
                ],
            }
        ],
    }

    assert build_conversation_image_index(conversation, json_path=json_path) == []


def test_write_conversation_image_index_writes_sidecar_json(tmp_path: Path) -> None:
    json_path = tmp_path / "IMChatlogExport_20260616090000_s1_Alice.json"
    json_path.write_text("{}", encoding="utf-8")
    conversation = {
        "sessionId": "s1",
        "messages": [
            {
                "sequence": 3,
                "messageType": "image",
                "attachments": [
                    {
                        "source": "messageBody",
                        "src": "https://cdn.example.com/missing.jpg",
                        "downloadStatus": "failed",
                    }
                ],
            }
        ],
    }

    index_path = write_conversation_image_index(conversation, json_path)

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert index_path.name == "IMChatlogExport_20260616090000_s1_Alice.image-index.json"
    assert payload["sessionId"] == "s1"
    assert payload["images"][0]["downloadStatus"] == "failed"
    assert payload["images"][0]["resolvedPath"] == ""
