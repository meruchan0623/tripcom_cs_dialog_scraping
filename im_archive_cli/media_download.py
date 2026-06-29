from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import requests

from .config import AppConfig
from .media import attachment_filename, iter_inline_image_attachments


def _safe_content_type(response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "")
    if ";" in content_type:
        content_type = content_type.split(";", 1)[0]
    return content_type.strip().lower()


def _set_failed(attachment: dict[str, Any], message: str, log: Callable[[str], None] | None) -> None:
    attachment["downloadStatus"] = "failed"
    attachment["downloadError"] = message
    attachment.pop("localPath", None)
    attachment.pop("relativePath", None)
    if log is not None:
        log(f"图片下载失败: {message}")


def _download_one(
    attachment: dict[str, Any],
    sequence: int,
    assets_dir: Path,
    base_name: str,
    cfg: AppConfig,
    log: Callable[[str], None] | None,
) -> None:
    src = attachment.get("src")
    if not isinstance(src, str) or not src:
        _set_failed(attachment, "missing or invalid image src", log)
        return

    max_bytes = int(cfg.image_max_bytes)
    timeout = int(cfg.image_timeout_sec)
    part_path = assets_dir / f"{attachment_filename(sequence, src)}.part"
    final_path = assets_dir / part_path.name.removesuffix(".part")

    try:
        with requests.get(src, stream=True, timeout=timeout) as response:
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}")

            content_type = _safe_content_type(response)
            if not content_type.startswith("image/"):
                raise RuntimeError(f"unsupported content-type: {content_type}")

            with part_path.open("wb") as fp:
                downloaded = 0
                for chunk in response.raw.stream(8192, decode_content=True):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > max_bytes:
                        raise RuntimeError(f"image exceeds max bytes: {max_bytes}")
                    fp.write(chunk)

        part_path.replace(final_path)
        attachment["localPath"] = str(final_path.resolve())
        attachment["relativePath"] = f"{base_name}_assets/{final_path.name}"
        attachment["downloadStatus"] = "downloaded"
        attachment.pop("downloadError", None)
    except Exception as exc:  # noqa: BLE001
        if part_path.exists():
            part_path.unlink(missing_ok=True)
        _set_failed(attachment, str(exc), log)


def download_conversation_images(
    conversation: dict[str, Any],
    conversation_dir: str | Path,
    base_name: str,
    config: AppConfig,
    log: Callable[[str], None] | None,
) -> None:
    if not config.download_images:
        return

    messages = conversation.get("messages")
    if not isinstance(messages, list):
        return

    attachments = list(iter_inline_image_attachments(messages))
    if not attachments:
        return

    assets_dir = Path(conversation_dir) / f"{base_name}_assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    max_workers = max(1, int(config.image_max_workers))
    request_interval = max(0.0, float(config.image_request_interval_sec))

    for batch_start in range(0, len(attachments), max_workers):
        if batch_start > 0:
            time.sleep(request_interval)

        batch = attachments[batch_start : batch_start + max_workers]
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_download_one, attachment, sequence, assets_dir, base_name, config, log): attachment
                for sequence, attachment in batch
            }
            for future in as_completed(futures):
                future.result()
