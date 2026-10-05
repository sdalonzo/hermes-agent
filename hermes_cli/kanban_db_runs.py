"""Attempt-history data model, re-exported by the public Kanban facade."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

@dataclass
class Run:
    """One attempt at a task (``task_runs`` row): opened on claim, closed on
    complete/block/crash/timeout/reclaim; carries the handoff summary."""

    id: int
    task_id: str
    profile: Optional[str]
    step_key: Optional[str]
    status: str
    claim_lock: Optional[str]
    claim_expires: Optional[int]
    worker_pid: Optional[int]
    max_runtime_seconds: Optional[int]
    last_heartbeat_at: Optional[int]
    started_at: int
    ended_at: Optional[int]
    outcome: Optional[str]
    summary: Optional[str]
    metadata: Optional[dict]
    error: Optional[str]
    worker_session_id: Optional[str] = None
    resumed_from_run_id: Optional[int] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Run":
        from hermes_cli.kanban_db import _lossy_text, _opt_int, _json_or, _row_get
        return cls(
            **{
                col: _lossy_text(row[col]) for col in (
                    "task_id", "profile", "step_key", "status", "claim_lock", "claim_expires",
                    "worker_pid", "max_runtime_seconds", "last_heartbeat_at", "outcome", "summary", "error",
                )
            },
            id=int(row["id"]),
            started_at=int(row["started_at"]),
            ended_at=_opt_int(row["ended_at"]),
            metadata=_json_or(_lossy_text(row["metadata"])),
            worker_session_id=_row_get(row, "worker_session_id"),
            resumed_from_run_id=_opt_int(_row_get(row, "resumed_from_run_id")),
        )


