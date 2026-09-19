from __future__ import annotations

import logging
import time
from pathlib import Path

from agent_loco.config import Settings
from agent_loco.llm.client import LLMClient
from agent_loco.runtime.improve import run_cycle

log = logging.getLogger("loco")


def watch(
    workspace_path: Path,
    settings: Settings,
    llm: LLMClient,
    goal: str | None = None,
) -> None:
    log.info(
        "watching %s every %ss",
        workspace_path,
        settings.watch_interval_seconds,
    )
    while True:
        result = run_cycle(workspace_path, settings, llm, goal, mark_checkbox=goal is None)
        log.info(
            "cycle status=%s committed=%s published=%s reason=%s",
            result.status,
            result.committed,
            result.published,
            result.reason,
        )
        time.sleep(settings.watch_interval_seconds)
