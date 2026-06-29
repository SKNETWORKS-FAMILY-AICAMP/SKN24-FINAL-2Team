"""

"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, Dict, Optional

from openai import OpenAI

from agents.bias_check import BiasClassifier, check_ai_bias, check_content_bias
from agents.hate_detection import run_hate_detection, llm_profanity_check

from .graph import build_chatbot_graph
from .intent_signals import (
    _prior_turn_was_eligibility_question,
    _query_signals_eligibility_check,
)
from .llm import (
    check_eligibility_info,
    check_message_card_consistency,
    generate_answer_tool,
    generate_answer_tool_stream,
    generate_clarifying_question,
    generate_missing_info_question,
    generate_recommend_message,
    generate_recommend_reason,
    generate_web_search_answer,
)
from .qdrant import (
    build_recommend_reason_context,
    chat_card_recommendations,
    new_chat_card_recommendations,
    # recommend_cards_tool,
    search_policy_docs,
)

logger = logging.getLogger(__name__)

OPENAI_MODEL     = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_BIAS_RETRIES = 2
RECENT_TURNS_KEEP = int(os.getenv("RECENT_TURNS_KEEP") or 4)

_sync_openai_client = OpenAI()
classifier = BiasClassifier()
_chat_graph = build_chatbot_graph()


async def _generate_checked_web_search_answer(
    user_query: str,
    search_results: list,
    user_profile_text: str,
    chat_history: Optional[list],
) -> str:
    """Generate the DDG-grounded answer and run it through check_content_bias before
    returning it, retrying up to MAX_BIAS_RETRIES times — DuckDuckGo content is
    uncurated, unlike card/policity_docs content, so unlike the other answer
    paths in this file it isn't safe to stream straight to the user unchecked.

    Uses check_content_bias (the KR-ELECTRA classifier already used for card/news
    content) rather than check_ai_bias — check_ai_bias judges debate-speech
    neutrality against a political constitution and expects political content to
    evaluate; for an apolitical DDG answer (e.g. "다른 카드 추천" pulling up generic
    credit-card results) it fails with "정치적 내용이 없음" every time, exhausting
    retries on content that was never actually biased.
    """
    # Same concurrent-candidates pattern as _handle_card_inquiry's bias loop: generate
    # all candidates and run their bias checks concurrently rather than retrying
    # sequentially, so the worst case costs one round-trip instead of up to
    # MAX_BIAS_RETRIES + 1 in series.
    async def _generate_and_check(attempt_no: int):
        generation = await generate_web_search_answer(
            user_query=user_query,
            search_results=search_results,
            user_profile_text=user_profile_text,
            chat_history=chat_history,
        )
        candidate = generation["answer"]
        bias_result = await asyncio.to_thread(check_content_bias, candidate, classifier)
        return candidate, bias_result

    attempts = await asyncio.gather(*[_generate_and_check(i) for i in range(MAX_BIAS_RETRIES + 1)])

    answer = None
    for attempt_no, (candidate, bias_result) in enumerate(attempts):
        logger.info(
            "[web_search] content bias check (candidate %d/%d): passed=%s label=%s confidence=%s",
            attempt_no + 1, len(attempts), bias_result.get("passed"),
            bias_result.get("label"), bias_result.get("confidence"),
        )
        if bias_result.get("passed", True):
            answer = candidate
            break
    if answer is None:
        answer = "죄송합니다. 현재 해당 질문에 대한 답변을 제공하기 어렵습니다. 다른 방법으로 문의해 주세요."
        logger.error("All %d concurrent DDG-answer candidates were biased. Returning fallback.", len(attempts))
    return answer

# Re-export symbols the API layer imports directly.
__all__ = [
    "ChatBlockedError",
    # "handle_chat_request",
    "handle_chat_request_stream",
    "new_chat_card_recommendations",
    "generate_recommend_message",
]



class ChatBlockedError(Exception):
    """Raised when a request is blocked (profanity / bias). Carries the HTTP detail dict."""
    def __init__(self, status_code: int, detail: Dict[str, Any]):
        self.status_code = status_code
        self.detail      = detail
        super().__init__(str(detail))


async def _handle_daily_life(reply: str, **_) -> Dict[str, Any]:
    logger.info("[6/6][daily_life] Using classifier reply directly.")
    return {"routing": "daily_life", "answer": reply}


async def _handle_card_inquiry(
    user_query: str,
    card_context: str,
    card_title: str,
    category_id: Optional[int],
    user_profile_text: str,
    qdrant_client,
    chat_history: Optional[list] = None,
    **_,
) -> Dict[str, Any]:
    if not card_context:
        logger.info("[6/6][card_inquiry] No card context — generating clarifying question via LLM.")
        clarifying_q = await generate_clarifying_question(
            user_query=user_query,
            user_profile_text=user_profile_text,
            chat_history=chat_history,
        )
        logger.info("[6/6][card_inquiry] Clarifying question: '%s'", clarifying_q)
        return {"routing": "card_inquiry", "answer": clarifying_q}

    total_tokens = 0

    # ── Personal eligibility check ────────────────────────────────────────────
    # If the user is asking "나는 받을 수 있어?"-style questions, first verify we
    # actually have the personal facts needed to give a specific answer, rather
    # than letting the LLM fall back to a vague restatement of the card.
    if _query_signals_eligibility_check(user_query):
        logger.info("[6/6][card_inquiry] Eligibility-style question detected — checking known user info...")
        eligibility = await check_eligibility_info(
            user_query=user_query,
            card_context=card_context,
            user_profile_text=user_profile_text,
            chat_history=chat_history,
        )
        logger.info("[6/6][card_inquiry] Eligibility check: status=%s missing_fields=%s",
                    eligibility["status"], eligibility.get("missing_fields"))
        if eligibility["status"] == "missing_info":
            missing_q = await generate_missing_info_question(
                card_context=card_context,
                missing_fields=eligibility.get("missing_fields", []),
                user_query=user_query,
                chat_history=chat_history,
            )
            logger.info("[6/6][card_inquiry] Missing-info question: '%s'", missing_q)
            return {"routing": "card_inquiry", "answer": missing_q, "prompt_tokens": total_tokens}
        is_personal_query = True
    else:
        is_personal_query = False

    logger.info("[6/6][card_inquiry] Searching policy docs for extra context...")
    extra_context = await search_policy_docs(
        user_query,
        card_title=card_title,
        category_id=category_id,
        qdrant_client=qdrant_client,
    )
    logger.info("[6/6][card_inquiry] extra_context found: %s chars", len(extra_context) if extra_context else 0)

    # Generate (MAX_BIAS_RETRIES + 1) candidate answers concurrently and bias-check
    # them concurrently too, instead of the old sequential generate->check->retry
    # chain. Trades extra LLM calls (cost) for lower latency: the worst case used to
    # be N sequential round-trips, now it's one round-trip regardless of how many
    # candidates fail the check.
    async def _generate_and_check(attempt_no: int):
        logger.info("[6/6][card_inquiry] Generating answer candidate %d/%d...", attempt_no + 1, MAX_BIAS_RETRIES + 1)
        generation = await generate_answer_tool(
            user_query=user_query,
            context=card_context,
            extra_context=extra_context or None,
            chat_history=chat_history,
            user_profile_text=user_profile_text,
            is_personal_query=is_personal_query,
        )
        candidate = generation["answer"]
        bias_result = await asyncio.to_thread(check_ai_bias, candidate, _sync_openai_client)
        return generation, candidate, bias_result

    attempts = await asyncio.gather(*[_generate_and_check(i) for i in range(MAX_BIAS_RETRIES + 1)])

    answer = None
    for attempt_no, (generation, candidate, bias_result) in enumerate(attempts):
        total_tokens += generation["prompt_tokens"]
        logger.info(
            "[6/6][card_inquiry] candidate %d/%d is_biased=%s | reason=%s | preview='%s'",
            attempt_no + 1, len(attempts), bias_result.get("is_biased"),
            bias_result.get("reason"), (candidate or "")[:150],
        )
        if not bias_result.get("is_biased", False):
            answer = candidate
            break
    if answer is None:
        answer = "죄송합니다. 현재 해당 질문에 대한 답변을 제공하기 어렵습니다. 다른 방법으로 문의해 주세요."
        logger.error("All %d concurrent candidates were biased. Returning fallback.", len(attempts))

    return {
        "routing":       "card_inquiry",
        "answer":        answer,
        "extra_context": extra_context or None,
        "prompt_tokens": total_tokens,
    }


async def _handle_recommend(
    intent: str,
    reply: str,
    user_profile_text: str,
    seen_card_ids: Optional[list],
    qdrant_client,
    chat_summary: Optional[str] = None,
    **_,
) -> Dict[str, Any]:
    if intent == "recommend_based_on_user_query":
        # classifier sets reply to "현재 관심: {extracted keywords}"
        embed_text = reply.removeprefix("현재 관심:").strip() or user_profile_text
        log_tag = "recommend_query"
    else:
        embed_text = user_profile_text
        log_tag = "recommend_profile"

    logger.info("[6/6][%s] Fetching recommendations | embed_text='%s'", log_tag, embed_text[:100])
    rec_result = chat_card_recommendations(
        user_profile_text=embed_text,
        seen_card_ids=seen_card_ids or [],  # #4: exclude already-seen cards
        top_k=3,
        qdrant_client=qdrant_client,
    )

    recommendations = rec_result["recommendations"]
    recommended_ids = [r["card_id"] for r in recommendations]
    hits_titles_combined = ", ".join(r["title"] for r in recommendations if r.get("title"))
    # #7: pass content snippets so the LLM can write a more specific intro message
    card_summaries = [
        {"title": r.get("title", ""), "content": r.get("content", "")}
        for r in recommendations
    ]
    logger.info("[6/6][%s] Found %d cards: titles='%s'", log_tag, len(recommendations), hits_titles_combined[:150])

    rec_message = await generate_recommend_message(user_profile_text, hits_titles_combined, card_summaries, chat_summary)
    logger.info("[6/6][%s] rec_message preview='%s'", log_tag, (rec_message or "")[:100])

    # Critique-and-revise: the consistency check used to only log a warning on
    # mismatch and ship the inconsistent message anyway. Now the critic's verdict
    # actually feeds back into one regeneration attempt before the message goes out.
    # This adds a real extra round-trip, but only on the (uncommon) mismatch path.
    consistent = await check_message_card_consistency(rec_message, card_summaries)
    if not consistent:
        logger.warning(
            "[6/6][%s] MISMATCH — critiquing and regenerating | message='%s' | cards='%s'",
            log_tag, rec_message, hits_titles_combined,
        )
        revision_note = (
            f"이전 시도: '{rec_message}' — 이 문장은 추천된 카드 내용과 맞지 않는다는 평가를 받았습니다. "
            "카드 내용에 실제로 부합하는 문장으로 다시 작성하세요."
        )
        revised_message = await generate_recommend_message(
            user_profile_text, hits_titles_combined, card_summaries, chat_summary,
            revision_note=revision_note,
        )
        revised_consistent = await check_message_card_consistency(revised_message, card_summaries)
        logger.info(
            "[6/6][%s] revision consistent=%s | revised='%s'",
            log_tag, revised_consistent, (revised_message or "")[:100],
        )
        rec_message = revised_message

    return {
        "routing":         "recommend",
        "recommendations": recommended_ids,
        "message":         rec_message,
    }


async def _handle_recommend_reason(
    user_profile_text: str,
    card_context: str,
    total_tokens: int,
    **_,
) -> Dict[str, Any]:
    logger.info("[6/6][recommend_reason] Generating recommendation rationale...")
    generation = await generate_recommend_reason(
        user_profile_text=user_profile_text,
        card_context=card_context,
    )
    total_tokens += generation["prompt_tokens"]
    logger.info("[6/6][recommend_reason] Answer preview='%s'", (generation["answer"] or "")[:100])
    return {
        "routing":       "recommend_reason",
        "answer":        generation["answer"],
        "prompt_tokens": total_tokens,
    }


_INTENT_HANDLERS = {
    "daily_life":                      _handle_daily_life,
    "card_inquiry":                    _handle_card_inquiry,
    "recommend_based_on_user_profile": _handle_recommend,
    "recommend_based_on_user_query":   _handle_recommend,
    "recommend_reason":                _handle_recommend_reason,
}



async def handle_chat_request_stream(payload: Dict[str, Any], qdrant_client=None):
    """
    Async generator for streaming responses.

    Yields SSE-formatted strings:
      - data: {"type": "meta", ...}   — metadata before text starts
      - data: {"type": "chunk", "text": "..."}  — text tokens (card_inquiry only)
      - data: {"type": "done", ...}   — final metadata (summary, tokens, etc.)
    """
    import json as _json

    user_id    = payload["user_id"]
    user_query = payload["user_query"]
    card_id    = payload.get("card_id")
    if card_id is not None:
        card_id = str(card_id)

    logger.info("[stream][1] start user_id=%s card_id=%s query='%s'", user_id, card_id, user_query[:100])

    def _sse(obj: dict) -> str:
        return f"data: {_json.dumps(obj, ensure_ascii=False)}\n\n"

    # ── Hate / profanity gate ─────────────────────────────────────────────────
    # Both checks read only user_query and don't depend on each other's result,
    # so they're run concurrently — but the precedence for which error gets
    # raised when both fail stays the same as the old sequential order
    # (hate_result checked first, then llm_profanity_result).
    logger.info("[stream][2] running hate/profanity gate")
    hate_result, llm_profanity_result = await asyncio.gather(
        asyncio.to_thread(run_hate_detection, user_query, qdrant_client=qdrant_client, openai_client=_sync_openai_client),
        llm_profanity_check(user_query),
    )
    if not hate_result["passed"]:
        logger.warning("[stream][2] hate detection blocked: layer=%s matched='%s'", hate_result["violation_type"], hate_result.get("matched"))
        raise ChatBlockedError(
            status_code=400,
            detail={
                "error":      "profanity_detected",
                "status":     "PROFANE",
                "reason":     hate_result["message"],
                "censored":   user_query.replace(hate_result["matched"], "*" * len(hate_result["matched"])) if hate_result["matched"] else user_query,
                "layer":      hate_result["violation_type"],
                "categories": {hate_result["violation_type"]: True},
            },
        )

    if not llm_profanity_result["passed"]:
        matched = llm_profanity_result.get("matched", "")
        logger.warning("[stream][2] llm profanity check blocked: matched='%s'", matched)
        raise ChatBlockedError(
            status_code=400,
            detail={
                "error":      "profanity_detected",
                "status":     "PROFANE",
                "reason":     llm_profanity_result["message"],
                "censored":   user_query.replace(matched, "*" * len(matched)) if matched else user_query,
                "layer":      "llm_profanity",
                "categories": {"llm_profanity": True},
            },
        )
    logger.info("[stream][2] profanity gate passed")

    chat_session_id = payload.get("chat_session_id") or str(uuid.uuid4())
    chat_history    = payload.get("chat_history") or []
    chat_summary    = payload.get("chat_summary") or ""
    recent_msg_ids  = payload.get("recent_msg_ids") or []
    card_data       = payload.get("card_data")
    logger.info("===CARD_DATA=======%s", card_data)

    # ── Per-turn orchestration (LangGraph) ────────────────────────────────────
    # Card-context prep, history compression, intent classification, and all
    # the routing overrides (explicit card request, daily_life escalation,
    # clarifying-question escalation, policity_docs/DDG source judging) are
    # delegated to the prep -> classify StateGraph in graph.py. It owns "what
    # should we do this turn"; the streaming/answer-generation code below
    # (including the bias-check generate-N-candidates pattern) stays plain
    # asyncio since neither maps cleanly onto LangGraph's per-node-output model.
    graph_state = {
        "user_query":                 user_query,
        "card_id":                    card_id,
        "card_data":                  card_data,
        "chat_history":               chat_history,
        "user_profile_text":          payload.get("user_profile_text") or "",
        "seen_card_ids":              payload.get("seen_card_ids") or [],
        "request_summary":           bool(payload.get("request_summary")),
        "daily_life_count":          payload.get("daily_life_count", 0),
        "clarifying_question_count": payload.get("clarifying_question_count", 0),
    }
    graph_config = {
        "configurable": {
            "thread_id":       chat_session_id,
            "qdrant_client":   qdrant_client,
            "chat_session_id": payload.get("chat_session_id"),
        }
    }
    logger.info("[stream][3-5] running orchestration graph chat_session_id=%s", chat_session_id)
    final_state = await _chat_graph.ainvoke(graph_state, config=graph_config)

    eligibility_card_context = final_state.get("eligibility_card_context", "")
    full_card_context        = final_state.get("full_card_context", "")
    card_title                = final_state.get("card_title", "")
    category_id               = final_state.get("category_id")
    new_summary                = final_state.get("new_summary", "")

    intent                     = final_state["intent"]
    reply                      = final_state["reply"]
    reason                     = final_state["reason"]
    total_tokens               = final_state["total_tokens"]
    daily_life_count           = final_state["daily_life_count"]
    clarifying_question_count  = final_state["clarifying_question_count"]
    card_inquiry_routing       = final_state.get("card_inquiry_routing")
    policy_doc_context         = final_state.get("policy_doc_context", "")
    web_search_results         = final_state.get("web_search_results", [])
    recommend_web_fallback     = final_state.get("recommend_web_fallback", False)

    # ── Send metadata before text starts ─────────────────────────────────────
    yield _sse({
        "type":            "meta",
        "routing":         card_inquiry_routing or ("web_search" if recommend_web_fallback else intent),
        "intent_reason":   reason,
        "chat_session_id": chat_session_id,
        "card_id":         card_id,
    })

    # ── Stream or resolve answer ──────────────────────────────────────────────
    logger.info("[stream][6] handling intent='%s'", intent)
    if intent == "card_inquiry":
        if not full_card_context:
            if card_inquiry_routing == "policity_docs":
                logger.info("[stream][6] card_inquiry — answering from policity_docs (no card selected)")
                answer_chunks: list[str] = []
                async for chunk in generate_answer_tool_stream(
                    user_query=user_query,
                    context="",
                    extra_context=policy_doc_context,
                    chat_history=chat_history,
                    user_profile_text=payload.get("user_profile_text") or "",
                    is_personal_query=False,
                ):
                    answer_chunks.append(chunk)
                    yield _sse({"type": "chunk", "text": chunk})
                answer = "".join(answer_chunks)
                logger.info("[stream][6] policity_docs answer complete len=%d", len(answer))

            elif card_inquiry_routing == "web_search":
                logger.info("[stream][6] card_inquiry — answering from DuckDuckGo search (no policity_docs match)")
                disclaimer = "해당 메시지나 질문에 대한 내부 데이터가 없습니다."
                web_answer = await _generate_checked_web_search_answer(
                    user_query=user_query,
                    search_results=web_search_results,
                    user_profile_text=payload.get("user_profile_text") or "",
                    chat_history=chat_history,
                )
                yield _sse({"type": "chunk", "text": disclaimer + "\n\n"})
                yield _sse({"type": "web_search", "sources": web_search_results})
                yield _sse({"type": "chunk", "text": web_answer})
                answer = disclaimer + "\n\n" + web_answer
                logger.info("[stream][6] web_search answer complete len=%d", len(answer))

            else:
                logger.info("[stream][6] card_inquiry — no card/doc/web match, generating clarifying question")
                answer = await generate_clarifying_question(
                    user_query=user_query,
                    user_profile_text=payload.get("user_profile_text") or "",
                    chat_history=chat_history,
                )
                yield _sse({"type": "chunk", "text": answer})
        else: #card_context.type == 'policy' || 'news'
            if card_data.get('type') == 'policy':
                user_profile_text = payload.get("user_profile_text") or ""
                is_personal_query = (
                    _query_signals_eligibility_check(user_query)
                    or _prior_turn_was_eligibility_question(chat_history)
                )
                missing_info_answer = None
                if is_personal_query:
                    logger.info("[stream][6] eligibility-style question detected — checking known info...")
                    eligibility = await check_eligibility_info(
                        user_query=user_query,
                        card_context=eligibility_card_context,
                        user_profile_text=user_profile_text,
                        chat_history=chat_history,
                    )
                    logger.info("[stream][6] eligibility check: status=%s missing_fields=%s",
                                eligibility["status"], eligibility.get("missing_fields"))
                    if eligibility["status"] == "missing_info":
                        missing_info_answer = await generate_missing_info_question(
                            card_context=full_card_context,
                            missing_fields=eligibility.get("missing_fields", []),
                            user_query=eligibility_card_context,
                            chat_history=chat_history,
                        )

                if missing_info_answer is not None:
                    logger.info("[stream][6] card_inquiry — missing personal info, asking clarifying question")
                    answer = missing_info_answer
                    yield _sse({"type": "chunk", "text": answer})
                else:
                    # maybe? search policity_docs by card_id metadata 
                    logger.info("[stream][6] card_inquiry — searching policy docs for extra context")
                    extra_context = await search_policy_docs(
                        user_query,
                        card_title=card_title,
                        category_id=category_id,
                        qdrant_client=qdrant_client,
                    )
                    logger.info("[stream][6] streaming answer chunks | is_personal_query=%s", is_personal_query)
                    answer_chunks: list[str] = []
                    async for chunk in generate_answer_tool_stream(
                        user_query=user_query,
                        context=full_card_context,
                        extra_context=extra_context or None,
                        chat_history=chat_history,
                        user_profile_text=user_profile_text,
                        is_personal_query=is_personal_query,
                    ):
                        answer_chunks.append(chunk)
                        yield _sse({"type": "chunk", "text": chunk})
                    answer = "".join(answer_chunks)
                    logger.info("[stream][6] card_inquiry answer complete len=%d", len(answer))

    elif intent == "daily_life":
        logger.info("[stream][6] daily_life — returning classify reply directly")
        answer = reply
        yield _sse({"type": "chunk", "text": answer})

    elif intent in ("recommend_based_on_user_profile", "recommend_based_on_user_query"):
        if recommend_web_fallback:
            logger.info("[stream][6] recommend — no policity_docs match, answering from DuckDuckGo search")
            disclaimer = "해당 메시지나 질문에 대한 내부 데이터가 없습니다."
            web_answer = await _generate_checked_web_search_answer(
                user_query=user_query,
                search_results=web_search_results,
                user_profile_text=payload.get("user_profile_text") or "",
                chat_history=chat_history,
            )
            yield _sse({"type": "chunk", "text": disclaimer + "\n\n"})
            yield _sse({"type": "web_search", "sources": web_search_results})
            yield _sse({"type": "chunk", "text": web_answer})
            answer = disclaimer + "\n\n" + web_answer
            logger.info("[stream][6] recommend web_search answer complete len=%d", len(answer))
        else:
            logger.info("[stream][6] recommend intent='%s'", intent)
            partial = await _handle_recommend(
                intent=intent,
                reply=reply,
                user_profile_text=payload.get("user_profile_text") or "",
                seen_card_ids=payload.get("seen_card_ids") or [],
                qdrant_client=qdrant_client,
                chat_summary=chat_summary,
            )
            yield _sse({"type": "recommend", **partial})
            answer = partial.get("message", "")
            logger.info("[stream][6] recommend done hits=%d", len(partial.get("recommendations", [])))

    elif intent == "recommend_reason":
        logger.info("[stream][6] recommend_reason — generating reason")
        recommend_reason_context = (
            build_recommend_reason_context(card_data) if card_data else card_context
        )
        generation = await generate_recommend_reason(
            user_profile_text=payload.get("user_profile_text") or "",
            card_context=recommend_reason_context,
        )
        total_tokens += generation["prompt_tokens"]
        answer = generation["answer"]
        yield _sse({"type": "chunk", "text": answer})
        logger.info("[stream][6] recommend_reason done tokens=%d", generation["prompt_tokens"])

    else:
        logger.info("[stream][6] fallback intent='%s'", intent)
        answer = reply or ""
        if answer:
            yield _sse({"type": "chunk", "text": answer})

    # ── Done event with final metadata ────────────────────────────────────────
    chat_history.append({"role": "user",      "content": user_query})
    chat_history.append({"role": "assistant", "content": answer})

    logger.info(
        "[stream][7] done total_tokens=%d daily_life_count=%d clarifying_question_count=%d",
        total_tokens, daily_life_count, clarifying_question_count,
    )
    yield _sse({
        "type":                       "done",
        "new_summary":                new_summary,
        "recent_msg_ids":             recent_msg_ids,
        "prompt_tokens":              total_tokens,
        "foul_count":                 payload.get("foul_count", 0),
        "daily_life_count":           daily_life_count,
        "clarifying_question_count":  clarifying_question_count,
        "chat_history":               chat_history,
    })


















