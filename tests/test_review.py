from __future__ import annotations

from agent_loco.llm.client import AssistantTurn, ScriptedClient
from agent_loco.progress import bind_progress, current_events, reset_progress
from agent_loco.runtime.review import parse_review, review_goal, review_reason


def test_parse_review_accepts_plain_json() -> None:
    review = parse_review('{"met": false, "reason": "header is still in the template"}')
    assert review.met is False
    assert review.parsed is True
    assert "header" in review.reason


def test_parse_review_accepts_fenced_json() -> None:
    payload = '{"met": true, "reason": "sidebar toggle works"}'
    review = parse_review(f"Sure.\n```json\n{payload}\n```")
    assert review.met is True
    assert review.parsed is True
    assert "toggle" in review.reason


def test_parse_review_fail_closed_without_verdict() -> None:
    review = parse_review("The goal looks done to me.")
    assert review.met is False
    assert review.parsed is False
    assert "verdict" in review.reason
    assert "looks done" in review.raw


def test_parse_review_empty_is_unmet() -> None:
    review = parse_review(None)
    assert review.met is False
    assert review.parsed is False


def test_review_reason_includes_raw_snippet_when_unparsed() -> None:
    review = parse_review("Looks complete, discord.py is clearly newer.")
    text = review_reason(review)
    assert "verdict" in text
    assert "Looks complete" in text


def test_review_goal_retries_when_first_reply_is_prose() -> None:
    llm = ScriptedClient(
        [
            AssistantTurn(text="Looks complete to me."),
            AssistantTurn(
                text='{"met": true, "reason": "discord.py 2.3.2 -> 2.7.1 in the lockfile"}'
            ),
        ]
    )
    token = bind_progress()
    try:
        verdict = review_goal(
            llm,
            "update discord.py",
            diff="Pipfile.lock (lockfile version changes):\n- discord.py: 2.3.2 -> 2.7.1",
            summary="updated the lockfile",
            tests_passed=True,
        )
        events = current_events()
    finally:
        reset_progress(token)
    assert verdict.met is True
    assert verdict.parsed is True
    reviews = [event for event in events if event["kind"] == "review"]
    assert len(reviews) == 2
    assert reviews[0]["parsed"] is False
    assert reviews[0]["raw"] == "Looks complete to me."
    assert reviews[1]["parsed"] is True
    assert "2.7.1" in reviews[1]["raw"]


def test_review_goal_existing_tree_uses_workspace_evidence() -> None:
    llm = ScriptedClient(
        [
            AssistantTurn(
                text='{"met": true, "reason": "discord.py is already 2.7.1"}'
            ),
        ]
    )
    token = bind_progress()
    try:
        verdict = review_goal(
            llm,
            "update discord.py",
            diff="Pipfile.lock current versions:\n- discord.py: 2.7.1",
            summary="already current",
            tests_passed=True,
            existing=True,
            upstream="HEAD is on main; no upstream tracking branch (not pushed).",
        )
    finally:
        reset_progress(token)
    assert verdict.met is True
    user = llm.calls[0][-1]["content"]
    assert "no file changes" in user.lower()
    assert "Current workspace:" in user
    assert "not pushed" in user
