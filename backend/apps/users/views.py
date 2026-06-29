# =============================================================================
# apps/users/views.py
# users + cards + debates + chatbots views 통합
# =============================================================================

# ── 공통 imports ──────────────────────────────────────────────────────────────
import json
import logging
import os
from typing import Any
from email.mime.image import MIMEImage

import httpx
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from dotenv import load_dotenv
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken

from apps.cards.models import InfoCard, Bookmark, RawData
from apps.cards.serializers import (
    InfoCardListSerializer,
    InfoCardDetailSerializer,
    BookmarkSerializer,
)
from .models import User, Region, Category, UserInterest
from .serializers import UserSerializer, RegionSerializer, CategorySerializer, UserInterestSerializer

load_dotenv()

logger = logging.getLogger(__name__)


# =============================================================================
# USERS
# =============================================================================

# ── Regions ───────────────────────────────────────────────────────────────────

class RegionListCreateView(generics.ListCreateAPIView):
    queryset         = Region.objects.all()
    serializer_class = RegionSerializer

    def create(self, request, *args, **kwargs):
        sido    = request.data.get('sido', '')
        sigungu = request.data.get('sigungu', '')
        region, _ = Region.objects.get_or_create(sido=sido, sigungu=sigungu)
        serializer = self.get_serializer(region)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class RegionDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset         = Region.objects.all()
    serializer_class = RegionSerializer
    lookup_field     = "region_id"


# ── Categories ────────────────────────────────────────────────────────────────

class CategoryListCreateView(generics.ListCreateAPIView):
    queryset         = Category.objects.all()
    serializer_class = CategorySerializer


class CategoryDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset         = Category.objects.all()
    serializer_class = CategorySerializer
    lookup_field     = "category_id"


# ── Users ─────────────────────────────────────────────────────────────────────

class UserListCreateView(generics.ListCreateAPIView):
    queryset         = User.objects.filter(deleted_at__isnull=True)
    serializer_class = UserSerializer


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset         = User.objects.all()
    serializer_class = UserSerializer
    lookup_field     = "user_id"

    def destroy(self, request, *args, **kwargs):
        from django.utils import timezone
        user = self.get_object()
        user.deleted_at = timezone.now()
        user.save()
        return Response({"detail": "User soft-deleted."}, status=status.HTTP_200_OK)


class ChangePasswordView(APIView):
    def post(self, request, user_id):
        user = get_object_or_404(User, user_id=user_id)
        current_pw = request.data.get("current_password", "")
        new_pw     = request.data.get("new_password", "")

        if not current_pw or not new_pw:
            return Response(
                {"detail": "현재 비밀번호와 새 비밀번호를 모두 입력해주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not check_password(current_pw, user.password):
            return Response(
                {"detail": "현재 비밀번호가 일치하지 않습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pw_pattern = r"^(?=.*[a-zA-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]).{8,16}$"
        if not re.match(pw_pattern, new_pw):
            return Response(
                {"detail": "새 비밀번호는 영문 대소문자, 숫자, 특수문자를 포함한 8~16자로 입력해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.password = make_password(new_pw)
        user.save(update_fields=["password"])
        return Response({"detail": "비밀번호가 변경되었습니다."}, status=status.HTTP_200_OK)


class WithdrawView(APIView):
    def post(self, request, user_id):
        from django.utils import timezone
        user = get_object_or_404(User, user_id=user_id)
        current_pw = request.data.get("current_password", "")

        if not current_pw:
            return Response(
                {"detail": "현재 비밀번호를 입력해주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not check_password(current_pw, user.password):
            return Response(
                {"detail": "비밀번호가 일치하지 않습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.deleted_at = timezone.now()
        user.save(update_fields=["deleted_at"])
        return Response({"detail": "탈퇴 처리되었습니다."}, status=status.HTTP_200_OK)


# ── UserInterests ─────────────────────────────────────────────────────────────

class UserInterestListCreateView(generics.ListCreateAPIView):
    serializer_class = UserInterestSerializer

    def get_queryset(self):
        return UserInterest.objects.filter(user_id=self.kwargs["user_id"])


class UserInterestDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset         = UserInterest.objects.all()
    serializer_class = UserInterestSerializer
    lookup_field     = "interest_id"


# =============================================================================
# CARDS
# =============================================================================

class InfoCardListView(generics.ListAPIView):
    serializer_class = InfoCardListSerializer

    def get_queryset(self):
        qs        = InfoCard.objects.select_related("category").all()
        card_type = self.request.query_params.get("type")
        category  = self.request.query_params.get("category_id")
        if card_type:
            qs = qs.filter(type=card_type)
        if category:
            qs = qs.filter(category_id=category)
        return qs.order_by("-created_at")


class InfoCardDetailView(generics.RetrieveAPIView):
    queryset         = InfoCard.objects.select_related("category")
    serializer_class = InfoCardDetailSerializer
    lookup_field     = "card_id"


class InfoCardBulkListView(generics.ListAPIView):
    serializer_class = InfoCardListSerializer

    def get_queryset(self):
        ids_param = self.request.query_params.get("ids", "")
        try:
            ids = [int(i) for i in ids_param.split(",") if i.strip()]
        except ValueError:
            ids = []
        return InfoCard.objects.select_related("category").filter(
            card_id__in=ids
        ).order_by("-created_at")


class BookmarkListCreateView(generics.ListCreateAPIView):
    serializer_class = BookmarkSerializer

    def get_queryset(self):
        return Bookmark.objects.filter(
            user_id=self.kwargs["user_id"]
        ).select_related("card__category")

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError:
            return Response({"detail": "Bookmark already exists."},
                            status=status.HTTP_409_CONFLICT)


class BookmarkDeleteView(generics.DestroyAPIView):
    queryset     = Bookmark.objects.all()
    lookup_field = "pk"

    def get_object(self):
        return get_object_or_404(
            Bookmark,
            user_id=self.kwargs["user_id"],
            card_id=self.kwargs["card_id"],
        )


BUS_INFO = {
    'jobs':      {'num': 1, 'name': '일자리',  'color': '#1e5c32', 'img': 'bus_half_1_transparent.png'},
    'housing':   {'num': 2, 'name': '주거',    'color': '#0d3f8a', 'img': 'bus_half_2_transparent.png'},
    'education': {'num': 3, 'name': '교육',    'color': '#94521f', 'img': 'bus_half_3_transparent.png'},
    'culture':   {'num': 4, 'name': '문화',    'color': '#561f63', 'img': 'bus_half_4_transparent.png'},
    'welfare':   {'num': 5, 'name': '생활복지', 'color': '#00635a', 'img': 'bus_half_5_transparent.png'},
    'finance':   {'num': 6, 'name': '금융',    'color': '#937220', 'img': 'bus_half_6_transparent.png'},
}


def bus_detail_view(request):
    category = request.GET.get('category', 'jobs')
    bus = BUS_INFO.get(category, BUS_INFO['jobs'])
    context = {
        'category':      category,
        'bus_num':       bus['num'],
        'category_name': bus['name'],
        'bus_color':     bus['color'],
        'bus_img':       bus['img'],
        'bus_stops': [
            {'num': v['num'], 'name': v['name'], 'color': v['color'], 'category': k}
            for k, v in BUS_INFO.items()
        ],
    }
    return render(request, 'buses/bus_detail.html', context)


# =============================================================================
# CHATBOT — moved to apps/chatbot/views.py
# =============================================================================

# from apps.chatbot.models import ChatMessage, ChatMsgCard, ChatSession, ChatSummary

# LLM_SERVER_URL = os.getenv("LLM_SERVER_URL")
# LLM_TIMEOUT: float = getattr(settings, "LLM_TIMEOUT", 30.0)


# def _post_llm(path: str, payload: dict) -> dict[str, Any]:
#     url = f"{LLM_SERVER_URL}{path}"
#     with httpx.Client(timeout=LLM_TIMEOUT) as client:
#         resp = client.post(url, json=payload)
#     resp.raise_for_status()
#     return resp.json()


# def _resolve_service_user(auth_user) -> User:
#     try:
#         return User.objects.get(email=auth_user.email)
#     except User.DoesNotExist:
#         raise ValueError(f"No service user found for email {auth_user.email}")


# @login_required
# def chatbot_test(request):
#     user = request.user
#     jwt_access_token = str(AccessToken.for_user(user))
#     return render(request, "chatbot_test.html", {"jwt_access": jwt_access_token})


# class RecommendationsView(APIView):
#     permission_classes = [IsAuthenticated]

#     def post(self, request: Request) -> Response:
#         try:
#             user = _resolve_service_user(request.user)
#         except ValueError as exc:
#             return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

#         category_names: list[str] = list(
#             UserInterest.objects.filter(user=user)
#             .values_list("category__category_name", flat=True)
#         )
#         if not category_names:
#             return Response(
#                 {"detail": "No interests found for this user."},
#                 status=status.HTTP_400_BAD_REQUEST,
#             )

#         last_20_msgs: str | None = request.data.get("last_20_msgs")

#         try:
#             llm_resp = _post_llm(
#                 "/chat/recommendations",
#                 {
#                     "user_id":          user.pk,
#                     "target_categories": category_names,
#                     "last_20_msgs":     last_20_msgs,
#                 },
#             )
#         except httpx.HTTPStatusError as exc:
#             logger.error("FastAPI /recommendations error: %s", exc.response.text)
#             return Response(
#                 {"detail": "LLM server error.", "llm_detail": exc.response.json()},
#                 status=status.HTTP_502_BAD_GATEWAY,
#             )
#         except httpx.RequestError as exc:
#             logger.error("FastAPI /recommendations unreachable: %s", exc)
#             return Response({"detail": "LLM server unreachable."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

#         card_ids: list[int] = []
#         for hit in llm_resp.get("results", []):
#             payload = hit.get("payload") or {}
#             raw_card_id = payload.get("card_id")
#             if raw_card_id is not None:
#                 try:
#                     card_ids.append(int(raw_card_id))
#                 except (TypeError, ValueError):
#                     logger.warning("Non-integer card_id in Qdrant payload: %s", raw_card_id)

#         cards_qs = InfoCard.objects.filter(pk__in=card_ids)
#         card_map = {c.pk: c for c in cards_qs}
#         ordered_cards = [card_map[cid] for cid in card_ids if cid in card_map]

#         with transaction.atomic():
#             session = ChatSession.objects.create(
#                 user=user,
#                 session_title=f"추천 카드 ({', '.join(category_names[:2])})",
#             )
#             msg = ChatMessage.objects.create(
#                 chat_session=session,
#                 input="",
#                 output=", ".join(str(c.pk) for c in ordered_cards),
#                 is_memory=0,
#             )
#             ChatMsgCard.objects.bulk_create([
#                 ChatMsgCard(chat_message=msg, card=card, is_selected=0)
#                 for card in ordered_cards
#             ])

#         return Response(
#             {
#                 "chat_session_id": session.chat_session_id,
#                 "cards": [
#                     {"card_id": card.pk, "title": getattr(card, "title", str(card))}
#                     for card in ordered_cards
#                 ],
#             },
#             status=status.HTTP_201_CREATED,
#         )


# class ChatView(APIView):
#     permission_classes = [IsAuthenticated]

#     def post(self, request: Request) -> Response:
#         try:
#             user = _resolve_service_user(request.user)
#         except ValueError as exc:
#             return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

#         data        = request.data
#         user_query: str        = data.get("user_query", "").strip()
#         card_id: str | None    = data.get("card_id")
#         session_id: int | None = data.get("chat_session_id")

#         if not user_query:
#             return Response({"detail": "user_query is required."}, status=status.HTTP_400_BAD_REQUEST)

#         if session_id:
#             try:
#                 session = ChatSession.objects.get(chat_session_id=session_id, user=user, is_delete=0)
#             except ChatSession.DoesNotExist:
#                 return Response({"detail": "Chat session not found."}, status=status.HTTP_404_NOT_FOUND)
#         else:
#             session = ChatSession.objects.create(user=user, session_title=user_query[:50])

#         recent_msgs = ChatMessage.objects.filter(chat_session=session).order_by("-created_at")[:20]
#         chat_history: list[dict] = []
#         for msg in reversed(recent_msgs):
#             if msg.input:
#                 chat_history.append({"role": "user",      "content": msg.input})
#             if msg.output:
#                 chat_history.append({"role": "assistant", "content": msg.output})

#         latest_summary_obj = (
#             ChatSummary.objects.filter(chat_session=session).order_by("-created_at").first()
#         )
#         chat_summary: str = latest_summary_obj.chat_summary if latest_summary_obj else ""
#         daily_life_count: int = ChatMessage.objects.filter(chat_session=session, is_memory=0).count()

#         llm_payload = {
#             "user_id":                       str(user.pk),
#             "user_query":                    user_query,
#             "card_id":                       card_id,
#             "chat_session_id":               str(session.chat_session_id),
#             "chat_history":                  chat_history,
#             "chat_summary":                  chat_summary,
#             "daily_life_conversation_count": daily_life_count,
#         }

#         try:
#             llm_resp = _post_llm("/chat/message", llm_payload)
#         except httpx.HTTPStatusError as exc:
#             logger.error("FastAPI /chat error: %s", exc.response.text)
#             return Response(
#                 {"detail": "LLM server error.", "llm_detail": exc.response.json()},
#                 status=status.HTTP_502_BAD_GATEWAY,
#             )
#         except httpx.RequestError as exc:
#             logger.error("FastAPI /chat unreachable: %s", exc)
#             return Response({"detail": "LLM server unreachable."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

#         if llm_resp.get("error") == "profanity_detected":
#             return Response(
#                 {
#                     "error":    "profanity_detected",
#                     "reason":   llm_resp.get("reason"),
#                     "censored": llm_resp.get("censored"),
#                 },
#                 status=status.HTTP_422_UNPROCESSABLE_ENTITY,
#             )

#         routing         = llm_resp.get("routing", "card_inquiry")
#         answer          = llm_resp.get("answer") or ""
#         recommendations = llm_resp.get("recommendations") or []
#         was_compressed  = llm_resp.get("history_was_compressed", False)
#         new_summary     = llm_resp.get("chat_summary", "")

#         with transaction.atomic():
#             is_memory = 1 if routing == "card_inquiry" else 0
#             msg = ChatMessage.objects.create(
#                 chat_session=session, input=user_query, output=answer, is_memory=is_memory,
#             )

#             if recommendations:
#                 card_ids_int = []
#                 for raw in recommendations:
#                     try:
#                         card_ids_int.append(int(raw))
#                     except (TypeError, ValueError):
#                         pass
#                 cards_qs = InfoCard.objects.filter(pk__in=card_ids_int)
#                 card_map = {c.pk: c for c in cards_qs}
#                 ChatMsgCard.objects.bulk_create([
#                     ChatMsgCard(chat_message=msg, card=card_map[cid], is_selected=0)
#                     for cid in card_ids_int if cid in card_map
#                 ])

#             if was_compressed and new_summary:
#                 ChatSummary.objects.create(chat_session=session, chat_summary=new_summary, is_memory=0)
#                 if latest_summary_obj:
#                     latest_summary_obj.is_memory = 1
#                     latest_summary_obj.save(update_fields=["is_memory"])

#             session.save(update_fields=["updated_at"])

#         response_body: dict[str, Any] = {
#             "chat_session_id": session.chat_session_id,
#             "chat_msg_id":     msg.chat_msg_id,
#             "routing":         routing,
#         }
#         if routing in ("daily_life", "card_inquiry"):
#             response_body["answer"] = answer
#         if routing == "recommend" and recommendations:
#             response_body["recommendations"] = [int(r) for r in recommendations if str(r).isdigit()]

#         return Response(response_body, status=status.HTTP_200_OK)


# class ChatSessionListView(generics.ListAPIView):
#     permission_classes = [IsAuthenticated]

#     def get_queryset(self):
#         try:
#             user = _resolve_service_user(self.request.user)
#         except ValueError:
#             return ChatSession.objects.none()
#         return ChatSession.objects.filter(user=user, is_delete=0).order_by("-updated_at")

#     def list(self, request, *args, **kwargs):
#         qs = self.get_queryset()
#         data = [
#             {
#                 "chat_session_id": s.chat_session_id,
#                 "session_title":   s.session_title,
#                 "updated_at":      s.updated_at,
#             }
#             for s in qs
#         ]
#         return Response(data)


# class ChatSessionDeleteView(APIView):
#     permission_classes = [IsAuthenticated]

#     def delete(self, request, session_id):
#         try:
#             user = _resolve_service_user(request.user)
#         except ValueError as exc:
#             return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

#         try:
#             session = ChatSession.objects.get(chat_session_id=session_id, user=user)
#         except ChatSession.DoesNotExist:
#             return Response({"detail": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

#         session.is_delete = 1
#         session.save(update_fields=["is_delete"])
#         return Response({"detail": "삭제되었습니다."}, status=status.HTTP_200_OK)


# =============================================================================
# DEBATES
# =============================================================================

from apps.debates.models import DebateMessage, DebateSession
from apps.debates.serializers import (
    DebateStartRequestSerializer,
    MessageCallbackSerializer,
    UserActionSerializer,
    UserInputSerializer,
)

AI_AGENT_URL = os.getenv("AI_AGENT_URL", "http://localhost:8001")

STAGE_TO_ROUND = {
    "position":  "1",
    "pro_round": "2",
    "con_round": "3",
    "summary":   "4",
    "done":      "4",
}

PARTICIPANT_TO_ROLE = {
    "pro":    "AI",
    "con":    "AI",
    "user":   "USER",
    "system": "AI",
}


def _ai_agent_msg_type(participant: str, msg_type: str) -> str:
    if msg_type == "question_ans":
        return "ANSWER"
    if participant == "user":
        return "QUESTION"
    if participant == "pro":
        return "PRO"
    if participant == "con":
        return "CON"
    return "PRO"


def _json_body(request):
    try:
        return json.loads(request.body)
    except Exception:
        return {}


@method_decorator(csrf_exempt, name="dispatch")
class DebateStartView(View):
    def post(self, request):
        data = _json_body(request)
        serializer = DebateStartRequestSerializer(data=data)
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)

        vd = serializer.validated_data
        try:
            card = InfoCard.objects.only("card_id", "card_title", "summary", "core_content").get(card_id=vd["card_id"])
        except InfoCard.DoesNotExist:
            return JsonResponse({"error": "카드를 찾을 수 없습니다."}, status=404)

        user_id = data.get("user_id", 1)
        try:
            user = User.objects.get(user_id=user_id)
        except User.DoesNotExist:
            return JsonResponse({"error": "사용자를 찾을 수 없습니다."}, status=404)

        session = DebateSession.objects.create(card=card, user=user, current_round="1")

        policy_card = {
            "id":             card.card_id,
            "title":          card.card_title,
            "summary_points": [card.summary],
            "background":     card.core_content,
        }
        payload = {
            "action":      "create",
            "policy_card": policy_card,
            "mode":        vd["mode"],
            "difficulty":  vd.get("difficulty", "hard"),
            "user_stance": vd.get("user_stance"),
        }

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(f"{AI_AGENT_URL}/debate/sessions/{session.debate_session_id}", json=payload)
                resp.raise_for_status()
        except httpx.HTTPError as e:
            session.delete()
            logger.error(f"ai_agent 세션 생성 실패: {e}")
            return JsonResponse({"error": "AI 서버 연결 실패"}, status=503)

        return JsonResponse({"debate_session_id": session.debate_session_id}, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class DebateDetailView(View):
    def get(self, request, debate_session_id):
        try:
            session = DebateSession.objects.get(debate_session_id=debate_session_id)
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)

        messages = list(session.messages.values(
            "debate_msg_id", "role", "content", "message_type", "created_at"
        ))
        return JsonResponse({
            "debate_session_id": session.debate_session_id,
            "card_id":           session.card_id,
            "user_id":           session.user_id,
            "current_round":     session.current_round,
            "messages":          messages,
        })


@method_decorator(csrf_exempt, name="dispatch")
class DebateStreamView(View):
    def get(self, request, debate_session_id):
        try:
            session = DebateSession.objects.get(debate_session_id=debate_session_id)
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)

        def event_generator():
            try:
                with httpx.Client(timeout=300) as client:
                    with client.stream("GET", f"{AI_AGENT_URL}/debate/sessions/{debate_session_id}/stream") as ai_resp:
                        for line in ai_resp.iter_lines():
                            if not line:
                                yield "\n"
                                continue
                            if line.startswith("data:"):
                                raw = line[5:].strip()
                                try:
                                    event_data = json.loads(raw)
                                    event_type = event_data.get("type", "")
                                    if event_type == "message":
                                        participant  = event_data.get("participant", "")
                                        msg_type_raw = event_data.get("msg_type", "")
                                        content      = event_data.get("content", "")
                                        if content:
                                            DebateMessage.objects.create(
                                                debate_session=session,
                                                role=PARTICIPANT_TO_ROLE.get(participant, "AI"),
                                                content=content,
                                                message_type=_ai_agent_msg_type(participant, msg_type_raw),
                                            )
                                    elif event_type == "round_update":
                                        new_round = STAGE_TO_ROUND.get(event_data.get("stage", ""), session.current_round)
                                        if new_round != session.current_round:
                                            session.current_round = new_round
                                            session.save(update_fields=["current_round"])
                                except json.JSONDecodeError:
                                    pass
                                yield f"data: {raw}\n\n"
                            else:
                                yield f"{line}\n"
            except httpx.HTTPError as e:
                logger.error(f"SSE relay 오류 (session={debate_session_id}): {e}")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        response = StreamingHttpResponse(event_generator(), content_type="text/event-stream")
        response["Cache-Control"]               = "no-cache"
        response["X-Accel-Buffering"]           = "no"
        response["Access-Control-Allow-Origin"] = "*"
        return response


@method_decorator(csrf_exempt, name="dispatch")
class DebateInputView(View):
    def post(self, request, debate_session_id):
        serializer = UserInputSerializer(data=_json_body(request))
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)

        try:
            DebateSession.objects.get(debate_session_id=debate_session_id)
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{AI_AGENT_URL}/debate/sessions/{debate_session_id}",
                    json={"action": "input", "user_input": serializer.validated_data["user_input"]},
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPError as e:
            logger.error(f"ai_agent input 전달 실패: {e}")
            return JsonResponse({"error": "AI 서버 연결 실패"}, status=503)

        return JsonResponse(result)


@method_decorator(csrf_exempt, name="dispatch")
class DebateActionView(View):
    def post(self, request, debate_session_id):
        serializer = UserActionSerializer(data=_json_body(request))
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)

        try:
            DebateSession.objects.get(debate_session_id=debate_session_id)
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)

        vd = serializer.validated_data
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{AI_AGENT_URL}/debate/sessions/{debate_session_id}",
                    json={"action": "choice", "user_action": vd["user_action"], "question_target": vd.get("question_target")},
                )
                resp.raise_for_status()
        except httpx.HTTPError as e:
            logger.error(f"ai_agent action 전달 실패: {e}")
            return JsonResponse({"error": "AI 서버 연결 실패"}, status=503)

        return JsonResponse({"ok": True})


@method_decorator(csrf_exempt, name="dispatch")
class DebateQuestionView(View):
    def post(self, request, debate_session_id):
        data = _json_body(request)
        try:
            session = DebateSession.objects.get(debate_session_id=debate_session_id)
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)

        try:
            with httpx.Client(timeout=120) as client:
                resp = client.post(
                    f"{AI_AGENT_URL}/debate/sessions/{debate_session_id}",
                    json={
                        "action":          "question",
                        "user_input":      data.get("user_input", ""),
                        "question_target": data.get("question_target", "pro"),
                    },
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPError as e:
            logger.error(f"ai_agent question 전달 실패: {e}")
            return JsonResponse({"error": "AI 서버 연결 실패"}, status=503)

        DebateMessage.objects.create(
            debate_session=session, role="USER",
            content=data.get("user_input", ""), message_type="QUESTION",
        )
        DebateMessage.objects.create(
            debate_session=session, role="AI",
            content=result.get("content", ""),
            message_type="PRO" if result.get("participant") == "pro" else "CON",
        )
        return JsonResponse(result)


@method_decorator(csrf_exempt, name="dispatch")
class DebateMessageCallbackView(View):
    def post(self, request, debate_session_id):
        serializer = MessageCallbackSerializer(data=_json_body(request))
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)

        try:
            session = DebateSession.objects.get(debate_session_id=debate_session_id)
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)

        vd = serializer.validated_data
        DebateMessage.objects.create(
            debate_session=session,
            role=vd["role"],
            content=vd["content"],
            message_type=vd["message_type"],
        )
        return JsonResponse({"ok": True}, status=201)


@method_decorator(csrf_exempt, name="dispatch")
class DebateHistoryView(View):
    def get(self, request):
        user_id = request.GET.get("user_id")
        if not user_id:
            return JsonResponse({"error": "user_id required"}, status=400)
        sessions = DebateSession.objects.filter(user_id=user_id).order_by("-created_at")
        return JsonResponse({
            "sessions": list(sessions.values("debate_session_id", "card_id", "current_round", "created_at"))
        })


@method_decorator(csrf_exempt, name="dispatch")
class DebateSessionDeleteView(View):
    def delete(self, request, debate_session_id):
        try:
            session = DebateSession.objects.get(debate_session_id=debate_session_id)
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)
        session.delete()
        return JsonResponse({"ok": True})


def test_page(request):
    return render(request, "debates/test.html")

# ── 이하 추가된 코드 ──────────────────────────────────────────────────────────

import random
import re
import string

from django.core.cache import cache
from django.core.mail import send_mail
from django.utils import timezone
from django.contrib.auth.hashers import make_password
from rest_framework.permissions import AllowAny

# ── 금칙어 목록 ────────────────────────────────────────────────────────────────
FORBIDDEN_WORDS = [
    # [범주 1] 성적 비하 및 범용 비속어
    "씨발", "씨팔", "씨빨", "씨바", "시발", "ㅅㅂ", "ㅆㅂ",
    "개새끼", "개새", "개색기", "ㄱㅅㄲ",
    "병신", "ㅂㅅ", "븅신", "벙신",
    "느금마", "니애미", "엠창",
    "창녀", "창년", "갈보", "보지", "자지",
    "씹", "좆", "조까", "지랄", "개지랄",
    "미친놈", "미친년", "미친새끼",

    # [범주 2] 특정 정치인 비하
    "찢재명", "이재앙", "쥐명", "드럼통",
    "굥", "윤틀러", "윤가", "멍윤",
    "닭그네", "닭근혜",
    "문재앙", "달창", "달빠",
    "쥐박", "쥐박이",
    "놈현", "고무현", "운지",
    "한가발", "한뚜껑",

    # [범주 3] 특정 정당 비하
    "국짐", "국짐당", "국개당",
    "만주당", "더불어만진당", "더듬어만진당",

    # [범주 4] 인종/성별/지역/종교 혐오
    "짱깨", "쪽바리",
    "홍어", "홍어충",
    "절라디언", "절라도",
    "과메기", "개쌍도", "경상도개",
    "서울충", "수도충",
    "틀딱", "틀딱충",
    "좌빨", "빨갱이", "종북",
    "일베", "일베충",
    "대깨문", "개딸", "명빠",
]

_NICKNAME_SUFFIX_PATTERNS = [
    r"[가-힣a-zA-Z]{1,6}충",
    r"[가-힣a-zA-Z]{1,4}새끼",
    r"[가-힣]{1,4}년",
    r"[가-힣a-zA-Z]{1,4}놈",
]

_NICKNAME_SUFFIX_WHITELIST = [
    "청년", "소년", "노년", "중년", "장년", "유년",
    "충분", "충성", "충고", "충남", "충북", "충청", "보충",
]

def _contains_forbidden(word: str) -> bool:
    text = word.lower()
    if any(fw in text for fw in FORBIDDEN_WORDS):
        return True
    for pat in _NICKNAME_SUFFIX_PATTERNS:
        for m in re.finditer(pat, word):
            matched = m.group()
            if not any(w in matched or matched in w for w in _NICKNAME_SUFFIX_WHITELIST):
                return True
    return False

def _send_code_cache_key(email): return f"email_code:{email}"
def _send_count_cache_key(email): return f"email_send_count:{email}"
def _send_cooldown_cache_key(email): return f"email_send_cooldown:{email}"
def _verified_cache_key(email): return f"email_verified:{email}"


# ── 이메일 인증코드 발송 ───────────────────────────────────────────────────────

from functools import lru_cache
from io import BytesIO

from django.contrib.staticfiles import finders
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from PIL import Image

EMAIL_LOGO_SIZE = 56  # 이메일 본문에 인라인으로 들어가는 표시 크기(px)


@lru_cache(maxsize=1)
def _get_email_logo_bytes() -> bytes:
    """
    static/assets/poli_profile.png(고화질 원본, ~1.5MB)을 이메일 인라인용
    56px PNG로 리사이즈한 바이트를 반환한다.
    원본 파일은 서버 실행 중 바뀌지 않으므로 최초 1회만 리사이즈하고 메모리에 캐싱한다.
    """
    source_path = finders.find("assets/poli_profile.png")
    with Image.open(source_path) as img:
        img = img.convert("RGBA")
        img.thumbnail((EMAIL_LOGO_SIZE, EMAIL_LOGO_SIZE), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def build_verification_email_html(title: str, code: str) -> str:
    return render_to_string("emails/verification_code.html", {
        "title": title,
        "code": code,
    })


def send_verification_email(*, subject: str, to_email: str, title: str, code: str) -> None:
    plain_text = f"인증 코드: {code}\n\n유효 시간은 3분입니다."
    html_body  = build_verification_email_html(title, code)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=plain_text,
        from_email="PoliCity <policity.biz@gmail.com>",
        to=[to_email],
    )
    msg.attach_alternative(html_body, "text/html")
    msg.mixed_subtype = "related"

    image = MIMEImage(_get_email_logo_bytes(), _subtype="png")
    image.add_header("Content-ID", "<poli_logo>")
    image.add_header("Content-Disposition", "inline", filename="poli_logo.png")
    msg.attach(image)

    msg.send(fail_silently=False)

class SendEmailCodeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip()

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return Response({"error": "이메일 형식이 맞지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)

        if User.objects.filter(email=email, deleted_at__isnull=True).exists():
            return Response({"error": "이미 등록된 이메일입니다."}, status=status.HTTP_409_CONFLICT)

        if cache.get(_send_cooldown_cache_key(email)):
            return Response({"error": "인증번호는 30초 후 다시 요청할 수 있습니다."}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        send_count = cache.get(_send_count_cache_key(email), 0)
        if send_count >= 5:
            cache.set(_send_count_cache_key(email), send_count, timeout=86400)
            return Response({"error": "발송 횟수를 초과했습니다. 24시간 후 다시 시도해 주세요."}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        code = "".join(random.choices(string.digits, k=6))

        cache.set(_send_code_cache_key(email), code, timeout=180)
        cache.set(_send_cooldown_cache_key(email), True, timeout=30)
        cache.set(_send_count_cache_key(email), send_count + 1, timeout=86400)

        try:
            send_verification_email(
                subject="[PoliCity] 이메일 인증 코드",
                to_email=email,
                title="PoliCity 이메일 인증",
                code=code,
            )
            
        except Exception:
            logging.getLogger(__name__).warning("[DEV] 이메일 인증코드 (%s): %s", email, code)

        return Response({"message": "인증코드가 발송되었습니다."}, status=status.HTTP_200_OK)


# ── 이메일 인증코드 확인 ───────────────────────────────────────────────────────

class VerifyEmailCodeView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip()
        code  = request.data.get("code", "").strip()

        saved_code = cache.get(_send_code_cache_key(email))

        if saved_code is None:
            return Response({"error": "인증번호가 만료되었습니다. 다시 발송해 주세요."}, status=status.HTTP_400_BAD_REQUEST)

        if saved_code != code:
            return Response({"error": "인증번호를 정확히 입력해 주세요."}, status=status.HTTP_400_BAD_REQUEST)

        cache.set(_verified_cache_key(email), True, timeout=600)
        cache.delete(_send_code_cache_key(email))

        return Response({"message": "이메일 인증에 성공하였습니다."}, status=status.HTTP_200_OK)

# ── 닉네임 금칙어 사전 검사 API (회원가입 blur 시점 호출용) ──────────────────
class NicknameCheckView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        nickname = request.data.get("nickname", "").strip()
        if not nickname:
            return Response({"available": False, "error": "닉네임을 입력해 주세요."})
        if not re.match(r"^[가-힣a-zA-Z0-9]{2,12}$", nickname):
            return Response({"available": False, "error": "닉네임은 한글, 영문 대소문자, 숫자만 입력할 수 있습니다."})
        if _contains_forbidden(nickname):
            return Response({"available": False, "error": "사용할 수 없는 닉네임입니다."})
        return Response({"available": True})

# ── 회원가입 ──────────────────────────────────────────────────────────────────

class SignupView(APIView):
    permission_classes = [AllowAny]

    INTEREST_MAP = {
        "job":       "1",
        "housing":   "3",
        "education": "2",
        "culture":   "6",
        "finance":   "4",
        "welfare":   "5",
    }

    def post(self, request):
        data = request.data

        nickname    = data.get("nickname", "").strip()
        email       = data.get("email", "").strip()
        password    = data.get("password", "")
        pw_confirm  = data.get("password_confirm", "")
        gender      = data.get("gender", "").strip().upper()
        birth_year  = data.get("birth_year")
        birth_month = data.get("birth_month")
        birth_day   = data.get("birth_day")
        interests   = data.get("interests", [])
        sido        = data.get("sido", "").strip()
        sigungu     = data.get("sigungu", "").strip()

        errors = {}

        # 닉네임
        if not nickname:
            errors["nickname"] = "닉네임을 입력해 주세요."
        elif not re.match(r"^[가-힣a-zA-Z0-9]{2,12}$", nickname):
            errors["nickname"] = "닉네임은 한글, 영문 대소문자, 숫자만 입력할 수 있습니다."
        elif _contains_forbidden(nickname):
            errors["nickname"] = "사용할 수 없는 닉네임입니다."

        # 이메일
        if not email:
            errors["email"] = "이메일을 입력해 주세요."
        elif not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            errors["email"] = "이메일 형식이 맞지 않습니다."
        elif User.objects.filter(email=email, deleted_at__isnull=True).exists():
            errors["email"] = "이미 등록된 이메일입니다."
        elif not cache.get(_verified_cache_key(email)):
            errors["email"] = "이메일 인증을 완료해 주세요."

        # 비밀번호
        pw_pattern = r"^(?=.*[a-zA-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]).{8,16}$"
        if not password:
            errors["password"] = "비밀번호를 입력해 주세요."
        elif not re.match(pw_pattern, password):
            errors["password"] = "비밀번호는 영문 대소문자, 숫자, 특수문자를 포함한 8~16자로 입력해 주세요."

        if not pw_confirm:
            errors["password_confirm"] = "비밀번호 확인을 입력해 주세요."
        elif password and password != pw_confirm:
            errors["password_confirm"] = "비밀번호가 일치하지 않습니다."

        # 성별
        if gender not in ("MALE", "FEMALE", "OTHER"):
            errors["gender"] = "성별을 선택해 주세요."

        # 생년월일 → 나이
        age = None
        try:
            birth_year  = int(birth_year)
            birth_month = int(birth_month)
            birth_day   = int(birth_day)

            import calendar  # ↓ 여기 추가
            max_day = calendar.monthrange(birth_year, birth_month)[1]
            if birth_day > max_day:
                errors["birth"] = "올바른 생년월일을 입력해 주세요."
            
            today = timezone.localdate()
            age = today.year - birth_year - (
                (today.month, today.day) < (birth_month, birth_day)
            )
            if not (1 <= age <= 120):
                errors["birth"] = "올바른 생년월일을 입력해 주세요."
        except (TypeError, ValueError):
            errors["birth"] = "올바른 생년월일을 입력해 주세요."

        # 관심사
        if not interests:
            errors["interests"] = "관심사 키워드를 1개 이상 선택해 주세요."
        elif len(interests) > 3:
            errors["interests"] = "관심사 키워드는 최대 3개까지 선택 가능합니다."

        if errors:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        # 거주지 (선택)
        region = None
        if sido and sigungu:
            region, _ = Region.objects.get_or_create(sido=sido, sigungu=sigungu)

        # 사용자 생성
        user = User.objects.create(
            email    = email,
            password = make_password(password),
            nickname = nickname,
            gender   = gender,
            age      = age,
            region   = region,
        )

        # 관심사 저장
        for key in interests:
            category_id = self.INTEREST_MAP.get(key)
            if not category_id:
                continue
            try:
                category = Category.objects.get(category_id=category_id)
                UserInterest.objects.create(user=user, category=category)
            except Category.DoesNotExist:
                pass

        cache.delete(_verified_cache_key(email))

        return Response({"message": "회원가입이 완료되었습니다."}, status=status.HTTP_201_CREATED)

# ── 로그인 ────────────────────────────────────────────────────────────────────
# 아래 코드를 views.py 맨 아래에 추가하세요.

from .serializers import CustomTokenObtainSerializer
from django.contrib.auth.hashers import check_password

class LoginView(APIView):
    """
    POST /member/login/
    body: { "email": "...", "password": "..." }

    - 이메일 존재 여부 확인
    - 비밀번호 일치 여부 확인
    - 실패 시 login_fail_count 누적
    - 5회 실패 시 잠금 (login_fail_count >= 5)
    - 성공 시 login_fail_count 초기화, JWT 반환
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email    = request.data.get("email", "").strip()
        password = request.data.get("password", "")

        # 이메일 존재 확인 (활성 계정 중 가장 최근 row)
        user = (
            User.objects
            .filter(email=email, deleted_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if user is None:
            return Response(
                {"error": "이메일 또는 비밀번호가 일치하지 않습니다.", "fail_count": 0},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # 잠금 상태 확인 (login_fail_count)
        if user.login_fail_count >= 5:
            return Response(
                {"error": "계정이 잠겼습니다. 비밀번호 찾기를 진행해주세요.", "locked": True, "locked_reason": "fail"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # 잠금 상태 확인 (foul_count)
        if user.foul_count >= 3:
            return Response(
                {"error": "혐오·욕설 표현 누적으로 계정이 제한되었습니다.\npolicity.biz@gmail.com로 문의해주세요.", "locked": True, "locked_reason": "foul"},
                status=status.HTTP_403_FORBIDDEN,
            )

        # 비밀번호 확인
        if not check_password(password, user.password):
            user.login_fail_count += 1
            user.save(update_fields=["login_fail_count"])
            return Response(
                {
                    "error": "이메일 또는 비밀번호가 일치하지 않습니다.",
                    "fail_count": user.login_fail_count,
                    "locked": user.login_fail_count >= 5,
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # 로그인 성공 — 실패 횟수 초기화
        user.login_fail_count = 0
        user.save(update_fields=["login_fail_count"])

        # JWT 발급
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken()
        refresh["user_id"]  = user.user_id
        refresh["email"]    = user.email
        refresh["nickname"] = user.nickname

        return Response(
            {
                "access":  str(refresh.access_token),
                "refresh": str(refresh),
                "nickname": user.nickname,
            },
            status=status.HTTP_200_OK,
        )

# ── 비밀번호 찾기/재설정 ─────────────────────────────────────────────────────

class SendPasswordFindCodeView(APIView):
    """
    POST /member/password/send-code/
    body: { "email": "..." }

    - 가입된 이메일에만 인증코드 발송
    - 회원가입 인증코드와 별도 캐시 키 사용 (prefix: pw_reset)
    - 30초 쿨다운, 하루 5회 제한 동일 적용
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip()

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return Response({"error": "이메일 형식이 맞지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)

        # 가입된 이메일만 허용
        if not User.objects.filter(email=email, deleted_at__isnull=True).exists():
            return Response({"error": "등록되지 않은 이메일입니다."}, status=status.HTTP_404_NOT_FOUND)

        cooldown_key = f"pw_reset_cooldown:{email}"
        count_key    = f"pw_reset_count:{email}"
        code_key     = f"pw_reset_code:{email}"

        if cache.get(cooldown_key):
            return Response({"error": "인증번호는 30초 후 다시 요청할 수 있습니다."}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        send_count = cache.get(count_key, 0)
        if send_count >= 5:
            cache.set(count_key, send_count, timeout=86400)
            return Response({"error": "발송 횟수를 초과했습니다. 24시간 후 다시 시도해 주세요."}, status=status.HTTP_429_TOO_MANY_REQUESTS)

        code = "".join(random.choices(string.digits, k=6))

        cache.set(code_key,    code, timeout=180)
        cache.set(cooldown_key, True, timeout=30)
        cache.set(count_key, send_count + 1, timeout=86400)

        try:
            send_verification_email(
                subject="[PoliCity] 비밀번호 재설정 인증 코드",
                to_email=email,
                title="PoliCity 비밀번호 재설정",
                code=code,
            )
        except Exception:
            logging.getLogger(__name__).warning("[DEV] 비밀번호 재설정 인증코드 (%s): %s", email, code)

        return Response({"message": "인증코드가 발송되었습니다."}, status=status.HTTP_200_OK)


class VerifyPasswordFindCodeView(APIView):
    """
    POST /member/password/verify-code/
    body: { "email": "...", "code": "123456" }
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email", "").strip()
        code  = request.data.get("code", "").strip()

        code_key     = f"pw_reset_code:{email}"
        verified_key = f"pw_reset_verified:{email}"

        saved_code = cache.get(code_key)

        if saved_code is None:
            return Response({"error": "인증번호가 만료되었습니다. 다시 발송해 주세요."}, status=status.HTTP_400_BAD_REQUEST)

        if saved_code != code:
            return Response({"error": "인증번호를 정확히 입력해 주세요."}, status=status.HTTP_400_BAD_REQUEST)

        cache.set(verified_key, True, timeout=600)
        cache.delete(code_key)

        return Response({"message": "이메일 인증에 성공하였습니다."}, status=status.HTTP_200_OK)


class PasswordResetView(APIView):
    """
    POST /member/password/reset/
    body: { "email": "...", "new_password": "...", "confirm_password": "..." }

    - 비밀번호 찾기 인증 완료 여부 확인
    - 비밀번호 형식 검증
    - 비밀번호 변경 + is_temp_password 초기화 + login_fail_count 초기화
    """
    permission_classes = [AllowAny]

    def post(self, request):
        email      = request.data.get("email", "").strip()
        new_pw     = request.data.get("new_password", "")
        confirm_pw = request.data.get("confirm_password", "")

        verified_key = f"pw_reset_verified:{email}"

        # 인증 완료 여부 확인
        if not cache.get(verified_key):
            return Response({"error": "이메일 인증을 완료해 주세요."}, status=status.HTTP_400_BAD_REQUEST)

        # 비밀번호 형식 검증
        pw_pattern = r"^(?=.*[a-zA-Z])(?=.*\d)(?=.*[!@#$%^&*()_+\-=\[\]{}|;:,.<>?]).{8,16}$"
        if not re.match(pw_pattern, new_pw):
            return Response({"error": "비밀번호는 영문 대소문자, 숫자, 특수문자를 포함한 8~16자로 입력해 주세요."}, status=status.HTTP_400_BAD_REQUEST)

        if new_pw != confirm_pw:
            return Response({"error": "비밀번호가 일치하지 않습니다."}, status=status.HTTP_400_BAD_REQUEST)

        user = (
            User.objects
            .filter(email=email, deleted_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if user is None:
            return Response({"error": "등록되지 않은 이메일입니다."}, status=status.HTTP_404_NOT_FOUND)

        # 새 비밀번호가 기존 비밀번호와 동일한 경우 변경을 허용하지 않는다
        if check_password(new_pw, user.password):
            return Response({"error": "이전에 사용한 비밀번호와 다른 비밀번호를 입력해주세요."}, status=status.HTTP_400_BAD_REQUEST)

        user.password         = make_password(new_pw)
        user.login_fail_count = 0
        user.save(update_fields=["password", "login_fail_count"])

        cache.delete(verified_key)
        return Response({"message": "비밀번호가 재설정되었습니다."}, status=status.HTTP_200_OK)


# ── 채팅 히스토리 (card_type 포함) ────────────────────────────────────────────

class ChatHistoryWithTypeView(APIView):
    """
    GET /member/chat-history/
    채팅 세션 목록 + card_type 반환
    """
    def get(self, request):
        from rest_framework_simplejwt.tokens import AccessToken as AT
        from apps.chatbot.models import ChatSession, ChatMessage, ChatMsgCard

        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        try:
            payload = AT(token)
            user_id = payload['user_id']
        except Exception:
            return Response({'detail': '인증 실패'}, status=status.HTTP_401_UNAUTHORIZED)

        def get_card_type(session):
            msg = ChatMessage.objects.filter(chat_session=session).first()
            if msg:
                card_ref = ChatMsgCard.objects.filter(chat_message=msg).select_related('card').first()
                if card_ref and card_ref.card:
                    return card_ref.card.type
            return 'policy'

        sessions = ChatSession.objects.filter(user_id=user_id, is_delete=0).order_by('-updated_at')
        return Response({
            'sessions': [
                {
                    'chat_session_id': s.chat_session_id,
                    'session_title':   s.session_title,
                    'created_at':      s.created_at,
                    'updated_at':      s.updated_at,
                    'card_type':       get_card_type(s),
                }
                for s in sessions
            ]
        }, status=status.HTTP_200_OK)


class ChatHistoryDetailWithTypeView(APIView):
    """
    GET    /member/chat-history/<chat_session_id>/  → 메시지 목록
    DELETE /member/chat-history/<chat_session_id>/  → 소프트 삭제
    """
    def get(self, request, chat_session_id):
        from rest_framework_simplejwt.tokens import AccessToken as AT
        from apps.chatbot.models import ChatSession, ChatMessage, ChatMsgCard

        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        try:
            payload = AT(token)
            user_id = payload['user_id']
        except Exception:
            return Response({'detail': '인증 실패'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            session = ChatSession.objects.get(chat_session_id=chat_session_id, user_id=user_id, is_delete=0)
        except ChatSession.DoesNotExist:
            return Response({'detail': '세션 없음'}, status=status.HTTP_404_NOT_FOUND)

        messages = ChatMessage.objects.filter(chat_session=session).order_by('created_at')
        msgs_data = []
        for msg in messages:
            cards = ChatMsgCard.objects.filter(chat_message=msg).select_related('card')
            msgs_data.append({
                'input':      msg.input,
                'output':     msg.output,
                'created_at': msg.created_at,
                'cards': [
                    {
                        'card_id':          c.card.card_id,
                        'card_title':       c.card.card_title,
                        'type':             c.card.type,
                        'chat_msg_card_id': c.chat_msg_card_id,
                        'is_selected':      c.is_selected,
                    }
                    for c in cards if c.card
                ],
            })
        return Response({
            'chat_session_id': session.chat_session_id,
            'session_title':   session.session_title,
            'messages':        msgs_data,
        }, status=status.HTTP_200_OK)

    def delete(self, request, chat_session_id):
        from rest_framework_simplejwt.tokens import AccessToken as AT
        from apps.chatbot.models import ChatSession

        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        try:
            payload = AT(token)
            user_id = payload['user_id']
        except Exception:
            return Response({'detail': '인증 실패'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            session = ChatSession.objects.get(chat_session_id=chat_session_id, user_id=user_id)
        except ChatSession.DoesNotExist:
            return Response({'detail': '세션 없음'}, status=status.HTTP_404_NOT_FOUND)

        session.is_delete = 1
        session.save(update_fields=['is_delete'])


# ── 내 성별 조회 ──────────────────────────────────────────────────────────────

class MeView(APIView):
    """
    GET /member/me/
    JWT 토큰으로 현재 로그인 유저의 gender 반환
    """
    def get(self, request):
        from rest_framework_simplejwt.tokens import AccessToken as AT
        from rest_framework_simplejwt.exceptions import TokenError
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return Response({'detail': '인증 필요'}, status=status.HTTP_401_UNAUTHORIZED)
        try:
            payload = AT(auth.split(' ')[1])
            user = User.objects.get(user_id=payload['user_id'])
        except (TokenError, KeyError, User.DoesNotExist):
            return Response({'detail': '인증 실패'}, status=status.HTTP_401_UNAUTHORIZED)
        return Response({'gender': user.gender})
        return Response({'detail': '삭제됨'}, status=status.HTTP_200_OK)