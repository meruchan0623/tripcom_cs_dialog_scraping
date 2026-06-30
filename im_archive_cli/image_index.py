from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _resolve_attachment_path(json_path: Path, attachment: dict[str, Any]) -> str:
    local_path = str(attachment.get("localPath") or "").strip()
    if local_path:
        return str(Path(local_path).expanduser().resolve())

    relative_path = str(attachment.get("relativePath") or "").strip()
    if relative_path:
        candidate = json_path.parent / relative_path
        if candidate.exists():
            return str(candidate.resolve())

    return ""


def build_conversation_image_index(conversation: dict[str, Any], json_path: Path) -> list[dict[str, Any]]:
    session_id = str(conversation.get("sessionId") or "")
    records: list[dict[str, Any]] = []
    messages = conversation.get("messages") or []
    if not isinstance(messages, list):
        return records

    for message in messages:
        if not isinstance(message, dict):
            continue
        attachments = message.get("attachments") or []
        if not isinstance(attachments, list):
            continue
        for attachment in attachments:
            if not isinstance(attachment, dict):
                continue
            if attachment.get("source") != "messageBody":
                continue
            if not attachment.get("src"):
                continue
            records.append(
                {
                    "sessionId": session_id,
                    "sequence": int(message.get("sequence") or 0),
                    "messageType": str(message.get("messageType") or ""),
                    "source": "messageBody",
                    "downloadStatus": str(attachment.get("downloadStatus") or ""),
                    "src": str(attachment.get("src") or ""),
                    "localPath": str(attachment.get("localPath") or ""),
                    "relativePath": str(attachment.get("relativePath") or ""),
                    "resolvedPath": _resolve_attachment_path(json_path, attachment),
                }
            )
    return records


def write_conversation_image_index(conversation: dict[str, Any], json_path: Path) -> Path:
    payload = {
        "sessionId": str(conversation.get("sessionId") or ""),
        "jsonPath": str(json_path.resolve()),
        "images": build_conversation_image_index(conversation, json_path),
    }
    index_path = json_path.with_name(f"{json_path.stem}.image-index.json")
    index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return index_path
