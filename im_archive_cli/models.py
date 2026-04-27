from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


DETAIL_BASE_URL = "https://imvendor.ctrip.com/queryMessages?accountsource=vbk&sessionId="


@dataclass
class SessionRecord:
    session_id: str
    cs_name: str
    create_time: str = ""
    detail_url: str = ""
    imported: bool = False

    def normalized(self) -> "SessionRecord":
        sid = str(self.session_id or "").strip()
        cs_name = str(self.cs_name or "").strip() or "Unknown"
        detail_url = str(self.detail_url or "").strip() or f"{DETAIL_BASE_URL}{sid}"
        return SessionRecord(
            session_id=sid,
            cs_name=cs_name,
            create_time=str(self.create_time or "").strip(),
            detail_url=detail_url,
            imported=bool(self.imported),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "SessionRecord":
        return SessionRecord(
            session_id=str(data.get("session_id") or data.get("sessionId") or ""),
            cs_name=str(data.get("cs_name") or data.get("csName") or "Unknown"),
            create_time=str(data.get("create_time") or data.get("createTime") or ""),
            detail_url=str(data.get("detail_url") or data.get("detailUrl") or ""),
            imported=bool(data.get("imported", False)),
        ).normalized()


@dataclass
class ImportRoleCount:
    cs_name: str
    count: int


@dataclass
class ImportPreview:
    total_sessions: int
    total_roles: int
    roles: list[ImportRoleCount] = field(default_factory=list)


@dataclass
class RunSummary:
    kind: str
    started_at: str
    finished_at: str
    total: int
    success: int
    failed: int

    @staticmethod
    def now(kind: str) -> "RunSummary":
        now = datetime.utcnow().isoformat()
        return RunSummary(kind=kind, started_at=now, finished_at=now, total=0, success=0, failed=0)

