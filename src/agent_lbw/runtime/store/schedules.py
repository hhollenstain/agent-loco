"""Schedule index. The instruction text stays in the schedule file, not here."""

from __future__ import annotations

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.names import SCHEDULE_KINDS


def save_schedule(
    db: Database,
    *,
    schedule_id: str,
    tenant_id: str,
    project_id: str,
    kind: str,
    cadence: str,
    enabled: bool,
    created_by: str,
) -> None:
    if kind not in SCHEDULE_KINDS:
        raise ValueError("kind must be change or scan")
    text = cadence.strip()
    if not text:
        raise ValueError("cadence is required")
    db.execute(
        """
        INSERT INTO schedules (id, tenant_id, project_id, kind, cadence, enabled, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            project_id = excluded.project_id,
            kind = excluded.kind,
            cadence = excluded.cadence,
            enabled = excluded.enabled
        """,
        (schedule_id, tenant_id, project_id, kind, text, 1 if enabled else 0, created_by),
    )


def delete_schedule_row(db: Database, schedule_id: str) -> None:
    db.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
