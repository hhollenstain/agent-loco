from __future__ import annotations

from agent_lbw.tools.base import ToolResult, ToolSpec, object_schema


def ask_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="ask_user",
            description=(
                "Ask the user one clarifying question, or for something you need "
                "before the change can continue. Call this when you are stuck: "
                "a missing choice, access, or fact. Do not guess, and do not "
                "keep editing while you wait."
            ),
            parameters=object_schema(
                {
                    "question": {
                        "type": "string",
                        "description": "One specific question the user can answer.",
                    }
                },
                ["question"],
            ),
            handler=_ask,
        )
    ]


def _ask(question: str) -> ToolResult:
    text = " ".join((question or "").split())
    if not text:
        return ToolResult(False, "question is required")
    return ToolResult(True, text[:2000])
