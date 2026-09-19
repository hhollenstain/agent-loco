from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent_loco.config import Settings
from agent_loco.llm.client import LLMClient, OpenAICompatClient
from agent_loco.runtime.improve import CycleResult, run_cycle

log = logging.getLogger("loco")

Runner = Callable[["Task"], CycleResult]


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_llm(settings: Settings) -> LLMClient:
    return OpenAICompatClient(
        model=settings.model_name,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
    )


@dataclass
class Task:
    id: str
    workspace: str
    goal: str | None
    auto_commit: bool
    publish: bool | None
    status: str = "queued"
    created_at: str = field(default_factory=_utcnow)
    started_at: str | None = None
    finished_at: str | None = None
    logs: list[str] = field(default_factory=list)
    summary: str | None = None
    reason: str | None = None
    tests_passed: bool | None = None
    committed: bool = False
    published: bool = False
    commit_sha: str | None = None
    error: str | None = None

    def to_dict(self, *, include_logs: bool = True) -> dict:
        payload = {
            "id": self.id,
            "workspace": self.workspace,
            "goal": self.goal,
            "auto_commit": self.auto_commit,
            "publish": self.publish,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.summary,
            "reason": self.reason,
            "tests_passed": self.tests_passed,
            "committed": self.committed,
            "published": self.published,
            "commit_sha": self.commit_sha,
            "error": self.error,
        }
        if include_logs:
            payload["logs"] = list(self.logs)
        return payload


class _TaskLogHandler(logging.Handler):
    """Capture `loco` log lines from the worker thread that owns a task."""

    def __init__(self, task: Task, thread_id: int) -> None:
        super().__init__(level=logging.INFO)
        self._task = task
        self._thread_id = thread_id
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        if threading.get_ident() != self._thread_id:
            return
        try:
            self._task.logs.append(self.format(record))
        except Exception:
            self.handleError(record)


class TaskManager:
    """Queue coding-agent cycles and run a bounded number of them at once."""

    def __init__(
        self,
        settings: Settings,
        *,
        max_concurrent: int = 1,
        runner: Runner | None = None,
        llm_factory: Callable[[Settings], LLMClient] | None = None,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        self.settings = settings
        self.max_concurrent = max_concurrent
        self._runner = runner
        self._llm_factory = llm_factory or _default_llm
        self._lock = threading.Lock()
        self._tasks: dict[str, Task] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrent,
            thread_name_prefix="loco-task",
        )

    def submit(
        self,
        workspace: Path,
        goal: str | None,
        *,
        auto_commit: bool | None = None,
        publish: bool | None = None,
    ) -> Task:
        workspace = workspace.expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"workspace is not a directory: {workspace}")
        task = Task(
            id=uuid4().hex,
            workspace=str(workspace),
            goal=(goal.strip() if goal and goal.strip() else None),
            auto_commit=self.settings.auto_commit if auto_commit is None else auto_commit,
            publish=publish,
        )
        with self._lock:
            self._tasks[task.id] = task
        self._executor.submit(self._run, task)
        return task

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda item: item.created_at, reverse=True)
        return tasks

    def counts(self) -> dict[str, int]:
        with self._lock:
            statuses = [task.status for task in self._tasks.values()]
        return {
            "queued": statuses.count("queued"),
            "running": statuses.count("running"),
            "total": len(statuses),
        }

    def shutdown(self, wait: bool = False, *, cancel_futures: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    def _run(self, task: Task) -> None:
        task.status = "running"
        task.started_at = _utcnow()
        handler = _TaskLogHandler(task, threading.get_ident())
        loco_log = logging.getLogger("loco")
        loco_log.addHandler(handler)
        try:
            result = self._execute(task)
            task.status = result.status
            task.summary = result.summary
            task.reason = result.reason
            task.tests_passed = result.tests_passed
            task.committed = result.committed
            task.published = result.published
            task.commit_sha = result.commit_sha
        except Exception as exc:
            log.exception("task %s crashed", task.id)
            task.status = "error"
            task.error = str(exc)
            task.reason = f"task crashed: {exc}"
        finally:
            loco_log.removeHandler(handler)
            task.finished_at = _utcnow()

    def _execute(self, task: Task) -> CycleResult:
        if self._runner is not None:
            return self._runner(task)
        settings = self.settings.model_copy(update={"auto_commit": task.auto_commit})
        llm = self._llm_factory(settings)
        return run_cycle(
            Path(task.workspace),
            settings,
            llm,
            task.goal,
            cli_publish=task.publish,
        )
