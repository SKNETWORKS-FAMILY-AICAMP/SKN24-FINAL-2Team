"""
agents/chatbot/graph.py

LangGraph StateGraph for the chatbot's *per-turn orchestration decision* —
card-context prep, history compression, intent classification, and the
routing overrides (explicit card request, daily_life escalation, clarifying-
question escalation, policity_docs/DuckDuckGo source judging). This mirrors
the StateGraph + checkpointer pattern already used in agents/debate/graph.py.

Deliberately NOT included here: answer generation/streaming and the bias-check
generate-N-candidates-concurrently pattern (_handle_card_inquiry, _handle_recommend,
the SSE loop in chatbot.py). Those stay as plain async functions called from
chatbot.py after this graph resolves "what should we do this turn" — folding
them into graph nodes would force the latency-tuned concurrent bias-check
logic and token-by-token SSE streaming through LangGraph's per-node-output
model, which doesn't map onto either without losing the speed they were
written for.

A MemorySaver checkpointer is enough here: Django remains the source of truth
for cross-request state (daily_life_count, clarifying_question_count,
chat_history, chat_summary are all sent in on `payload` and returned in the
`done` SSE event for Django to persist, unchanged from before). The
checkpointer only needs to span a single request's prep -> classify run.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from .intent_signals import (
    _prior_turn_was_clarifying_question,
    _query_explicitly_requests_cards,
    _query_signals_recommend_reason,
)
from .llm import (
    classify_user_intent,
    compress_history,
    judge_source_relevance,
    llm_signals_recommendation,
)
from .qdrant import (
    _build_card_context,
    build_eligibility_card_context,
    build_full_card_context,
    fetch_card_by_id,
    search_policy_docs,
)
from .web_search import duckduckgo_search

logger = logging.getLogger(__name__)


class ChatGraphState(TypedDict, total=False):
    # ── inputs (set once, before invoke) ──────────────────────────────────
    user_query:                 str
    card_id:                    Optional[str]
    card_data:                  Optional[Dict[str, Any]]
    chat_history:                List[Dict[str, Any]]
    user_profile_text:          str
    seen_card_ids:               List[int]
    request_summary:            bool
    daily_life_count:           int
    clarifying_question_count:  int

    # ── prep node outputs ──────────────────────────────────────────────────
    eligibility_card_context:   str
    full_card_context:          str
    card_title:                  str
    category_id:                 Optional[int]
    new_summary:                str
    recent_turns:                 List[Dict[str, Any]]
    allow_recommend:             bool
    allow_recommend_reason:      bool

    # ── classify node outputs ─────────────────────────────────────────────
    intent:                      str
    reply:                       str
    reason:                      str
    total_tokens:                 int
    card_inquiry_routing:         Optional[str]
    policy_doc_context:          str
    web_search_results:           list
    recommend_web_fallback:      bool


async def _prep_node(state: ChatGraphState, config) -> dict:
    qdrant_client = config["configurable"]["qdrant_client"]
    card_data = state.get("card_data")
    card_id = state.get("card_id")
    chat_history = state.get("chat_history") or []
    user_query = state["user_query"]

    eligibility_card_context = ""
    full_card_context = ""
    card_title = ""
    category_id: Optional[int] = None

    if card_data:
        card_title = card_data.get("title", "")
        category_id = card_data.get("category_id")
        eligibility_card_context = build_eligibility_card_context(card_data)
        full_card_context = build_full_card_context(card_data)
        logger.info("[graph][prep] card_data provided by Django, title='%s' category_id=%s", card_title, category_id)
    elif card_id:
        logger.info("[graph][prep] no card_data — falling back to Qdrant intro-only fetch for card_id=%s", card_id)
        card_payload = fetch_card_by_id(card_id, qdrant_client=qdrant_client)
        full_card_context = _build_card_context(card_payload)
        card_title = card_payload.get("title", "")
        category_id = card_payload.get("category_id")
        logger.info("[graph][prep] card fetched title='%s' category_id=%s", card_title, category_id)
    else:
        logger.info("[graph][prep] no card_id — skipping card fetch")

    recent_turns = chat_history[-4:]

    async def _maybe_compress_history():
        if not state.get("request_summary"):
            return ""
        logger.info("[graph][prep] compressing chat history (%d messages)", len(chat_history))
        summary = await compress_history(chat_history)
        logger.info("[graph][prep] history compressed")
        return summary

    async def _recommend_gate():
        if _prior_turn_was_clarifying_question(chat_history):
            return True
        return await llm_signals_recommendation(user_query)

    new_summary, allow_recommend = await asyncio.gather(
        _maybe_compress_history(),
        _recommend_gate(),
    )

    return {
        "eligibility_card_context": eligibility_card_context,
        "full_card_context":        full_card_context,
        "card_title":                card_title,
        "category_id":               category_id,
        "new_summary":               new_summary,
        "recent_turns":               recent_turns,
        "allow_recommend":           allow_recommend,
        "allow_recommend_reason":    _query_signals_recommend_reason(user_query),
    }


async def _classify_node(state: ChatGraphState, config) -> dict:
    qdrant_client = config["configurable"]["qdrant_client"]
    user_query = state["user_query"]
    full_card_context = state.get("full_card_context") or ""

    logger.info(
        "[graph][classify] classifying intent allow_recommend=%s allow_recommend_reason=%s",
        state.get("allow_recommend"), state.get("allow_recommend_reason"),
    )
    classification = await classify_user_intent(
        user_query=user_query,
        user_profile_text=state.get("user_profile_text") or "",
        seen_card_ids=state.get("seen_card_ids") or [],
        chat_session_id=config["configurable"].get("chat_session_id"),
        recent_turns=state.get("recent_turns") or [],
        card_context=full_card_context,
        allow_recommend=state.get("allow_recommend", False),
        allow_recommend_reason=state.get("allow_recommend_reason", False),
    )

    intent       = classification["intent"]
    reply        = classification["reply"]
    reason       = classification["reason"]
    total_tokens = classification["prompt_tokens"]

    # User explicitly asked for cards (e.g. "카드 추천", "다른 카드") — force the
    # recommend intent regardless of what the classifier returned. See chatbot.py
    # history for the original rationale (classifier sometimes mislabels these as
    # card_inquiry).
    if not full_card_context and _query_explicitly_requests_cards(user_query):
        logger.info(
            "[graph][classify] explicit card-recommendation request — overriding intent='%s' to recommend",
            intent,
        )
        intent = "recommend_based_on_user_profile"

    # After 3 consecutive daily_life turns, force a recommendation.
    daily_life_count: int = state.get("daily_life_count", 0)
    if intent == "daily_life":
        if daily_life_count >= 3:
            logger.info("[graph][classify] daily_life_count=%d >= 3 — overriding to recommend", daily_life_count)
            intent = "recommend_based_on_user_profile"
            daily_life_count = 0
        else:
            daily_life_count += 1
    else:
        daily_life_count = 0

    clarifying_question_count: int = state.get("clarifying_question_count", 0)
    card_inquiry_routing: Optional[str] = None
    policy_doc_context = ""
    web_search_results: list = []

    if intent == "card_inquiry" and not full_card_context:
        if clarifying_question_count >= 1:
            logger.info(
                "[graph][classify] clarifying_question_count=%d >= 1 — overriding to recommend instead of re-asking",
                clarifying_question_count,
            )
            intent = "recommend_based_on_user_profile"
            clarifying_question_count = 0
        else:
            logger.info("[graph][classify] no card_data — searching policity_docs + DuckDuckGo concurrently for query='%s'", user_query[:100])
            policy_doc_context, web_search_results = await asyncio.gather(
                search_policy_docs(user_query, qdrant_client=qdrant_client),
                duckduckgo_search(user_query),
            )
            source = await judge_source_relevance(user_query, policy_doc_context, web_search_results)
            logger.info("[graph][classify] source relevance judge picked '%s'", source)
            if source == "docs":
                card_inquiry_routing = "policity_docs"
                web_search_results = []
                clarifying_question_count = 0
            elif source == "web":
                card_inquiry_routing = "web_search"
                policy_doc_context = ""
                clarifying_question_count = 0
            else:
                card_inquiry_routing = "clarifying_question"
                policy_doc_context = ""
                web_search_results = []
                clarifying_question_count += 1
    else:
        clarifying_question_count = 0

    recommend_web_fallback = False
    if intent == "recommend_based_on_user_query" and not full_card_context:
        if _query_explicitly_requests_cards(user_query):
            logger.info("[graph][classify] explicit card-recommendation request — skipping policity_docs/DDG gate")
        else:
            logger.info("[graph][classify] no card_data — checking policity_docs + DuckDuckGo concurrently before recommending cards")
            recommend_doc_context, web_search_results = await asyncio.gather(
                search_policy_docs(user_query, qdrant_client=qdrant_client),
                duckduckgo_search(user_query),
            )
            source = await judge_source_relevance(user_query, recommend_doc_context, web_search_results)
            logger.info("[graph][classify] source relevance judge picked '%s'", source)
            if source == "docs":
                logger.info("[graph][classify] judge picked policity_docs — proceeding with card recommendation")
            elif source == "web":
                recommend_web_fallback = True
            else:
                web_search_results = []

    logger.info(
        "[graph][classify] intent='%s' reason='%s' tokens=%d daily_life_count=%d clarifying_question_count=%d "
        "card_inquiry_routing=%s recommend_web_fallback=%s",
        intent, reason, total_tokens, daily_life_count, clarifying_question_count,
        card_inquiry_routing, recommend_web_fallback,
    )

    return {
        "intent":                      intent,
        "reply":                       reply,
        "reason":                      reason,
        "total_tokens":                 total_tokens,
        "daily_life_count":             daily_life_count,
        "clarifying_question_count":    clarifying_question_count,
        "card_inquiry_routing":         card_inquiry_routing,
        "policy_doc_context":           policy_doc_context,
        "web_search_results":            web_search_results,
        "recommend_web_fallback":       recommend_web_fallback,
    }


def build_chatbot_graph(checkpointer=None):
    """Compile the prep -> classify orchestration graph. Called once at module
    import time in chatbot.py; reused (thread-safe, stateless aside from the
    checkpointer) across requests.
    """
    graph = StateGraph(ChatGraphState)
    graph.add_node("prep", _prep_node)
    graph.add_node("classify", _classify_node)
    graph.set_entry_point("prep")
    graph.add_edge("prep", "classify")
    graph.add_edge("classify", END)

    if checkpointer is None:
        checkpointer = MemorySaver()

    return graph.compile(checkpointer=checkpointer)
