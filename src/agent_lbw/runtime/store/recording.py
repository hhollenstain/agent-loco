"""Write a task request or a schedule index when the running app already has one.

The file scheduler and the task queue stay where they are. This module records
the directory row those callers are missing.
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_lbw.runtime.account import load_account
from agent_lbw.runtime.db import Database, database
from agent_lbw.runtime.store.projects import ensure_project
from agent_lbw.runtime.store.schedules import delete_schedule_row, save_schedule
from agent_lbw.runtime.store.tasks import save_task_request, touch_task


def record_task(root: Path, database_url: str, task) -> None:
    """Store a queued request before its run starts."""
    account = load_account(root, database_url=database_url)
    workspace = Path(task.workspace)
    with database(root, database_url) as db:
        project = _project(db, account, workspace, workspace.name or "project")
        save_task_request(
            db,
            root,
            task_id=task.id,
            tenant_id=account["org"]["id"],
            project_id=project["id"],
            requester_id=account["current_user_id"],
            goal=task.goal,
            status=task.status,
            branch=task.branch,
            pr_url=task.pr_url,
            run_ref=f".lbw/runs/{task.id}",
        )


def sync_task(root: Path, database_url: str, task) -> None:
    """Update status and publish fields after a run. The goal stays sealed."""
    account = load_account(root, database_url=database_url)
    with database(root, database_url) as db:
        touch_task(
            db,
            task_id=task.id,
            tenant_id=account["org"]["id"],
            status=task.status,
            branch=task.branch,
            pr_url=task.pr_url,
            run_ref=f".lbw/runs/{task.id}",
        )


def index_change_schedule(root: Path, database_url: str, item: dict) -> None:
    """Index a file schedule. Its instruction text stays in the schedule file."""
    account = load_account(root, database_url=database_url)
    workspace = Path(str(item.get("workspace") or root))
    cadence = item.get("cadence")
    text = cadence if isinstance(cadence, str) else json.dumps(cadence, sort_keys=True)
    with database(root, database_url) as db:
        project = _project(db, account, workspace, str(item.get("name") or "schedule"))
        save_schedule(
            db,
            schedule_id=str(item["id"]),
            tenant_id=account["org"]["id"],
            project_id=project["id"],
            kind="change",
            cadence=text,
            enabled=bool(item.get("enabled", True)),
            created_by=account["current_user_id"],
        )


def drop_schedule_index(root: Path, database_url: str, schedule_id: str) -> None:
    with database(root, database_url) as db:
        delete_schedule_row(db, schedule_id)


def _project(db: Database, account: dict, workspace: Path, name: str) -> dict:
    kind = "git" if (workspace / ".git").exists() else "folder"
    return ensure_project(
        db,
        account["org"]["id"],
        name=name,
        kind=kind,
        locator=str(workspace),
    )
