from __future__ import annotations

from agent_loco.runtime.review import parse_review


def test_parse_review_accepts_plain_json() -> None:
    review = parse_review('{"met": false, "reason": "header is still in the template"}')
    assert review.met is False
    assert "header" in review.reason


def test_parse_review_accepts_fenced_json() -> None:
    review = parse_review("Sure.\n```json\n{\"met\": true, \"reason\": \"sidebar toggle works\"}\n```")
    assert review.met is True
    assert "toggle" in review.reason


def test_parse_review_fail_closed_without_verdict() -> None:
    review = parse_review("The goal looks done to me.")
    assert review.met is False
    assert "verdict" in review.reason


def test_parse_review_empty_is_unmet() -> None:
    review = parse_review(None)
    assert review.met is False
