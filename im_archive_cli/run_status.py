from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal


SCHEMA_VERSION = "1.0"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _date_token(value: str | None) -> str | None:
    if not value:
        return None
    return str(value).strip()[:10].replace("-", "") or None


@dataclass(frozen=True)
class RunMetadata:
    """Optional caller metadata carried from collect to export/retry."""

    start_date: str | None = None
    end_date: str | None = None

    @classmethod
    def from_mapping(cls, value: Any) -> "RunMetadata":
        data = value if isinstance(value, dict) else {}
        return cls(
            start_date=data.get("start_date") or data.get("startDate"),
            end_date=data.get("end_date") or data.get("endDate"),
        )

    def merged_with(self, fallback: "RunMetadata") -> "RunMetadata":
        return RunMetadata(self.start_date or fallback.start_date, self.end_date or fallback.end_date)

    def to_dict(self) -> dict[str, str | None]:
        return {"start_date": self.start_date, "end_date": self.end_date}


class CompletionStatusRun:
    """Write one reporting-compatible completion sidecar when a run exits."""

    def __init__(
        self,
        output_dir: str | Path,
        *,
        kind: str,
        metadata: RunMetadata | None = None,
        failures_file: str | Path | None = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.kind = kind
        self.metadata = metadata or RunMetadata()
        self.failures_file = Path(failures_file) if failures_file else None
        self.started = _utc_now()
        self.total = 0
        self.success = 0
        self.failed = 0
        self._recorded = False
        self.path: Path | None = None

    def __enter__(self) -> "CompletionStatusRun":
        return self

    def record(self, *, total: int, success: int, failed: int) -> None:
        self.total = max(0, int(total))
        self.success = max(0, int(success))
        # Include sessions abandoned by a circuit-breaker as incomplete failures.
        self.failed = max(0, int(failed), self.total - self.success)
        self._recorded = True

    def __exit__(self, exc_type, exc, _traceback) -> Literal[False]:
        if exc is not None and not self._recorded:
            self.failed = max(1, self.total - self.success)
        self.path = write_completion_status(
            self.output_dir,
            kind=self.kind,
            metadata=self.metadata,
            total=self.total,
            success=self.success,
            failed=self.failed,
            started_at=self.started,
            finished_at=_utc_now(),
            failures_file=self.failures_file,
        )
        return False


def write_completion_status(
    output_dir: str | Path,
    *,
    kind: str,
    metadata: RunMetadata,
    total: int,
    success: int,
    failed: int,
    started_at: datetime,
    finished_at: datetime,
    failures_file: Path | None = None,
) -> Path:
    total = max(0, int(total))
    success = max(0, int(success))
    failed = max(0, int(failed), total - success)
    if failed == 0:
        status = "complete"
    elif success > 0:
        status = "partial"
    else:
        status = "failed"

    start_token = _date_token(metadata.start_date) or "unknown"
    end_token = _date_token(metadata.end_date) or start_token
    run_token = started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = Path(output_dir) / f"run_summary_{start_token}_{end_token}_{run_token}.summary.json"
    failure_ref = failures_file.name if failures_file else "failures.jsonl"
    errors: list[dict[str, Any]] = []
    if failed:
        errors.append({"reference": failure_ref, "count": failed, "redacted": True})

    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "platform": "trip",
        "kind": kind,
        "status": status,
        "ok": status == "complete",
        "exitCode": 0 if status == "complete" else 1,
        "total": total,
        "success": success,
        "failed": failed,
        "startedAt": started_at.astimezone(UTC).isoformat(),
        "finishedAt": finished_at.astimezone(UTC).isoformat(),
        "errors": errors,
        "failure": {"reference": failure_ref, "count": failed, "redacted": True},
    }
    if metadata.start_date:
        payload["start_date"] = metadata.start_date
    if metadata.end_date:
        payload["end_date"] = metadata.end_date

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return path
