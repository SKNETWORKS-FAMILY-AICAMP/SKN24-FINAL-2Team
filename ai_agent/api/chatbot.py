"""
api/chatbot.py
POLICITY 챗봇 FastAPI 라우터

POST /chat/message          — 챗봇 응답 (단일)
POST /chat/recommendations  — 첫 채팅 카드 추천
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agents.chatbot.chatbot import (
    ChatBlockedError,
    check_message_card_consistency,
    generate_recommend_message,
    # handle_chat_request,
    handle_chat_request_stream,
    new_chat_card_recommendations,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chatbot"])


def _get_qdrant_client():
    from main import _state
    qdrant_client = _state.get("qdrant_client")
    if qdrant_client is None:
        raise HTTPException(status_code=503, detail="Qdrant 초기화 중입니다.")
    return qdrant_client


# ── 스키마 ───────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    user_id:   Union[str, int]
    last_20_msgs:      Optional[str] = None
    user_query: Optional[str] = None

    card_id:         Optional[Union[str, int]] = None
    card_data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Full InfoCard row from MySQL (Django), nested/deserialized — "
                     "card_id, type, title, intro, summary{}, core_content, perspectives[], "
                     "debate_topic, source_urls[]. Qdrant only embeds intro, so this is the "
                     "source of full content when card_id is set.",
    )
    chat_session_id: Optional[str] = None

    chat_history: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Prior turns [{role, content}]; loaded from MySQL by Django.",
    )
    chat_summary: Optional[str] = Field(
        default=None,
        description="Existing compressed summary string from MySQL.",
    )
    foul_count: int = Field(
        default=0,
        description="Cumulative profanity/bias violation counter; persisted by Django.",
    )
    daily_life_count: int = Field(
        default=0,
        description="Consecutive daily-life turn counter for this session; persisted by Django.",
    )
    clarifying_question_count: int = Field(
        default=0,
        description="Consecutive unresolved card_inquiry clarifying-question counter; persisted by Django.",
    )
    request_summary: bool
    recent_msg_ids:    Optional[List[int]] = Field(default=None, description="IDs of recent messages in the session.")
    user_profile_text: Optional[str]       = Field(default=None, description="User interest profile text for recommendations.")
    seen_card_ids:     Optional[List[int]] = Field(default=None, description="Card IDs the user has already seen.")


class _ChatResponseBase(BaseModel):
    chat_session_id: str
    card_id:         Optional[Union[str, int]] = None
    intent_reason:   Optional[str]             = None
    recent_msg_ids:  List[int]
    new_summary:     str
    prompt_tokens:   int
    foul_count:      int


class ConversationalChatResponse(_ChatResponseBase):
    """Returned for daily_life, card_inquiry, and recommend_reason intents."""
    routing: Literal["daily_life", "card_inquiry", "recommend_reason"]
    answer:  str


class RecommendChatResponse(_ChatResponseBase):
    """Returned for recommend intent (profile- or query-based)."""
    routing:         Literal["recommend"]
    message:         str
    recommendations: List[str]


ChatResponse = Annotated[
    Union[ConversationalChatResponse, RecommendChatResponse],
    Field(discriminator="routing"),
]


class RecommendationRequest(BaseModel):
    user_id: int
    category_names: list[str]
    top_k: int = 3
    chat_summary: Optional[str] = None


class CardHit(BaseModel):
    id:      str
    score:   Optional[float] = None
    title:   Optional[str]   = None
    payload: Optional[Dict]  = None


class RecommendationResponse(BaseModel):
    user_id: int
    message: Optional[str] = None
    results: List[CardHit]
    total:   int


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

# @router.post(
#     "/message",
#     response_model=ChatResponse,
#     summary="챗봇 메시지 전송"
# )
# async def send_message(body: ChatRequest):
#     """

#     """
#     try:
#         result = await handle_chat_request(body.model_dump(), qdrant_client=_get_qdrant_client())
#         result["foul_count"] = body.foul_count
#         # return new_summary, recent_msg_ids, chat_session_id
#         return result
#     except ChatBlockedError as e:
#         detail = dict(e.detail)
#         detail["foul_count"] = body.foul_count + 1
#         raise HTTPException(status_code=e.status_code, detail=detail)
#     except Exception as e:
#         logger.exception("챗봇 응답 오류")
#         raise HTTPException(status_code=500, detail=str(e))

########################################################################
@router.post("/message/stream", summary="챗봇 메시지 전송 (스트리밍)")
async def send_message_stream(body: ChatRequest):
    """
    SSE streaming variant of /chat/message.

    Each event is `data: <JSON>\\n\\n` with one of:
      - {"type": "meta", "routing": ..., "chat_session_id": ..., ...}
      - {"type": "chunk", "text": "..."}
      - {"type": "recommend", "routing": "recommend", "recommendations": [...], "message": "..."}
      - {"type": "done", "new_summary": ..., "prompt_tokens": ..., "foul_count": ...}
    """
    async def _generate():
        try:
            async for chunk in handle_chat_request_stream(
                body.model_dump(), qdrant_client=_get_qdrant_client()
            ):
                yield chunk
        except ChatBlockedError as e:
            import json
            detail = dict(e.detail)
            detail["foul_count"] = body.foul_count + 1
            yield f"data: {json.dumps({'type': 'error', **detail}, ensure_ascii=False)}\n\n"
        except Exception as e:
            import json
            logger.exception("챗봇 스트리밍 오류")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


########################################################################
@router.post("/recommendations", response_model=RecommendationResponse, summary="첫 채팅 카드 추천")
async def recommend_cards(body: RecommendationRequest):
    """
    Django가 사용자 관심사 카테고리를 넘기면, Qdrant cards 컬렉션에서
    관련 카드를 검색해 반환합니다. (첫 채팅 / 신규 세션 전용)
    """
    try:
        logger.info("[recommendations] START | user_id=%s | top_k=%s | category_names=%s",
                    body.user_id, body.top_k, body.category_names)

        user_profile_text = ", ".join(body.category_names)

        logger.info("[recommendations] Searching Qdrant for matching cards...")
        raw = await asyncio.to_thread(
            new_chat_card_recommendations,
            user_profile_text=user_profile_text,
            top_k=3,
            qdrant_client=_get_qdrant_client(),
        )

        recommendations = raw["recommendations"]
        logger.info("[recommendations] Qdrant returned %d results.", len(recommendations))

        hits = [
            CardHit(
                id=rec["card_id"],
                score=rec.get("score"),
                title=rec.get("title"),
                payload=rec,
            )
            for rec in recommendations
        ]

        if not hits:
            logger.warning("[recommendations] No cards found for user_id=%s", body.user_id)
            return RecommendationResponse(
                user_id=body.user_id,
                message="아직 추천할 카드를 찾지 못했어요. 관심사를 조금 더 알려주시면 더 잘 찾아드릴게요!",
                results=[],
                total=0,
            )

        hits_titles_combined = ", ".join(hit.title for hit in hits if hit.title)
        logger.info("[recommendations] Card titles: '%s'", hits_titles_combined)

        # #7: pass content snippets so the LLM can write a more specific intro message
        card_summaries = [
            {"title": hit.title or "", "content": (hit.payload or {}).get("content", "") if hit.payload else ""}
            for hit in hits
        ]

        logger.info("[recommendations] Generating recommendation message...")
        message = await generate_recommend_message(user_profile_text, hits_titles_combined, card_summaries, chat_summary=body.chat_summary)
        logger.info("[recommendations] Message preview='%s'", (message or "")[:100])

        async def _log_consistency_check():
            consistent = await check_message_card_consistency(message, card_summaries)
            if not consistent:
                logger.warning(
                    "[recommendations] MISMATCH — message does not match cards | "
                    "user_id=%s | message='%s' | cards=%s",
                    body.user_id, message, hits_titles_combined,
                )

        asyncio.create_task(_log_consistency_check())

        logger.info("[recommendations] DONE | user_id=%s | total=%d", body.user_id, len(hits))
        return RecommendationResponse(
            user_id=body.user_id,
            message=message,
            results=hits,
            total=len(hits),
        )

    except Exception:
        logger.exception("카드 추천 오류 user_id=%s", body.user_id)
        raise HTTPException(status_code=500, detail="카드 추천 처리 중 오류가 발생했습니다.")
