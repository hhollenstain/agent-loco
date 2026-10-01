from __future__ import annotations

import re

from agent_loco.llm.client import LLMClient
from agent_loco.progress import timed_complete
from agent_loco.runtime.project import ProjectConfig, collect_context
from agent_loco.sandbox import Workspace

_NOTHING = re.compile(r"\bNOTHING_TO_DO\b", re.IGNORECASE)
_GOAL_LINE = re.compile(r"^GOAL:\s*(.+)$", re.IGNORECASE | re.MULTILINE)

PROPOSE_SYSTEM = """You review a codebase and pick the next small improvement.

Choose ONE concrete change that improves best practices or user experience.
It must be finishable in a single agent cycle. Prefer a real gap in the tree
over a rewrite.

Reply with:
GOAL: <one-line imperative>
then a short spec (files to touch and why).

If the repo is already in good shape, reply with NOTHING_TO_DO and nothing else.
"""


def parse_proposed_goal(text: str | None) -> str | None:
    """Turn a steward proposal into a cycle goal, or None when there is no work."""
    raw = (text or "").strip()
    if not raw or _NOTHING.search(raw):
        return None
    match = _GOAL_LINE.search(raw)
    if match:
        headline = match.group(1).strip()
        rest = raw[match.end() :].strip()
        if rest:
            return f"{headline}\n\n{rest}"
        return headline or None
    return raw[:4000]


def propose_improvement_goal(
    llm: LLMClient,
    workspace: Workspace,
    project: ProjectConfig,
    *,
    allow_publish: bool,
) -> str | None:
    """Ask the model for one best-practice or UX improvement from the current tree."""
    context = collect_context(workspace.root, project, allow_publish=allow_publish)
    turn = timed_complete(
        llm,
        [
            {"role": "system", "content": PROPOSE_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"{context}\n\n"
                    "Pick the next improvement. One change only."
                ),
            },
        ],
        [],
        purpose="propose",
    )
    return parse_proposed_goal(turn.text)
