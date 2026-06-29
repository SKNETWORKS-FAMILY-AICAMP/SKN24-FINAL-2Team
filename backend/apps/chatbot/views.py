"""
apps//views.py
======================
Django-side HTTP clients for the two FastAPI LLM endpoints.

Flow
----
POST /chat/recommendations
    1. Resolve user_interests → category names (MySQL)
    2. POST  →  FastAPI /recommendations  (Qdrant vector search)
    3. Fetch InfoCard rows by returned card_ids (MySQL)
    4. Persist ChatSession + ChatMessage + ChatMsgCard
    5. Return card list to frontend

POST /chat/message
    1. Load active ChatSession + last ChatSummary + recent ChatMessages
    2. POST  →  FastAPI /chat  (intent routing, RAG, bias check)
    3. Persist ChatMessage + ChatMsgCard + ChatSummary if compressed
    4. Return routing result + answer/recommendations to frontend
"""

import json
import logging
import os
from typing import Any

import httpx
from django.conf import settings
from django.db import models, transaction
from django.http import StreamingHttpResponse
from dotenv import load_dotenv
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.cards.models import Bookmark, InfoCard
from apps.users.models import User as ServiceUser, UserInterest
from .models import ChatMessage, ChatMsgCard, ChatSession, ChatSummary


class IsFirstChatView(APIView):
    """GET /api/chatbot/is-first-chat/ — returns whether the user has ever had a chat session."""

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            user = _resolve_service_user(request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        has_session = ChatSession.objects.filter(user=user).exists()
        return Response({"is_first_chat": not has_session}, status=status.HTTP_200_OK)


load_dotenv()

logger = logging.getLogger(__name__)

        

def _resolve_service_user(auth_user) -> ServiceUser:
    """Django auth.User → custom apps.users.User (email 기준)."""
    try:
        return ServiceUser.objects.get(email=auth_user.email)
    except ServiceUser.DoesNotExist:
        raise ValueError(f"No service user found for email {auth_user.email}")

# ── FastAPI base URL (set in settings.py / .env) ─────────────────────────────
# e.g. LLM_SERVER_URL = "http://llm-server:8001"
LLM_SERVER_URL = os.getenv("LLM_SERVER_URL")
LLM_TIMEOUT: float  = getattr(settings, "LLM_TIMEOUT", 30.0)

# Recommendations currently live on a separate FastAPI host.
RECOMMENDATIONS_URL = f"{LLM_SERVER_URL}/chat/recommendations"
CHAT_URL = f"{LLM_SERVER_URL}/chat/message"


# ─────────────────────────────────────────────────────────────────────────────
#  Helper: call FastAPI
# ─────────────────────────────────────────────────────────────────────────────

def _post_llm(path: str, payload: dict) -> dict[str, Any]:
    """
    POST to the FastAPI LLM server and return the parsed JSON body.
    Raises httpx.HTTPStatusError on 4xx/5xx.
    """
    url = f"{LLM_SERVER_URL}{path}"
    with httpx.Client(timeout=LLM_TIMEOUT) as client:
        resp = client.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


# ─────────────────────────────────────────────────────────────────────────────
#  GET /chat/history  &  GET/DELETE /chat/history/<id>
# ─────────────────────────────────────────────────────────────────────────────

class ChatHistoryListView(APIView):
    """
    List the requesting user's chat sessions (most recently updated first),
    for rendering the history sidebar.

    Response
    --------
    {
        "sessions": [
            {
                "chat_session_id": 42,
                "session_title": "청년 월세 지원 정책",
                "created_at": "...",
                "updated_at": "..."
            },
            ...
        ]
    }
    """

    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        try:
            user = _resolve_service_user(request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        sessions = (
            ChatSession.objects.filter(user=user, is_delete=0)
            .order_by("-updated_at")
        )

        return Response(
            {
                "sessions": [
                    {
                        "chat_session_id": s.chat_session_id,
                        "session_title":   s.session_title,
                        "created_at":      s.created_at,
                        "updated_at":      s.updated_at,
                    }
                    for s in sessions
                ],
            },
            status=status.HTTP_200_OK,
        )


class ChatHistoryDetailView(APIView):
    """
    GET    /chat/history/<chat_session_id>/  → full message log for a session
    DELETE /chat/history/<chat_session_id>/  → soft-delete a session

    GET response
    ------------
    {
        "chat_session_id": 42,
        "session_title": "청년 월세 지원 정책",
        "messages": [
            {
                "chat_msg_id": 88,
                "input": "...",
                "output": "...",
                "is_memory": 1,
                "created_at": "...",
                "cards": [ { "card_id": 7, "card_title": "...", ... }, ... ]
            },
            ...
        ]
    }
    """

    permission_classes = [IsAuthenticated]

    def _get_session(self, request: Request, chat_session_id: int) -> ChatSession | None:
        user = _resolve_service_user(request.user)
        return ChatSession.objects.filter(
            chat_session_id=chat_session_id, user=user, is_delete=0
        ).first()

    def get(self, request: Request, chat_session_id: int) -> Response:
        try:
            session = self._get_session(request, chat_session_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if session is None:
            return Response({"detail": "Chat session not found."}, status=status.HTTP_404_NOT_FOUND)

        messages = (
            ChatMessage.objects.filter(chat_session=session)
            .order_by("created_at")
            .prefetch_related("recommended_cards__card__category")
        )

        def serialize_msg(msg: ChatMessage) -> dict[str, Any]:
            cards = [
                {
                    "chat_msg_card_id": link.chat_msg_card_id,
                    "is_selected":   link.is_selected,
                    "card_id":       link.card.pk,
                    "category_id":   link.card.category_id,
                    "category_name": link.card.category.category_name,
                    "type":          link.card.type,
                    "card_title":    link.card.card_title,
                    "intro":         link.card.intro,
                    "summary":       link.card.summary,
                    "core_content":  link.card.core_content,
                    "perspectives":  link.card.perspectives,
                    "debate_topic":  link.card.debate_topic,
                    "created_at":    link.card.created_at,
                    "updated_at":    link.card.updated_at,
                }
                for link in msg.recommended_cards.all()
            ]
            return {
                "chat_msg_id": msg.chat_msg_id,
                "input":       msg.input,
                "output":      msg.output,
                "is_memory":   msg.is_memory,
                "created_at":  msg.created_at,
                "cards":       cards,
            }

        return Response(
            {
                "chat_session_id": session.chat_session_id,
                "session_title":   session.session_title,
                "messages":        [serialize_msg(m) for m in messages],
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request: Request, chat_session_id: int) -> Response:
        try:
            session = self._get_session(request, chat_session_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if session is None:
            return Response({"detail": "Chat session not found."}, status=status.HTTP_404_NOT_FOUND)

        title = request.data.get("session_title", "").strip()
        if not title:
            return Response({"detail": "session_title is required."}, status=status.HTTP_400_BAD_REQUEST)

        session.session_title = title[:100]
        session.save(update_fields=["session_title"])
        return Response({"session_title": session.session_title}, status=status.HTTP_200_OK)

    def delete(self, request: Request, chat_session_id: int) -> Response:
        try:
            session = self._get_session(request, chat_session_id)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if session is None:
            return Response({"detail": "Chat session not found."}, status=status.HTTP_404_NOT_FOUND)

        session.is_delete = 1
        session.save(update_fields=["is_delete"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─────────────────────────────────────────────────────────────────────────────
#  PATCH /chat/cards/<chat_msg_card_id>/select/
# ─────────────────────────────────────────────────────────────────────────────

class ChatMsgCardSelectView(APIView):
    """
    PATCH /chat/cards/<chat_msg_card_id>/select/  → mark a recommended card as selected.

    Sets CHAT_MSG_CARDS.is_selected = 1 for the given row, scoped to the
    requesting user's own chat sessions.
    """

    permission_classes = [IsAuthenticated]

    def patch(self, request: Request, chat_msg_card_id: int) -> Response:
        try:
            user = _resolve_service_user(request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        link = ChatMsgCard.objects.filter(
            chat_msg_card_id=chat_msg_card_id,
            chat_message__chat_session__user=user,
        ).first()
        if link is None:
            return Response({"detail": "Card not found."}, status=status.HTTP_404_NOT_FOUND)

        if link.is_selected != 1:
            link.is_selected = 1
            link.save(update_fields=["is_selected"])

        return Response(
            {"chat_msg_card_id": link.chat_msg_card_id, "is_selected": link.is_selected},
            status=status.HTTP_200_OK,
        )

# ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

















# ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
#  POST /chat/recommendations
# ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _build_card_data(card: InfoCard) -> dict[str, Any]:
    """Deserialize an InfoCard row into the nested structure the FastAPI
    chatbot expects, so it can build purpose-specific context slices
    (light summary vs. full content) instead of one flat blob."""
    def _safe_json(raw, default):
        try:
            return json.loads(raw) if raw else default
        except (TypeError, ValueError):
            return default

    return {
        "card_id":      card.pk,
        "type":         card.type,
        "category_id":  card.category_id,
        "category_name": card.category.category_name,
        "title":        card.card_title,
        "intro":        card.intro,
        "summary":      _safe_json(card.summary, {}),
        "core_content": card.core_content,
        "perspectives": _safe_json(card.perspectives, []),
        "debate_topic": card.debate_topic,
        "source_urls":  _safe_json(card.source_urls, []),
        "created_at":   card.created_at.isoformat() if card.created_at else None,
        "updated_at":   card.updated_at.isoformat() if card.updated_at else None,
    }


def _build_user_profile_text(user) -> str:
    parts = []

    # 4. Demographics
    if user.gender:
        parts.append(f"성별: {user.gender}")
    if user.region:
        parts.append(f"거주지: {user.region.sido} {user.region.sigungu}")
    if user.age:
        parts.append(f"나이: {user.age}세")

    # 1. Declared interest categories 
    category_names = list(
        UserInterest.objects.filter(user=user)
        .values_list("category__category_name", flat=True)
    )
    if category_names:
        parts.append(f"관심 카테고리: {', '.join(category_names)}")

    # 2. Cards the user has clicked before 
    selected_card_titles = list(
        InfoCard.objects.filter(
            chat_msg_cards__chat_message__chat_session__user=user,
            chat_msg_cards__is_selected=1,
        )
        .values_list("card_title", flat=True)
        .distinct()[:10]
    )
    if selected_card_titles:
        parts.append(f"관심 있게 읽은 카드: {'; '.join(selected_card_titles)}")

    # 3. Bookmarked cards
    bookmarked_titles = list(
        InfoCard.objects.filter(bookmark__user=user)
        .values_list("card_title", flat=True)[:10]
    )
    if bookmarked_titles:
        parts.append(f"저장한 카드: {'; '.join(bookmarked_titles)}")

    return "\n".join(parts)


class RecommendationsView(APIView):
    """
    POST /chat/recommendations/

    Called when the user opens a new chat session before sending any message.
    Sends only the user's declared interest categories to FastAPI to
    vector-search Qdrant for the best matching InfoCards (kept minimal —
    the full profile-text version was too slow).

    No ChatSession or ChatMessage is written here — those are created by
    ChatView when the user sends their first real message.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> Response:
        try:
            user = _resolve_service_user(request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # ── 1. Declared interest categories ────────────────────────────────
        category_names = list(
            UserInterest.objects.filter(user=user)
            .values_list("category__category_name", flat=True)
        )
        if not category_names:
            return Response(
                {"detail": "No interests found for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 2. Call FastAPI /chat/recommendations ─────────────────────────────
        try:
            llm_resp = _post_llm(
                "/chat/recommendations",
                {
                    "user_id":        user.pk,
                    "category_names": category_names,
                    "top_k":          3,
                },
            )
        except httpx.HTTPStatusError as exc:
            logger.error("FastAPI /recommendations error: %s", exc.response.text)
            try:
                llm_detail = exc.response.json()
            except Exception:
                llm_detail = exc.response.text
            return Response(
                {"detail": "LLM server error.", "llm_detail": llm_detail},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except httpx.RequestError as exc:
            logger.error("FastAPI /recommendations unreachable: %s", exc)
            return Response(
                {"detail": "LLM server unreachable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        llm_message: str = llm_resp.get("message", "")

        # ── 3. Extract card_ids from Qdrant hits ──────────────────────────────
        card_ids: list[int] = []
        for hit in llm_resp.get("results", []):
            raw_card_id = (hit.get("payload") or {}).get("card_id")
            if raw_card_id is not None:
                try:
                    card_ids.append(int(raw_card_id))
                except (TypeError, ValueError):
                    logger.warning("Non-integer card_id in Qdrant payload: %s", raw_card_id)

        # ── 4. Fetch InfoCard rows from MySQL ─────────────────────────────────
        cards_qs = InfoCard.objects.select_related("category").filter(pk__in=card_ids)
        card_map = {c.pk: c for c in cards_qs}
        # Preserve Qdrant ranking order; cap at 3
        ordered_cards = [card_map[cid] for cid in card_ids if cid in card_map][:3]

        # ── 5. Return cards to frontend (no DB writes — session/messages are
        #       created only when the user actually sends their first query) ──
        return Response(
            {
                "message": llm_message,
                "cards": [
                    {
                        "card_id":       card.pk,
                        "category_id":   card.category_id,
                        "category_name": card.category.category_name,
                        "type":          card.type,
                        "card_title":    card.card_title,
                        "intro":         card.intro,
                        "summary":       card.summary,
                        "core_content":  card.core_content,
                        "perspectives":  card.perspectives,
                        "debate_topic":  card.debate_topic,
                        "created_at":    card.created_at,
                        "updated_at":    card.updated_at,
                    }
                    for card in ordered_cards
                ],
            },
            status=status.HTTP_200_OK,
        )


# ─────────────────────────────────────────────────────────────────────────────
#  POST /chat/message
# ─────────────────────────────────────────────────────────────────────────────

class ChatView(APIView):
    """

    """

    permission_classes = [IsAuthenticated]
    # From frontend (request body)
    def post(self, request: Request) -> Response:
        try:
            user = _resolve_service_user(request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        data        = request.data
        user_query: str       = data.get("user_query", "").strip()
        card_id: str | None   = data.get("card_id")
        session_id: int | None = data.get("chat_session_id")
        reco_card_ids: list   = data.get("reco_card_ids") or []
        reco_message: str     = data.get("reco_message", "").strip() or "오늘의 카드 추천이야. 뭐든지 물어봐! 😊"
        selected_reco_card_id: str | None = str(data.get("selected_reco_card_id", "") or "")

        if not user_query:
            return Response(
                {"detail": "user_query is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── 1. Build personalized profile + exclusion list ────────────────────
        user_profile_text = _build_user_profile_text(user)
        if not user_profile_text:
            return Response(
                {"detail": "No interests found for this user."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        seen_card_ids: list[int] = list(
            ChatMsgCard.objects.filter(
                chat_message__chat_session__user=user
            )
            .values_list("card_id", flat=True)
            .distinct()
        )


        # ── 2. Load existing ChatSession (new sessions are created only after
        #       a successful LLM response, to avoid ghost sessions on blocks).
        session = None
        if session_id:
            try:
                session = ChatSession.objects.get(
                    chat_session_id=session_id, user=user, is_delete=0
                )
            except ChatSession.DoesNotExist:
                return Response(
                    {"detail": "Chat session not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        # ── 3. Load chat history and summary (existing sessions only) ─────────
        chat_history: list[dict] = []
        chat_summary: str = ""
        request_summary: bool = False
        recent_msg_ids = None
        latest_summary_obj = None

        if session:
            # Rename auto-generated recommendation titles once the user actually
            # speaks — the first real message is a much more useful label than
            # the generic "추천 카드 (...)" title set by RecommendationsView.
            has_real_message = ChatMessage.objects.filter(
                chat_session=session
            ).exclude(input="").exists()
            if not has_real_message and session.session_title != user_query[:50]:
                session.session_title = user_query[:50]
                session.save(update_fields=["session_title"])

            recent_msgs = (
                ChatMessage.objects.filter(chat_session=session, is_memory=0)
                .order_by("-created_at")[:20]
            )
            if len(recent_msgs) >= 20:
                request_summary = True
                recent_msg_ids = [msg.chat_msg_id for msg in recent_msgs]
            for msg in reversed(recent_msgs):
                if msg.input:
                    chat_history.append({"role": "user",      "content": msg.input})
                if msg.output:
                    chat_history.append({"role": "assistant", "content": msg.output})

            latest_summary_obj = (
                ChatSummary.objects.filter(chat_session=session)
                .order_by("-created_at")
                .first()
            )
            chat_summary = latest_summary_obj.chat_summary if latest_summary_obj else ""

        daily_life_count: int = int(data.get("daily_life_count", 0) or 0)
        clarifying_question_count: int = int(data.get("clarifying_question_count", 0) or 0)

        # ── 5. Call FastAPI /chat/message ─────────────────────────────────────────────
        llm_payload = {
            "user_id":                        str(user.pk),
            "user_query":                     user_query,
            "card_id":                        card_id,
            "chat_session_id":                str(session.chat_session_id) if session else "",
            "chat_history":                   chat_history,
            "chat_summary":                   chat_summary,
            "foul_count":                     user.foul_count,
            "daily_life_count":               daily_life_count,
            "clarifying_question_count":      clarifying_question_count,
            "request_summary":                request_summary,
            "recent_msg_ids":                 recent_msg_ids,
            "user_profile_text":              user_profile_text,
            "seen_card_ids":                  seen_card_ids,
        }

        try:
            llm_resp = _post_llm("/chat/message", llm_payload)
        except httpx.HTTPStatusError as exc:
            logger.error("FastAPI /chat error: %s", exc.response.text)
            try:
                err_body = exc.response.json()
            except Exception:
                err_body = {}
            detail = err_body.get("detail") or {}
            error_type = detail.get("error")
            if error_type in ("profanity_detected", "biased_query"):
                llm_foul_count = detail.get("foul_count")
                if llm_foul_count is not None:
                    user.__class__.objects.filter(pk=user.pk).update(foul_count=llm_foul_count)
                    user.foul_count = llm_foul_count
                else:
                    user.__class__.objects.filter(pk=user.pk).update(
                        foul_count=models.F("foul_count") + 1
                    )
                    user.refresh_from_db(fields=["foul_count"])
                if session and session.session_title == user_query[:50]:
                    session.session_title = "제목 없음"
                    session.save(update_fields=["session_title"])
                if error_type == "profanity_detected":
                    return Response(
                        {
                            "error":      "profanity_detected",
                            "reason":     detail.get("reason", ""),
                            "censored":   detail.get("censored", ""),
                            "foul_count": user.foul_count,
                        },
                        status=status.HTTP_200_OK,
                    )
                return Response(
                    {
                        "error":      "biased_query",
                        "message":    detail.get("message", "특정 정당·후보·정책을 지지하거나 비방하는 답변하지 않습니다."),
                        "foul_count": user.foul_count,
                    },
                    status=status.HTTP_200_OK,
                )
            return Response(
                {"detail": "LLM server error.", "llm_detail": err_body},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except httpx.RequestError as exc:
            logger.error("FastAPI /chat unreachable: %s", exc)
            return Response(
                {"detail": "LLM server unreachable."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # Profanity gate — FastAPI returns this instead of a normal response
        if llm_resp.get("error") == "profanity_detected":
            llm_foul_count = llm_resp.get("foul_count")
            if llm_foul_count is not None:
                user.__class__.objects.filter(pk=user.pk).update(foul_count=llm_foul_count)
                user.foul_count = llm_foul_count
            else:
                user.__class__.objects.filter(pk=user.pk).update(
                    foul_count=models.F("foul_count") + 1
                )
                user.refresh_from_db(fields=["foul_count"])
            if session and session.session_title == user_query[:50]:
                session.session_title = "제목 없음"
                session.save(update_fields=["session_title"])
            return Response(
                {
                    "error":      "profanity_detected",
                    "reason":     llm_resp.get("reason"),
                    "censored":   llm_resp.get("censored"),
                    "foul_count": user.foul_count,
                },
                status=status.HTTP_200_OK,
            )
        routing:                  str       = llm_resp.get("routing", "card_inquiry")
        answer:                   str       = llm_resp.get("answer") or llm_resp.get("message") or ""
        recommendations:          list[str] = llm_resp.get("recommendations") or []
        new_summary:              str       = llm_resp.get("new_summary", "")
        resp_foul_count:          int | None = llm_resp.get("foul_count")
        resp_daily_life_count:    int        = llm_resp.get("daily_life_count", daily_life_count)
        resp_clarifying_question_count: int  = llm_resp.get("clarifying_question_count", clarifying_question_count)
        resp_recent_msg_ids:      list[int]  = llm_resp.get("recent_msg_ids") or []

        # ── 6. Persist results ────────────────────────────────────────────────
        with transaction.atomic():

            # Create the session now that the LLM responded successfully.
            if session is None:
                session = ChatSession.objects.create(
                    user=user,
                    session_title=user_query[:50],
                )

            # Save the initial recommendation cards as a system-initiated message
            # when a brand-new session is being created for the first time.
            if not session_id and reco_card_ids:
                try:
                    reco_ids_int = [int(cid) for cid in reco_card_ids]
                except (TypeError, ValueError):
                    reco_ids_int = []
                if reco_ids_int:
                    reco_cards_qs = InfoCard.objects.filter(pk__in=reco_ids_int)
                    reco_card_map = {c.pk: c for c in reco_cards_qs}
                    initial_msg = ChatMessage.objects.create(
                        chat_session=session,
                        input="",
                        output=reco_message,
                        is_memory=0,
                    )
                    ChatMsgCard.objects.bulk_create([
                        ChatMsgCard(
                            chat_message=initial_msg,
                            card=reco_card_map[cid],
                            is_selected=1 if str(cid) == selected_reco_card_id else 0,
                        )
                        for cid in reco_ids_int
                        if cid in reco_card_map
                    ])

            # A new message has never been folded into a summary yet, so it
            # always starts as is_memory=0 (사용). The compression step below
            # is the only place that flips a message to is_memory=1 (미사용)
            # once it's been absorbed into a ChatSummary.
            msg = ChatMessage.objects.create(
                chat_session=session,
                input=user_query,
                output=answer,
                is_memory=0,
            )

            # Save the card being discussed (from card_id param) into CHAT_MSG_CARDS
            if card_id:
                try:
                    discussed_card = InfoCard.objects.get(pk=int(card_id))
                    ChatMsgCard.objects.create(
                        chat_message=msg,
                        card=discussed_card,
                        is_selected=1,
                    )
                except (InfoCard.DoesNotExist, TypeError, ValueError):
                    logger.warning("card_id %s not found or invalid, skipping ChatMsgCard creation", card_id)

            # Save recommended card links if routing == "recommend"
            if recommendations:
                card_ids_int = []
                for raw in recommendations:
                    try:
                        card_ids_int.append(int(raw))
                    except (TypeError, ValueError):
                        pass

                cards_qs = InfoCard.objects.filter(pk__in=card_ids_int)
                card_map = {c.pk: c for c in cards_qs}
                ChatMsgCard.objects.bulk_create([
                    ChatMsgCard(chat_message=msg, card=card_map[cid], is_selected=0)
                    for cid in card_ids_int
                    if cid in card_map
                ])

            # Persist new summary and mark compressed messages as is_memory=1
            if new_summary:
                ChatSummary.objects.create(
                    chat_session=session,
                    chat_summary=new_summary,
                    is_memory=0,
                )
                if latest_summary_obj:
                    latest_summary_obj.is_memory = 1
                    latest_summary_obj.save(update_fields=["is_memory"])
                if resp_recent_msg_ids:
                    ChatMessage.objects.filter(
                        chat_msg_id__in=resp_recent_msg_ids,
                        chat_session=session,
                    ).update(is_memory=1)

            # Sync foul_count from LLM response if provided
            if resp_foul_count is not None:
                user.__class__.objects.filter(pk=user.pk).update(foul_count=resp_foul_count)
                user.foul_count = resp_foul_count

            session.save(update_fields=["updated_at"])

        # ── 7. Return to frontend ─────────────────────────────────────────────
        response_body: dict[str, Any] = {
            "chat_session_id":  session.chat_session_id,
            "chat_msg_id":      msg.chat_msg_id,
            "routing":          routing,
            "foul_count":       user.foul_count,
            "daily_life_count": resp_daily_life_count,
            "clarifying_question_count": resp_clarifying_question_count,
        }

        if routing in ("daily_life", "card_inquiry", "recommend", "recommend_reason"):
            response_body["answer"] = answer

        if routing == "recommend" and recommendations:
            response_body["recommendations"] = [int(r) for r in recommendations if str(r).isdigit()]
            # Return chat_msg_card_id for each card so the frontend can PATCH selections
            chat_msg_card_map = {
                str(link["card_id"]): link["chat_msg_card_id"]
                for link in ChatMsgCard.objects.filter(chat_message=msg).values("chat_msg_card_id", "card_id")
            }
            response_body["chat_msg_card_map"] = chat_msg_card_map

        return Response(response_body, status=status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
#  POST /chat/stream
# ─────────────────────────────────────────────────────────────────────────────

CHAT_STREAM_URL = f"{LLM_SERVER_URL}/chat/message/stream"


class ChatStreamView(APIView):
    """
    POST /api/chatbot/stream/

    Streams the LLM response to the browser as SSE (text/event-stream).
    The frontend receives the same event types as FastAPI emits:
      {"type": "meta", ...}
      {"type": "chunk", "text": "..."}
      {"type": "recommend", ...}
      {"type": "done", ...}
      {"type": "error", ...}

    DB writes happen after the "done" event is received from FastAPI,
    still inside the generator so the session/messages are persisted
    before the stream closes.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request: Request) -> StreamingHttpResponse:
        try:
            user = _resolve_service_user(request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        data = request.data
        user_query: str        = data.get("user_query", "").strip()
        card_id: str | None    = data.get("card_id")
        session_id: int | None = data.get("chat_session_id")
        reco_card_ids: list    = data.get("reco_card_ids") or []
        reco_message: str      = data.get("reco_message", "").strip() or "오늘의 카드 추천이야. 뭐든지 물어봐! 😊"
        selected_reco_card_id: str = str(data.get("selected_reco_card_id", "") or "")

        if not user_query:
            return Response({"detail": "user_query is required."}, status=status.HTTP_400_BAD_REQUEST)

        user_profile_text = _build_user_profile_text(user)
        if not user_profile_text:
            return Response({"detail": "No interests found for this user."}, status=status.HTTP_400_BAD_REQUEST)

        seen_card_ids: list[int] = list(
            ChatMsgCard.objects.filter(chat_message__chat_session__user=user)
            .values_list("card_id", flat=True)
            .distinct()
        )

        session = None
        if session_id:
            try:
                session = ChatSession.objects.get(chat_session_id=session_id, user=user, is_delete=0)
            except ChatSession.DoesNotExist:
                return Response({"detail": "Chat session not found."}, status=status.HTTP_404_NOT_FOUND)

        chat_history: list[dict] = []
        chat_summary: str = ""
        request_summary: bool = False
        recent_msg_ids = None
        latest_summary_obj = None

        if session:
            has_real_message = ChatMessage.objects.filter(chat_session=session).exclude(input="").exists()
            if not has_real_message and session.session_title != user_query[:50]:
                session.session_title = user_query[:50]
                session.save(update_fields=["session_title"])

            recent_msgs = (
                ChatMessage.objects.filter(chat_session=session, is_memory=0)
                .order_by("-created_at")[:20]
            )
            if len(recent_msgs) >= 20:
                request_summary = True
                recent_msg_ids = [msg.chat_msg_id for msg in recent_msgs]
            for msg in reversed(recent_msgs):
                if msg.input:
                    chat_history.append({"role": "user", "content": msg.input})
                if msg.output:
                    chat_history.append({"role": "assistant", "content": msg.output})

            latest_summary_obj = (
                ChatSummary.objects.filter(chat_session=session)
                .order_by("-created_at")
                .first()
            )
            chat_summary = latest_summary_obj.chat_summary if latest_summary_obj else ""

        daily_life_count: int = int(data.get("daily_life_count", 0) or 0)
        clarifying_question_count: int = int(data.get("clarifying_question_count", 0) or 0)

        # Resolve full card data from MySQL up front — Qdrant's policity_cards
        # collection only embeds `intro`, so the FastAPI side can no longer get
        # full content from there. card_id is a direct PK lookup, not a search,
        # so it belongs here rather than round-tripping through Qdrant.
        card_data: dict[str, Any] | None = None
        if card_id:
            try:
                card_obj = InfoCard.objects.select_related("category").get(pk=int(card_id))
                card_data = _build_card_data(card_obj)
            except (InfoCard.DoesNotExist, TypeError, ValueError):
                logger.warning("card_id %s not found or invalid — sending no card_data", card_id)

        llm_payload = {
            "user_id":           str(user.pk),
            "user_query":        user_query,
            "card_id":           card_id,
            "card_data":         card_data,
            "chat_session_id":   str(session.chat_session_id) if session else "",
            "chat_history":      chat_history,
            "chat_summary":      chat_summary,
            "foul_count":        user.foul_count,
            "daily_life_count":  daily_life_count,
            "clarifying_question_count": clarifying_question_count,
            "request_summary":   request_summary,
            "recent_msg_ids":    recent_msg_ids,
            "user_profile_text": user_profile_text,
            "seen_card_ids":     seen_card_ids,
        }

        def _stream_generator():
            nonlocal session
            collected_chunks: list[str] = []
            meta: dict = {}
            done_data: dict = {}
            recommend_data: dict = {}

            try:
                with httpx.Client(timeout=120.0) as client:
                    with client.stream("POST", CHAT_STREAM_URL, json=llm_payload) as resp:
                        resp.raise_for_status()
                        for line in resp.iter_lines():
                            if not line.startswith("data: "):
                                continue
                            raw = line[6:]
                            try:
                                event = json.loads(raw)
                            except json.JSONDecodeError:
                                continue

                            etype = event.get("type")

                            if etype == "meta":
                                meta = event
                                # Forward meta with session_id resolved on Django side
                                out = dict(event)
                                if session:
                                    out["chat_session_id"] = session.chat_session_id
                                yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"

                            elif etype == "chunk":
                                collected_chunks.append(event.get("text", ""))
                                yield f"data: {raw}\n\n"

                            elif etype == "recommend":
                                recommend_data = event
                                yield f"data: {raw}\n\n"

                            elif etype == "done":
                                done_data = event
                                # DB writes happen here; _persist_chat returns the session
                                session, reco_chat_msg_card_map, mid_reco_card_map = _persist_chat(
                                    user=user,
                                    session=session,
                                    session_id=session_id,
                                    user_query=user_query,
                                    card_id=card_id,
                                    routing=meta.get("routing", "card_inquiry"),
                                    answer="".join(collected_chunks) or recommend_data.get("message", ""),
                                    recommendations=recommend_data.get("recommendations") or [],
                                    new_summary=done_data.get("new_summary", ""),
                                    resp_recent_msg_ids=done_data.get("recent_msg_ids") or [],
                                    resp_foul_count=done_data.get("foul_count"),
                                    reco_card_ids=reco_card_ids,
                                    reco_message=reco_message,
                                    selected_reco_card_id=selected_reco_card_id,
                                    latest_summary_obj=latest_summary_obj,
                                )
                                out = dict(done_data)
                                out.pop("chat_history", None)  # internal-only; Django rebuilds history from DB
                                out["chat_session_id"] = session.chat_session_id
                                if reco_chat_msg_card_map:
                                    out["reco_card_map"] = reco_chat_msg_card_map
                                if mid_reco_card_map:
                                    out["chat_msg_card_map"] = mid_reco_card_map
                                yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"

                            elif etype == "error":
                                # Profanity / bias block from FastAPI
                                foul = done_data.get("foul_count") or event.get("foul_count")
                                if foul is not None:
                                    user.__class__.objects.filter(pk=user.pk).update(foul_count=foul)
                                    user.foul_count = foul
                                else:
                                    user.__class__.objects.filter(pk=user.pk).update(
                                        foul_count=models.F("foul_count") + 1
                                    )
                                    user.refresh_from_db(fields=["foul_count"])
                                out = dict(event)
                                out["foul_count"] = user.foul_count
                                yield f"data: {json.dumps(out, ensure_ascii=False)}\n\n"

            except httpx.HTTPStatusError as exc:
                logger.error("FastAPI stream error: %s", exc)
                yield f"data: {json.dumps({'type': 'error', 'detail': 'LLM server error.'}, ensure_ascii=False)}\n\n"
            except httpx.RequestError as exc:
                logger.error("FastAPI stream unreachable: %s", exc)
                yield f"data: {json.dumps({'type': 'error', 'detail': 'LLM server unreachable.'}, ensure_ascii=False)}\n\n"

        response = StreamingHttpResponse(_stream_generator(), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # disable nginx buffering
        return response


def _persist_chat(
    *,
    user,
    session,
    session_id,
    user_query: str,
    card_id,
    routing: str,
    answer: str,
    recommendations: list,
    new_summary: str,
    resp_recent_msg_ids: list,
    resp_foul_count,
    reco_card_ids: list,
    reco_message: str,
    selected_reco_card_id: str,
    latest_summary_obj,
) -> tuple["ChatSession", dict, dict]:
    """Write ChatSession / ChatMessage / ChatMsgCard / ChatSummary to MySQL.
    Returns (session, reco_chat_msg_card_map, mid_reco_card_map) where
    reco_chat_msg_card_map is {str(card_id): chat_msg_card_id} for the initial
    welcome reco cards, and mid_reco_card_map is the same for mid-chat recommendations."""
    reco_chat_msg_card_map: dict = {}
    mid_reco_card_map: dict = {}
    with transaction.atomic():
        if session is None:
            session = ChatSession.objects.create(user=user, session_title=user_query[:50])

        if not session_id and reco_card_ids:
            try:
                reco_ids_int = [int(cid) for cid in reco_card_ids]
            except (TypeError, ValueError):
                reco_ids_int = []
            if reco_ids_int:
                reco_cards_qs = InfoCard.objects.filter(pk__in=reco_ids_int)
                reco_card_map = {c.pk: c for c in reco_cards_qs}
                initial_msg = ChatMessage.objects.create(
                    chat_session=session, input="", output=reco_message, is_memory=0
                )
                ChatMsgCard.objects.bulk_create([
                    ChatMsgCard(
                        chat_message=initial_msg,
                        card=reco_card_map[cid],
                        is_selected=1 if str(cid) == selected_reco_card_id else 0,
                    )
                    for cid in reco_ids_int if cid in reco_card_map
                ])
                reco_chat_msg_card_map = {
                    str(link["card_id"]): link["chat_msg_card_id"]
                    for link in ChatMsgCard.objects.filter(chat_message=initial_msg)
                    .values("card_id", "chat_msg_card_id")
                }

        # See ChatView.post — new messages always start unsummarized.
        msg = ChatMessage.objects.create(
            chat_session=session, input=user_query, output=answer, is_memory=0
        )

        if card_id:
            try:
                discussed_card = InfoCard.objects.get(pk=int(card_id))
                ChatMsgCard.objects.create(chat_message=msg, card=discussed_card, is_selected=1)
            except (InfoCard.DoesNotExist, TypeError, ValueError):
                logger.warning("card_id %s not found or invalid", card_id)

        if recommendations:
            card_ids_int = []
            for raw in recommendations:
                try:
                    card_ids_int.append(int(raw))
                except (TypeError, ValueError):
                    pass
            cards_qs = InfoCard.objects.filter(pk__in=card_ids_int)
            card_map = {c.pk: c for c in cards_qs}
            ChatMsgCard.objects.bulk_create([
                ChatMsgCard(chat_message=msg, card=card_map[cid], is_selected=0)
                for cid in card_ids_int if cid in card_map
            ])
            mid_reco_card_map = {
                str(link["card_id"]): link["chat_msg_card_id"]
                for link in ChatMsgCard.objects.filter(chat_message=msg)
                .values("card_id", "chat_msg_card_id")
            }

        if new_summary:
            ChatSummary.objects.create(chat_session=session, chat_summary=new_summary, is_memory=0)
            if latest_summary_obj:
                latest_summary_obj.is_memory = 1
                latest_summary_obj.save(update_fields=["is_memory"])
            if resp_recent_msg_ids:
                ChatMessage.objects.filter(
                    chat_msg_id__in=resp_recent_msg_ids, chat_session=session
                ).update(is_memory=1)

        if resp_foul_count is not None:
            user.__class__.objects.filter(pk=user.pk).update(foul_count=resp_foul_count)
            user.foul_count = resp_foul_count

        session.save(update_fields=["updated_at"])

    return session, reco_chat_msg_card_map, mid_reco_card_map