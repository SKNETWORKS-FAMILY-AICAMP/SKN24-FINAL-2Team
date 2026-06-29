"""
agents/hate_detection.py
혐오감지 에이전트 — 공유 모듈

사전 기반 1차 필터 + 벡터 유사도 2차 필터를 담당.
debate, chatbot 등 여러 에이전트에서 재사용 가능.

공개 함수:
    run_hate_detection(text, qdrant_client)
        → {"passed": bool, "violation_type": str, "matched": str, "message": str}
    init_hate_collection(qdrant_client, ...)  — 최초 1회 실행
"""
from __future__ import annotations

import logging
from typing import Dict

from openai import AsyncOpenAI

# debate 폴더의 filters, hate_vector 재사용
from agents.debate.filters import quick_filter
from agents.debate.hate_vector import vector_hate_filter, init_hate_collection

logger = logging.getLogger(__name__)
_async_openai = AsyncOpenAI()


async def llm_profanity_check(text: str) -> Dict:
    """
    LLM 기반 3차 필터 — 사전에 없는 한국어 욕설·비속어·혐오 표현 탐지.

    Returns
    -------
    {"passed": bool, "matched": str, "message": str}
    """
    try:
        response = await _async_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a Korean profanity and hate-speech detector. "
                        "Given a user message, decide if it contains ANY Korean swear words, "
                        "insults, derogatory slang, sexual harassment, or hate speech — "
                        "including slang spellings, abbreviations (e.g. ㅅㅂ, ㅂㅅ), "
                        "and creative evasions. "
                        "Reply with ONLY valid JSON, no markdown:\n"
                        '{"flagged": true/false, "matched": "<the offending word or phrase, empty string if none>"}'
                    ),
                },
                {"role": "user", "content": text},
            ],
            temperature=0,
            max_tokens=60,
        )
        import json
        parsed = json.loads(response.choices[0].message.content)
        if parsed.get("flagged"):
            matched = parsed.get("matched", "")
            return {
                "passed": False,
                "matched": matched,
                "message": (
                    f"욕설 또는 비속어('{matched}')가 포함되어 있습니다. "
                    "정중한 표현으로 다시 작성해주세요."
                ),
            }
    except Exception as exc:
        logger.warning("LLM profanity check failed (skipping): %s", exc)

    return {"passed": True, "matched": "", "message": ""}


def run_hate_detection(
    text: str,
    qdrant_client,
    openai_client=None,  # 하위 호환용 (미사용)
    vector_threshold: float = 0.70,
) -> Dict:
    """
    혐오표현 탐지 통합 실행.

    1차: 사전 필터  — 멸칭·지역비하·위협어 즉시 차단 (LLM 없음)
    2차: 벡터 필터  — ko-sroberta 임베딩 유사도 기반 변형·우회 표현 탐지

    Note: 주제이탈·맥락 혐오(LLM 검사)는 bias_check.check_user_topic()이 담당.

    Parameters
    ----------
    text              : 검사할 텍스트
    qdrant_client     : QdrantClient
    vector_threshold  : 벡터 유사도 차단 임계값 (기본 0.70)

    Returns
    -------
    {
        "passed"        : bool,
        "violation_type": str,
        "matched"       : str,
        "message"       : str,
    }
    """
    # 1차: 사전 필터
    r1 = quick_filter(text)
    if not r1["passed"]:
        return {
            "passed":         False,
            "violation_type": r1["violation_type"],
            "matched":        r1["matched"],
            "message":        r1["message"],
        }

    # 2차: 벡터 유사도
    r2 = vector_hate_filter(
        text=text,
        qdrant_client=qdrant_client,
        threshold=vector_threshold,
    )
    if not r2["passed"]:
        return {
            "passed":         False,
            "violation_type": r2["violation_type"],
            "matched":        r2.get("matched", ""),
            "message":        r2["message"],
        }

    return {"passed": True, "violation_type": "none", "matched": "", "message": ""}


__all__ = ["run_hate_detection", "llm_profanity_check", "init_hate_collection"]
