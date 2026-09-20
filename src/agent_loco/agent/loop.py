from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from agent_loco.agent.prompts import SYSTEM_PROMPT, user_prompt
from agent_loco.llm.client import AssistantTurn, LLMClient, ToolCall
from agent_loco.llm.toolparse import parse_tool_calls
from agent_loco.progress import timed_complete
from agent_loco.tools import ToolSpec, execute_tool

log = logging.getLogger("loco")

MUTATING_TOOLS = {"write_file"}
MAX_PLAN_NUDGES = 3
CONTINUE_NUDGE = (
    "You have not changed any files yet. That reply was a plan, not a finish. "
    "Call write_file now and implement the goal. Do not summarize, and do not "
    "commit `.loco/runs/` logs."
)


@dataclass
class AgentResult:
    summary: str
    iterations: int
    tool_calls: int
    stopped_reason: str


class CodingAgent:
    def __init__(
        self,
        llm: LLMClient,
        tools: list[ToolSpec],
        *,
        max_iterations: int,
        system_prompt: str | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations
        prompt = (system_prompt or "").strip()
        self.system_prompt = prompt or SYSTEM_PROMPT.strip()

    def run(self, goal: str, context: str = "") -> AgentResult:
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": user_prompt(goal, context)},
        ]
        schemas = [tool.openai_schema() for tool in self.tools]
        known_names = {tool.name for tool in self.tools}
        tool_calls = 0
        mutated = False
        plan_nudges = 0

        for iteration in range(1, self.max_iterations + 1):
            turn = timed_complete(self.llm, messages, schemas, purpose="agent")
            calls = turn.tool_calls or parse_tool_calls(turn.text, known_names)
            if calls:
                native = bool(turn.tool_calls)
                if native:
                    messages.append(_assistant_tool_message(turn))
                else:
                    messages.append({"role": "assistant", "content": turn.text or ""})
                for call in calls:
                    tool_calls += 1
                    result = execute_tool(self.tools, call.name, call.arguments)
                    log.info("tool %s ok=%s", call.name, result.ok)
                    log.debug("%s args=%s", call.name, call.arguments)
                    if call.name in MUTATING_TOOLS and result.ok:
                        mutated = True
                    messages.append(_tool_result_message(call, result.output, native=native))
                continue

            if not mutated and plan_nudges < MAX_PLAN_NUDGES:
                plan_nudges += 1
                log.info("nudging agent to keep working after a plan-only turn")
                messages.append({"role": "assistant", "content": turn.text or ""})
                messages.append({"role": "user", "content": CONTINUE_NUDGE})
                continue

            summary = (turn.text or "").strip() or "Agent finished without a summary."
            messages.append({"role": "assistant", "content": summary})
            return AgentResult(
                summary=summary,
                iterations=iteration,
                tool_calls=tool_calls,
                stopped_reason="completed",
            )

        return AgentResult(
            summary="Stopped after reaching the iteration limit.",
            iterations=self.max_iterations,
            tool_calls=tool_calls,
            stopped_reason="max_iterations",
        )


def _assistant_tool_message(turn: AssistantTurn) -> dict:
    payload: dict = {"role": "assistant", "content": turn.text or None, "tool_calls": []}
    for call in turn.tool_calls:
        payload["tool_calls"].append(_openai_tool_call(call))
    return payload


def _tool_result_message(call: ToolCall, output: str, *, native: bool) -> dict:
    clipped = _clip(output)
    if native:
        return {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": clipped,
        }
    return {
        "role": "user",
        "content": f"Tool {call.name} returned:\n{clipped}",
    }


def _openai_tool_call(call: ToolCall) -> dict:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments),
        },
    }


def _clip(text: str, limit: int = 16_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... truncated"
