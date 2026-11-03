from __future__ import annotations

import logging
import time
from pathlib import Path

from agent_lbw.config import Settings
from agent_lbw.llm.client import LLMClient, effective_context_window
from agent_lbw.runtime.improve import run_cycle

log = logging.getLogger("lbw")


def watch(
    workspace_path: Path,
    settings: Settings,
    llm: LLMClient,
    goal: str | None = None,
    *,
    continuous: bool = False,
    cli_create_pr: bool | None = None,
) -> None:
    log.info(
        "watching %s every %ss%s",
        workspace_path,
        settings.watch_interval_seconds,
        " (keep improving)" if continuous else "",
    )
    context_window = effective_context_window(
        configured=settings.context_window,
        base_url=settings.model_base_url,
        api_key=settings.model_api_key,
        model=settings.model_name,
    )
    while True:
        result = run_cycle(
            workspace_path,
            settings,
            llm,
            goal,
            mark_checkbox=goal is None,
            cli_create_pr=cli_create_pr,
            continuous=continuous,
            context_window=context_window,
            store_root=workspace_path,
        )
        log.info(
            "cycle status=%s committed=%s published=%s merged=%s reason=%s",
            result.status,
            result.committed,
            result.published,
            result.merged,
            result.reason,
        )
        time.sleep(settings.watch_interval_seconds)
