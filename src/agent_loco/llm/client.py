from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from openai import OpenAI


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        """Return the next assistant turn."""


class OpenAICompatClient:
    """Talks to Ollama, vLLM, LM Studio, or any other OpenAI-compatible server."""

    def __init__(self, *, model: str, base_url: str, api_key: str) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.client = OpenAI(base_url=self.base_url, api_key=api_key)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
        )
        choice = response.choices[0].message
        calls: list[ToolCall] = []
        for call in choice.tool_calls or []:
            calls.append(
                ToolCall(
                    id=call.id,
                    name=call.function.name,
                    arguments=_parse_arguments(call.function.arguments),
                )
            )
        return AssistantTurn(text=choice.content, tool_calls=calls)


class ScriptedClient:
    """Deterministic stand-in used by unit tests."""

    def __init__(self, turns: Sequence[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        self.calls.append(messages)
        if not self._turns:
            return AssistantTurn(text="no more scripted turns")
        return self._turns.pop(0)


def check_model_endpoint(base_url: str, api_key: str, timeout: float = 3.0) -> tuple[bool, str]:
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        return False, f"unreachable: {exc}"
    if response.status_code >= 400:
        return False, f"HTTP {response.status_code} from {url}"
    try:
        payload = response.json()
    except ValueError:
        return True, f"reachable ({response.status_code})"
    names = model_ids_from_payload(payload)
    if names:
        shown = names[:8]
        extra = "" if len(names) <= 8 else f" (+{len(names) - 8} more)"
        return True, "models: " + ", ".join(shown) + extra
    return True, f"reachable ({response.status_code})"


def list_remote_models(base_url: str, api_key: str, timeout: float = 3.0) -> list[str]:
    """Return model ids from an OpenAI-compatible `/models` endpoint."""
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
    except httpx.HTTPError:
        return []
    if response.status_code >= 400:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    return model_ids_from_payload(payload)


def model_ids_from_payload(payload: object) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("id") or item.get("name")
        if not name:
            continue
        text = str(name)
        if text in seen:
            continue
        seen.add(text)
        names.append(text)
    return names


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    import json

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}


def iter_text_blocks(text: str | None) -> Iterator[str]:
    if text:
        yield text
