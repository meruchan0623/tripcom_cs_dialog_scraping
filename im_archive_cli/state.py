from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import RunSummary, SessionRecord


def dedupe_sessions(sessions: list[SessionRecord]) -> list[SessionRecord]:
    seen: set[str] = set()
    out: list[SessionRecord] = []
    for s in sessions:
        normalized = s.normalized()
        if not normalized.session_id or normalized.session_id in seen:
            continue
        seen.add(normalized.session_id)
        out.append(normalized)
    return out


def unique_roles(sessions: list[SessionRecord]) -> list[str]:
    return sorted({s.cs_name for s in sessions if s.cs_name}, key=lambda x: x.lower())


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def default(self) -> dict[str, Any]:
        return {
            "collected_sessions": [],
            "available_roles": [],
            "selected_roles": [],
            "last_run_summary": None,
            "updated_at": datetime.utcnow().isoformat(),
        }

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            data = self.default()
            self.save(data)
            return data
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = self.default()
            self.save(data)
            return data

    def save(self, data: dict[str, Any]) -> None:
        payload = dict(data)
        payload["updated_at"] = datetime.utcnow().isoformat()
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_sessions(self) -> list[SessionRecord]:
        raw = self.load().get("collected_sessions", [])
        return [SessionRecord.from_dict(x) for x in raw]

    def set_sessions(self, sessions: list[SessionRecord], auto_select_all: bool = True) -> None:
        clean = dedupe_sessions(sessions)
        roles = unique_roles(clean)
        data = self.load()
        data["collected_sessions"] = [s.to_dict() for s in clean]
        data["available_roles"] = roles
        if auto_select_all:
            data["selected_roles"] = roles
        else:
            data["selected_roles"] = [r for r in data.get("selected_roles", []) if r in roles]
        self.save(data)

    def set_selected_roles(self, roles: list[str]) -> list[str]:
        data = self.load()
        available = set(data.get("available_roles", []))
        selected = [r for r in roles if r in available]
        data["selected_roles"] = selected
        self.save(data)
        return selected

    def filtered_sessions(self) -> list[SessionRecord]:
        data = self.load()
        selected = set(data.get("selected_roles", []))
        all_sessions = [SessionRecord.from_dict(x) for x in data.get("collected_sessions", [])]
        if not selected:
            return []
        return [s for s in all_sessions if s.cs_name in selected]

    def set_summary(self, summary: RunSummary) -> None:
        data = self.load()
        data["last_run_summary"] = asdict(summary)
        self.save(data)

