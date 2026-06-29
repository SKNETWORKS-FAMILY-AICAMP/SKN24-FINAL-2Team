"""
utils.py
Policity AI Agent 공통 유틸리티

사용처:
  - llm(): agents/card_generation/news/nodes.py
           agents/card_generation/policy/nodes.py
           agents/chatbot/chatbot.py
           agents/debate/debate.py
"""
from typing import Dict, List

from openai import OpenAI

from config import LLM_MODEL, LLM_MODEL_FAST  # noqa: F401


def llm(
    messages: List[Dict],
    client: OpenAI,
    model: str = LLM_MODEL,
    max_tokens: int = 1000,
    json_mode: bool = False,
) -> str:
    """OpenAI Chat Completion 래퍼"""
    kwargs: Dict = dict(
        model=model,
        messages=messages,
        max_completion_tokens=max_tokens,
    )
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content