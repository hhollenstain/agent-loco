"""Task requests. The goal is ciphertext. The run directory holds the context."""

from __future__ import annotations

import secrets
import time
from pathlib import Path

from agent_lbw.runtime.db import Database
from agent_lbw.runtime.store.crypto import master_key, open_secret, seal, unwrap_key, wrap_key
from agent_lbw.runtime.store.projects import can_access

_KEY_BYTES = 32


def save_task_request(
    db: Database,
    root: Path,
    *,
    task_id: str,
    tenant_id: str,
    project_id: str,
    requester_id: str,
    goal: str | None,
    status: str,
    branch: str | None = None,
    pr_url: str | None = None,
    run_ref: str | None = None,
) -> None:
    """Insert a request. A person who cannot use the project is refused."""
    if not can_access(db, requester_id, project_id, need="use"):
        raise PermissionError("that project is closed to this person")
    ciphertext = None if goal is None else seal(tenant_dek(db, root, tenant_id), goal)
    db.execute(
        """
        INSERT INTO tasks (
            id, tenant_id, project_id, requester_id, status, branch, pr_url,
            run_ref, goal_ciphertext, created
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO NOTHING
        """,
        (
            task_id,
            tenant_id,
            project_id,
            requester_id,
            status,
            branch,
            pr_url,
            run_ref,
            ciphertext,
            int(time.time()),
        ),
    )


def touch_task(
    db: Database,
    *,
    task_id: str,
    tenant_id: str,
    status: str,
    branch: str | None,
    pr_url: str | None,
    run_ref: str | None,
) -> None:
    """Update status and publish fields. The goal ciphertext stays as first written."""
    db.execute(
        """
        UPDATE tasks
        SET status = ?, branch = ?, pr_url = ?, run_ref = ?
        WHERE id = ? AND tenant_id = ?
        """,
        (status, branch, pr_url, run_ref, task_id, tenant_id),
    )


def read_task(db: Database, root: Path, *, task_id: str, identity_id: str) -> dict:
    row = db.fetchone(
        """
        SELECT id, tenant_id, project_id, requester_id, status, branch, pr_url,
               run_ref, goal_ciphertext, created
        FROM tasks WHERE id = ?
        """,
        (task_id,),
    )
    if row is None:
        raise KeyError(task_id)
    if not can_access(db, identity_id, row["project_id"], need="use"):
        raise PermissionError("that project is closed to this person")
    goal = None
    if row["goal_ciphertext"]:
        goal = open_secret(tenant_dek(db, root, row["tenant_id"]), row["goal_ciphertext"])
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "requester_id": row["requester_id"],
        "status": row["status"],
        "branch": row["branch"],
        "pr_url": row["pr_url"],
        "run_ref": row["run_ref"],
        "goal": goal,
        "created": row["created"],
    }


def tenant_dek(db: Database, root: Path, tenant_id: str) -> bytes:
    row = db.fetchone("SELECT wrapped_key FROM tenant_keys WHERE tenant_id = ?", (tenant_id,))
    master = master_key(root)
    if row is None:
        dek = secrets.token_bytes(_KEY_BYTES)
        db.execute(
            "INSERT INTO tenant_keys (tenant_id, wrapped_key) VALUES (?, ?)",
            (tenant_id, wrap_key(master, dek)),
        )
        return dek
    return unwrap_key(master, row["wrapped_key"])
