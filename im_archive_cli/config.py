from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class AppConfig:
    profile_dir: str = ".im_archive/profile"
    state_file: str = ".im_archive/state.json"
    output_dir: str = ".im_archive/output"
    log_dir: str = ".im_archive/logs"
    failures_file: str = ".im_archive/failures.jsonl"
    vbooking_url: str = "https://vbooking.ctrip.com/"
    detail_base_url: str = "https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId="
    page_size: int = 100
    max_pages: int = 50
    concurrency: int = 20
    window_sec: int = 30
    output_prefix: str = "IM_Archive"
    headless: bool = True
    timezone: str = "Asia/Shanghai"
    cdp_port: int = 9222
    chrome_path: str = ""
    extension_dir: str = "."
    extension_runtime_dir: str = ".im_archive/runtime_extensions"
    chrome_state_file: str = ".im_archive/chrome_state.json"
    cdp_poll_interval_sec: float = 1.0

    @staticmethod
    def from_mapping(data: dict[str, Any]) -> "AppConfig":
        cfg = AppConfig()
        for k, v in data.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_or_create_config(path: Path) -> AppConfig:
    if path.exists():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return AppConfig.from_mapping(data)
    cfg = AppConfig()
    ensure_parent(path)
    path.write_text(yaml.safe_dump(asdict(cfg), sort_keys=False, allow_unicode=True), encoding="utf-8")
    return cfg
