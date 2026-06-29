"""Helpers for parsing inline image attachments from message body payloads."""

from __future__ import annotations

import json
from collections.abc import Iterator
from hashlib import sha256
from json import JSONDecodeError
from typing import Any
from urllib.parse import urlparse

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def _parse_message_body(message_body: str) -> dict[str, Any] | None:
    """Parse a messageBody JSON and only accept object payloads."""
    try:
        data = json.loads(message_body)
    except (TypeError, JSONDecodeError):
        return None

    return data if isinstance(data, dict) else None


def _first_non_empty(value: dict[str, Any], *keys: str) -> str:
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str):
            candidate = candidate.strip()
            if candidate:
                return candidate
    return ""


def _normalize_ext(extension: str) -> str:
    if not extension:
        return ""

    normalized = extension.strip().lower()
    if not normalized.startswith("."):
        normalized = f".{normalized}"

    if normalized == ".jpeg":
        normalized = ".jpg"

    return normalized if normalized in IMAGE_EXTENSIONS else ""


def _ext_from_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rsplit("/", 1)[-1]
    if "." not in path:
        return ""

    extension = f".{path.rsplit('.', 1)[-1].lower()}"
    return _normalize_ext(extension)


def _ext_from_content_type(content_type: str) -> str:
    normalized = content_type.strip().lower()
    if ";" in normalized:
        normalized = normalized.split(";", 1)[0]

    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
    }

    return mapping.get(normalized, "")


def _looks_like_image_url(url: str) -> bool:
    return bool(_ext_from_url(url))


def _coerce_str(value: Any) -> str:
    if value is None:
        return ""

    return str(value).strip()


def extract_inline_image_attachment(message_body: str) -> dict[str, str] | None:
    data = _parse_message_body(message_body)
    if data is None:
        return None

    src = _first_non_empty(data, "originImageUrl")
    fallback_src = _first_non_empty(data, "url")
    thumb_src = _first_non_empty(data, "thumbUrl", "url")

    candidate_ext = _first_non_empty(data, "ext")

    src_is_image = bool(src and _looks_like_image_url(src))
    fallback_src_is_image = bool(fallback_src and _looks_like_image_url(fallback_src))
    thumb_is_image = bool(thumb_src and _looks_like_image_url(thumb_src))

    if not (src_is_image or fallback_src_is_image or thumb_is_image):
        return None

    if not src_is_image:
        src = fallback_src if fallback_src_is_image else ""

    if thumb_src and not thumb_is_image:
        thumb_src = src if src_is_image else ""

    if not src:
        return None

    return {
        "src": src,
        "thumbSrc": thumb_src,
        "width": _coerce_str(data.get("width")),
        "height": _coerce_str(data.get("height")),
        "source": "messageBody",
        "btype": _coerce_str(data.get("btype")),
        "imagePath": _coerce_str(data.get("imagePath")),
        "thumbPath": _coerce_str(data.get("thumbPath")),
    }


def iter_inline_image_attachments(messages: list[dict[str, Any]]) -> Iterator[tuple[int, dict[str, Any]]]:
    for message in messages:
        if not isinstance(message, dict):
            continue

        sequence = int(message.get("sequence") or 0)
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
            yield sequence, attachment


def attachment_filename(sequence: int, url: str, content_type: str = "") -> str:
    digest = sha256(url.encode("utf-8")).hexdigest()[:12]
    extension = _ext_from_url(url) or _ext_from_content_type(content_type)

    if not extension:
        extension = ".bin"

    return f"seq{sequence:04d}_{digest}{extension}"
