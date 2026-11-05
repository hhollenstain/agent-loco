from __future__ import annotations

from agent_lbw.llm.client import OpenAICompatClient


def test_a_blank_key_still_opens_a_local_client() -> None:
    llm = OpenAICompatClient(
        model="qwen-pi-fixed:latest",
        base_url="http://192.168.86.122:11434/v1",
        api_key="",
    )
    assert llm.client.api_key == "ollama"

    kept = OpenAICompatClient(
        model="qwen-pi-fixed:latest",
        base_url="http://192.168.86.122:11434/v1",
        api_key="sk-real",
    )
    assert kept.client.api_key == "sk-real"
