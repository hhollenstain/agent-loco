from __future__ import annotations

from agent_loco.runtime.steward import parse_proposed_goal


def test_parse_proposed_goal_reads_goal_line() -> None:
    text = (
        "GOAL: Extract inline styles into layout.css\n\n"
        "Move the button rules out of index.html."
    )
    goal = parse_proposed_goal(text)
    assert goal is not None
    assert goal.startswith("Extract inline styles into layout.css")
    assert "layout.css" in goal


def test_parse_proposed_goal_nothing_to_do() -> None:
    assert parse_proposed_goal("NOTHING_TO_DO") is None
    assert parse_proposed_goal("The tree is fine.\nNOTHING_TO_DO\n") is None
    assert parse_proposed_goal("  ") is None
