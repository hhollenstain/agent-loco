from __future__ import annotations

import logging

from agent_loco.llm.client import AssistantTurn, ScriptedClient
from agent_loco.logging import UtcFormatter, format_elapsed, utcnow_iso
from agent_loco.progress import (
    bind_progress,
    clip_output,
    current_events,
    record_test_run,
    reset_progress,
    timed_complete,
)


def test_utc_formatter_uses_iso_timestamps() -> None:
    record = logging.LogRecord(
        name="loco",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello",
        args=(),
        exc_info=None,
    )
    formatted = UtcFormatter("%(asctime)s %(message)s").format(record)
    stamp, message = formatted.split(" ", 1)
    assert message == "hello"
    assert stamp.endswith("Z")


def test_format_elapsed_and_utcnow() -> None:
    assert format_elapsed(0.012).endswith("ms")
    assert format_elapsed(1.234).endswith("s")
    assert utcnow_iso().endswith("Z")


def test_timed_complete_records_latency() -> None:
    token = bind_progress()
    try:
        turn = timed_complete(
            ScriptedClient([AssistantTurn(text="ok")]),
            [{"role": "user", "content": "hi"}],
            [],
            purpose="agent",
        )
        events = current_events()
    finally:
        reset_progress(token)
    assert turn.text == "ok"
    assert events[0]["kind"] == "llm"
    assert events[0]["purpose"] == "agent"
    assert events[0]["ok"] is True
    assert events[0]["elapsed_ms"] >= 0


def test_record_test_run_keeps_failure_tail() -> None:
    token = bind_progress()
    try:
        record_test_run(
            command="python3 check.py",
            ok=False,
            output="AssertionError: expected 5",
            phase="after",
            elapsed_ms=42,
        )
        events = current_events()
    finally:
        reset_progress(token)
    failed = events[0]
    assert failed["kind"] == "test"
    assert failed["ok"] is False
    assert failed["phase"] == "after"
    assert failed["command"] == "python3 check.py"
    assert "AssertionError" in failed["output"]
    clipped = clip_output("x" * 50 + "TAIL", limit=8)
    assert clipped.endswith("TAIL")
    assert "truncated" in clipped
