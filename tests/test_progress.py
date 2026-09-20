from __future__ import annotations

import logging
from agent_loco.logging import UtcFormatter, format_elapsed, utcnow_iso
from agent_loco.progress import bind_progress, current_events, reset_progress, timed_complete
from agent_loco.llm.client import AssistantTurn, ScriptedClient


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
