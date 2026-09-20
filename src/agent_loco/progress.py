from __future__ import annotations

import difflib
import logging
import time
from contextvars import ContextVar, Token
from typing import Any

from agent_loco.llm.client import AssistantTurn, LLMClient
from agent_loco.logging import format_elapsed, utcnow_iso

log = logging.getLogger("loco")

MAX_DIFF_CHARS = 12_000
MAX_TEST_OUTPUT_CHARS = 16_000
ProgressToken = Token[list[dict[str, Any]] | None]

_events: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "loco_progress_events", default=None
)


def current_events() -> list[dict[str, Any]]:
    return list(_events.get() or [])


def bind_progress(events: list[dict[str, Any]] | None = None) -> ProgressToken | None:
    """Attach an event list to this task/cycle. Reuses a list already bound."""
    if _events.get() is not None and events is None:
        return None
    return _events.set([] if events is None else events)


def reset_progress(token: ProgressToken | None) -> None:
    if token is not None:
        _events.reset(token)


def record_event(kind: str, **fields: Any) -> dict[str, Any]:
    event = {"kind": kind, "at": utcnow_iso(), **fields}
    bucket = _events.get()
    if bucket is not None:
        bucket.append(event)
    return event


def unified_file_diff(path: str, before: str, after: str, *, created: bool) -> str:
    from_file = "/dev/null" if created else f"a/{path}"
    to_file = f"b/{path}"
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=from_file,
            tofile=to_file,
        )
    )
    if not diff:
        return f"--- {from_file}\n+++ {to_file}\n"
    if len(diff) > MAX_DIFF_CHARS:
        return diff[:MAX_DIFF_CHARS] + "\n... truncated"
    return diff


def record_file_change(
    path: str,
    *,
    before: str | None,
    after: str,
    created: bool,
) -> dict[str, Any]:
    action = "created" if created else "updated"
    if before is None:
        diff = f"(could not read previous contents of {path})"
    else:
        diff = unified_file_diff(path, before, after, created=created)
    event = record_event(kind="file", path=path, action=action, diff=diff)
    log.info("file %s %s", action, path)
    return event


def clip_output(text: str, limit: int = MAX_TEST_OUTPUT_CHARS) -> str:
    value = text or ""
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"... truncated {omitted} chars\n" + value[-limit:]


def record_test_run(
    *,
    command: str,
    ok: bool,
    output: str,
    phase: str = "tests",
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    event = record_event(
        kind="test",
        command=command,
        ok=ok,
        phase=phase,
        elapsed_ms=elapsed_ms,
        output=clip_output(output),
    )
    log.info("tests %s ok=%s", phase, ok)
    return event


def timed_complete(
    llm: LLMClient,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    purpose: str,
) -> AssistantTurn:
    """Run an LLM completion and record how long the server took to answer."""
    started = time.perf_counter()
    try:
        turn = llm.complete(messages, tools)
    except Exception:
        elapsed = time.perf_counter() - started
        record_event(
            kind="llm",
            purpose=purpose,
            ok=False,
            elapsed_ms=int(elapsed * 1000),
        )
        log.info("llm %s failed after %s", purpose, format_elapsed(elapsed))
        raise
    elapsed = time.perf_counter() - started
    elapsed_ms = int(round(elapsed * 1000))
    record_event(
        kind="llm",
        purpose=purpose,
        ok=True,
        elapsed_ms=elapsed_ms,
    )
    log.info("llm %s response in %s", purpose, format_elapsed(elapsed))
    return turn
