"""
agents/chatbot/intent_signals.py

Deterministic Korean-keyword gates used ahead of LLM calls in the chatbot
orchestration. Split out of chatbot.py so both chatbot.py (response generation)
and graph.py (LangGraph orchestration) can import them without a circular
import between the two.
"""
from __future__ import annotations

from typing import Optional

_RECOMMEND_KEYWORDS = {
    "추천", "추천해", "추천해줘", "추천해주세요", "추천받고",
    "알려줘", "알려주세요", "보여줘", "보여주세요",
    "비슷한", "관련된", "관련 카드", "다른 카드", "다른 정책",
    "어떤 게 있", "뭐가 있", "뭐 있어", "뭐 있나",
    "찾아줘", "찾아주세요", "검색해줘",
}

_RECOMMEND_REASON_KEYWORDS = {
    "왜 추천", "왜 보여", "왜 이 카드", "왜 이걸",
    "어떤 이유로", "무슨 이유로", "추천한 이유", "추천해준 이유",
    "추천 이유", "왜 나한테", "왜 내게",
}

_ELIGIBILITY_KEYWORDS = {
    "받을수있", "해당되는", "해당돼", "해당하나요", "해당되나요", "해당될까",
    "나도가능", "나도받을", "나도해당",
    "자격되", "자격이되", "자격있",
    "조건에맞", "조건맞아", "조건충족",
    "신청할수있", "신청가능", "대상이되",
}

_PERSONAL_FACT_KEYWORDS = {
    "거주", "소득", "저소득", "차상위", "기초생활", "나이", "가구", "재학",
    "혼인", "결혼", "직업", "취업", "다문화", "한부모", "탈북",
}


def _query_signals_recommendation(query: str) -> bool:
    """Return True only if the query contains explicit recommendation-seeking language."""
    q = query.strip()
    return any(kw in q for kw in _RECOMMEND_KEYWORDS)


def _query_explicitly_requests_cards(query: str) -> bool:
    """Return True if the user is explicitly asking for policy *cards* to be
    recommended (e.g. "카드 추천", "다른 카드", "다른 카드 추천") rather than asking
    an open informational question about a topic. These should always go straight
    to card recommendation — skipping the policity_docs/DuckDuckGo gate — since the
    user isn't asking to be informed about something, they're asking for cards.
    """
    q = query.strip()
    return "카드" in q and _query_signals_recommendation(q)


def _query_signals_recommend_reason(query: str) -> bool:
    """Return True only if the query is asking WHY a card was recommended."""
    q = query.strip()
    return any(kw in q for kw in _RECOMMEND_REASON_KEYWORDS)


def _query_signals_eligibility_check(query: str) -> bool:
    """Return True if the query is asking whether the user personally qualifies/is eligible.

    Whitespace-insensitive: Korean spacing around words like "해당되는" / "해당 되는" /
    "해당이 되는" is inconsistent in casual typing, so spaces are stripped before matching.
    """
    q = "".join(query.split())
    return any(kw in q for kw in _ELIGIBILITY_KEYWORDS)


def _prior_turn_was_eligibility_question(chat_history: Optional[list]) -> bool:
    """Return True if the immediately preceding ASSISTANT turn looks like it was asking
    the user for a personal fact (age/region/income/etc.) needed for an eligibility check.

    Used to catch follow-up replies like "나 저소득층인데" or "부산 살아요" that answer such a
    question as a plain statement — these carry no explicit eligibility keywords of their own,
    so _query_signals_eligibility_check alone would miss them.
    """
    for m in reversed(chat_history or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "assistant":
            return False
        content = m.get("content") or ""
        return "?" in content and any(kw in content for kw in _PERSONAL_FACT_KEYWORDS)
    return False


def _prior_turn_was_clarifying_question(chat_history: Optional[list]) -> bool:
    """Return True if the immediately preceding ASSISTANT turn was the open-ended
    "no card context yet" clarifying question from generate_clarifying_question().

    Unlike _prior_turn_was_eligibility_question, this isn't about a specific personal
    fact — it's a generic "which topic/situation are you asking about?" question. A
    terse follow-up like "주거" is the user's answer to it, not a fresh standalone query,
    so it should be treated as recommendation-narrowing input rather than re-classified
    from scratch.
    """
    for m in reversed(chat_history or []):
        if not isinstance(m, dict):
            continue
        if m.get("role") != "assistant":
            return False
        content = m.get("content") or ""
        return content.strip().endswith("?")
    return False
