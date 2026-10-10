from __future__ import annotations

ATEM_RETRY_NUDGE = (
    "That tool call was invalid. Address the tool and wrap the invoke in "
    '<atem:function_calls>, then <atem:invoke name="..."> with '
    "<atem:parameter> children. Do not emit JSON or Qwen XML."
)


def uses_atem_tools(model: str) -> bool:
    """Muse Glimmer emits ATEM XML on a tool channel, not OpenAI JSON tool calls."""
    return "glimmer" in (model or "").lower()


def is_atem_parse_error(exc: BaseException) -> bool:
    """Ollama's Glimmer parser 500s when a tool channel is truncated or unwrapped."""
    text = str(exc)
    return (
        "ATEM function_calls wrapper" in text
        or "parse Glimmer call" in text
        or "XML syntax error" in text
    )
