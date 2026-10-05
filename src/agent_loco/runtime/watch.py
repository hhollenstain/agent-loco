from __future__ import annotations

import logging
import time
from pathlib import Path

from agent_loco.config import Settings
from agent_loco.llm.client import LLMClient, lookup_context_window
from agent_loco.runtime.improve import run_cycle

log = logging.getLogger("loco")


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
    context_window = lookup_context_window(
        settings.model_base_url,
        settings.model_api_key,
        settings.model_name,
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
