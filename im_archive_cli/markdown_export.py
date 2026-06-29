from __future__ import annotations

from .models import SessionRecord


def create_markdown(meta: SessionRecord, messages: list[dict]) -> str:
    lines = [
        f"# 会话 {meta.session_id}",
        "",
        f"- 客服: {meta.cs_name}",
        f"- 链接: {meta.detail_url}",
        f"- 消息数: {len(messages)}",
        "",
    ]
    for message in messages:
        sender = message.get("senderRole", "unknown")
        name = message.get("senderName", "")
        lines.append(f"## {message.get('sequence', '-')}. {sender}{f' / {name}' if name else ''}")
        lines.append("")
        lines.append(f"- 时间: {message.get('timestampText', '-')}")
        lines.append(f"- 类型: {message.get('messageType', '-')}")
        text = message.get("text") or ("[图片消息]" if message.get("messageType") == "image" else "[空内容]")
        lines.append(f"- 文本: {text}")
        attachments = message.get("attachments") or []
        for attachment in attachments:
            path = attachment.get("relativePath") or attachment.get("src") or ""
            if message.get("messageType") == "image":
                lines.append(f"![图片]({path})")
            elif path:
                lines.append(f"- 附件: {path}")
        lines.append("")
    return "\n".join(lines)
