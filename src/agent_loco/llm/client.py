from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx
from openai import OpenAI

from agent_loco.llm.glimmer import ATEM_RETRY_NUDGE, is_atem_parse_error

log = logging.getLogger("loco")

_WINDOW_CACHE: dict[tuple[str, str], int | None] = {}
_TRANSIENT_ATTEMPTS = 3
_TRANSIENT_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_CONTEXT_KEYS = (
    "context_length",
    "max_model_len",
    "max_tokens",
    "context_window",
    "max_context_length",
)
_NUM_CTX_RE = re.compile(r"num_ctx\s+(\d+)", re.IGNORECASE)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AssistantTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


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
        self.base_url = normalize_model_base_url(base_url)
        self.client = OpenAI(base_url=self.base_url, api_key=api_key)

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        try:
            return self._complete_with_retry(messages, tools)
        except Exception as exc:
            if not (tools and is_atem_parse_error(exc)):
                raise
            log.warning("server rejected Glimmer tool call without ATEM wrapper; retrying")
            nudged = [*messages, {"role": "user", "content": ATEM_RETRY_NUDGE}]
            try:
                return self._complete_with_retry(nudged, tools)
            except Exception as retry_exc:
                if not is_atem_parse_error(retry_exc):
                    raise
                log.warning("Glimmer ATEM retry still rejected; completing without native tools")
                return self._complete_with_retry(nudged, [])

    def _complete_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AssistantTurn:
        for attempt in range(1, _TRANSIENT_ATTEMPTS + 1):
            try:
                return self._complete(messages, tools)
            except Exception as exc:
                if attempt >= _TRANSIENT_ATTEMPTS or not is_transient_llm_error(exc):
                    raise
                log.warning(
                    "model request failed (%s); retrying %s/%s",
                    exc,
                    attempt,
                    _TRANSIENT_ATTEMPTS - 1,
                )
                time.sleep(0.5 * attempt)
        raise RuntimeError("model request failed")

    def _complete(
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
        prompt_tokens, completion_tokens, total_tokens = completion_usage(response)
        return AssistantTurn(
            text=choice.content,
            tool_calls=calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )


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


def is_transient_llm_error(exc: BaseException) -> bool:
    """True when the model server failed in a way a second request may survive."""
    text = str(exc)
    if is_atem_parse_error(exc) and "XML syntax error" not in text:
        return False
    status = getattr(exc, "status_code", None)
    if status in _TRANSIENT_STATUS:
        return True
    lowered = text.lower()
    return "xml syntax error" in lowered or "unexpected eof" in lowered


def check_model_endpoint(base_url: str, api_key: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        url = normalize_model_base_url(base_url).rstrip("/") + "/models"
    except ValueError as exc:
        return False, str(exc)
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
    url = normalize_model_base_url(base_url).rstrip("/") + "/models"
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


def normalize_model_base_url(url: str) -> str:
    """Accept host:port or a full OpenAI-compatible `/v1` URL."""
    text = url.strip()
    if not text:
        raise ValueError("LLM server URL is required")
    if "://" not in text:
        text = "http://" + text
    parsed = text.rstrip("/")
    if parsed.endswith("/v1"):
        return parsed
    return parsed + "/v1"


def completion_usage(response: object) -> tuple[int | None, int | None, int | None]:
    """Read prompt/completion/total tokens from an OpenAI-compatible response."""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return None, None, None
    return (
        _usage_int(usage, "prompt_tokens"),
        _usage_int(usage, "completion_tokens"),
        _usage_int(usage, "total_tokens"),
    )


def lookup_context_window(
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: float = 2.0,
) -> int | None:
    """Best-effort context length for a model on an OpenAI-compatible host."""
    name = (model or "").strip()
    if not name:
        return None
    try:
        url = normalize_model_base_url(base_url)
    except ValueError:
        return None
    cache_key = (url, name)
    if cache_key in _WINDOW_CACHE:
        return _WINDOW_CACHE[cache_key]
    window = _fetch_context_window(url, api_key, name, timeout=timeout)
    _WINDOW_CACHE[cache_key] = window
    return window


def context_window_from_ollama_show(payload: object) -> int | None:
    if not isinstance(payload, dict):
        return None
    info = payload.get("model_info") or payload.get("modelinfo") or {}
    if isinstance(info, dict):
        for key, value in info.items():
            text = str(key).lower()
            if text.endswith(".context_length") or text in _CONTEXT_KEYS:
                parsed = _positive_int(value)
                if parsed is not None:
                    return parsed
    params = payload.get("parameters")
    if isinstance(params, str):
        match = _NUM_CTX_RE.search(params)
        if match:
            return _positive_int(match.group(1))
    return None


def context_window_from_models_payload(payload: object, model: str) -> int | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    wanted = (model or "").strip().lower()
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or item.get("name") or "").strip()
        if wanted and name.lower() != wanted:
            continue
        for key in _CONTEXT_KEYS:
            parsed = _positive_int(item.get(key))
            if parsed is not None:
                return parsed
        extra = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        for key in _CONTEXT_KEYS:
            parsed = _positive_int(extra.get(key))
            if parsed is not None:
                return parsed
    return None


def _fetch_context_window(
    base_url: str,
    api_key: str,
    model: str,
    *,
    timeout: float,
) -> int | None:
    headers = {"Authorization": f"Bearer {api_key}"}
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    try:
        response = httpx.post(
            f"{origin}/api/show",
            json={"name": model},
            headers=headers,
            timeout=timeout,
        )
        if response.status_code < 400:
            window = context_window_from_ollama_show(response.json())
            if window is not None:
                return window
    except (httpx.HTTPError, ValueError):
        pass
    try:
        response = httpx.get(
            f"{base_url.rstrip('/')}/models",
            headers=headers,
            timeout=timeout,
        )
        if response.status_code < 400:
            return context_window_from_models_payload(response.json(), model)
    except (httpx.HTTPError, ValueError):
        pass
    return None


def _usage_int(usage: object, key: str) -> int | None:
    if isinstance(usage, dict):
        return _positive_int(usage.get(key))
    return _positive_int(getattr(usage, key, None))


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


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


def _parse_arguments(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    if not isinstance(raw, str):
        return {"_raw": str(raw)}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw}
    return parsed if isinstance(parsed, dict) else {"_raw": raw}


def iter_text_blocks(text: str | None) -> Iterator[str]:
    if text:
        yield text
