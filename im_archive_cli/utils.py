from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any


def setup_logger(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    log_path = log_dir / f"run_{stamp}.log"

    logger = logging.getLogger("im_archive_cli")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    logger.addHandler(file_handler)
    return logger


def safe_name(text: str, fallback: str = "Unknown") -> str:
    normalized = re.sub(r"[\\/:*?\"<>|]+", "_", str(text or "").strip())
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return (normalized or fallback)[:60]


def append_failure(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")



def normalize_create_time_parts(create_time: str) -> tuple[str, str]:
    """Return (yyyymmddhhmmss, yyyymmdd) with robust fallback to now."""
    raw = str(create_time or "").strip()
    dt = None

    if raw:
        if raw.isdigit() and len(raw) in (10, 13):
            try:
                ts = int(raw)
                if len(raw) == 13:
                    ts = ts / 1000.0
                dt = datetime.fromtimestamp(ts)
            except Exception:
                dt = None

        if dt is None:
            normalized = (
                raw.replace("年", "-")
                .replace("月", "-")
                .replace("日", " ")
                .replace("/", "-")
                .replace(".", "-")
                .replace("T", " ")
            )
            normalized = re.sub(r"\s+", " ", normalized).strip().rstrip("Z")
            for fmt in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%m-%d %H:%M:%S",
                "%m-%d %H:%M",
            ):
                try:
                    parsed = datetime.strptime(normalized, fmt)
                    if fmt.startswith("%m-"):
                        now = datetime.now()
                        parsed = parsed.replace(year=now.year)
                    if fmt == "%Y-%m-%d":
                        parsed = parsed.replace(hour=0, minute=0, second=0)
                    dt = parsed
                    break
                except ValueError:
                    continue

        if dt is None and re.match(r"^\d{14}$", raw):
            try:
                dt = datetime.strptime(raw, "%Y%m%d%H%M%S")
            except ValueError:
                dt = None

    if dt is None:
        dt = datetime.now()

    stamp14 = dt.strftime("%Y%m%d%H%M%S")
    date8 = stamp14[:8]
    return stamp14, date8
