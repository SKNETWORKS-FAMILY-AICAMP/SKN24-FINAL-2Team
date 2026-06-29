"""
apps/debates/views.py
"""
import json
import logging
import os

import httpx
from asgiref.sync import sync_to_async
from django.db import models
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework_simplejwt.tokens import AccessToken
from rest_framework_simplejwt.exceptions import TokenError

from apps.cards.models import InfoCard

from .models import DebateMessage, DebateSession
from .serializers import (
    DebateStartRequestSerializer,
    MessageCallbackSerializer,
    UserActionSerializer,
    UserInputSerializer,
)

logger = logging.getLogger(__name__)

AI_AGENT_URL = os.getenv("AI_AGENT_URL", "http://localhost:8001")

FOUL_VIOLATIONS = {
    "profanity", "political_slur", "group_stigma",
    "regional_slur", "threat", "hate_suffix", "context_hate",
}

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
    if participant == "pro":
        return "PRO"
    if participant == "con":
        return "CON"
    return "QUESTION"  # user question (fallback)


def _json_body(request):
    try:
        return json.loads(request.body)
    except Exception:
        return {}


# ── 토론 세션 시작 ─────────────────────────────────────────────────────────

class DebateStartView(View):
    def post(self, request):
        data = _json_body(request)
        serializer = DebateStartRequestSerializer(data=data)
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)

        vd = serializer.validated_data

        try:
            card = InfoCard.objects.only(
                "card_id", "card_title", "summary", "core_content", "debate_topic"
            ).get(card_id=vd["card_id"])
        except InfoCard.DoesNotExist:
            return JsonResponse({"error": "카드를 찾을 수 없습니다."}, status=404)

        from apps.users.models import User as _User
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JsonResponse({"error": "로그인이 필요합니다."}, status=401)
        try:
            token = AccessToken(auth_header.split(" ")[1])
            user = _User.objects.get(user_id=token["user_id"])
        except (TokenError, KeyError, _User.DoesNotExist):
            return JsonResponse({"error": "유효하지 않은 토큰입니다."}, status=401)

        session = DebateSession.objects.create(
            card=card, user=user, current_round="1"
        )

        # 모드 판별용 마커: aiuser 세션 생성 시 즉시 저장
        if vd.get("mode") == "ai_vs_user":
            DebateMessage.objects.create(
                debate_session=session,
                role="USER",
                content="__mode_aiuser__",
                message_type="PRO",
            )

        policy_card = {
            "id":             card.card_id,
            "title":          card.card_title,
            "summary_points": [card.summary],
            "background":     card.core_content,
            "debate_topic":   card.debate_topic or "",   # 'A다 vs B다' (뉴스카드), 정책카드는 ""
        }

        payload = {
            "action":            "create",
            "policy_card":       policy_card,
            "mode":              vd["mode"],
            "difficulty":        vd.get("difficulty", "hard"),
            "user_stance":       vd.get("user_stance"),
        }

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{AI_AGENT_URL}/debate/sessions/{session.debate_session_id}",
                    json=payload,
                )
                resp.raise_for_status()
        except httpx.HTTPError as e:
            session.delete()
            logger.error(f"ai_agent 세션 생성 실패: {e}")
            return JsonResponse({"error": "AI 서버 연결 실패"}, status=503)

        return JsonResponse({"debate_session_id": session.debate_session_id}, status=201)


# ── 세션 조회 ─────────────────────────────────────────────────────────────

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


# ── SSE 스트리밍 relay ────────────────────────────────────────────────────

class DebateStreamView(View):
    # ASGI(UvicornWorker)에서 동기 제너레이터로 스트리밍하면 응답이 한 번에 버퍼링된다.
    # 비동기 뷰 + httpx.AsyncClient(aiter_lines)로 바꿔 ai_agent가 발언을 보내는 즉시
    # 브라우저로 흘려보낸다(입장제시부터 바로 표시 → 초기 로딩 체감 단축).
    async def get(self, request, debate_session_id):
        try:
            session = await sync_to_async(DebateSession.objects.get)(
                debate_session_id=debate_session_id
            )
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)

        from django.utils import timezone
        await sync_to_async(
            lambda: DebateSession.objects.filter(pk=session.pk).update(updated_at=timezone.now())
        )()

        @sync_to_async
        def _bump_foul_count():
            from apps.users.models import User
            User.objects.filter(pk=session.user_id).update(foul_count=models.F("foul_count") + 1)
            return User.objects.filter(pk=session.user_id).values_list("foul_count", flat=True).first()

        @sync_to_async
        def _save_message(participant, save_type, content):
            DebateMessage.objects.create(
                debate_session=session,
                role=PARTICIPANT_TO_ROLE.get(participant, "AI"),
                content=content,
                message_type=save_type,
            )

        @sync_to_async
        def _save_round(new_round):
            session.current_round = new_round
            session.save(update_fields=["current_round"])

        async def event_generator():
            try:
                # read 타임아웃은 발언 생성 사이 긴 간격(수십 초)을 견디도록 넉넉히
                timeout = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream(
                        "GET",
                        f"{AI_AGENT_URL}/debate/sessions/{debate_session_id}/stream",
                    ) as ai_resp:
                        async for line in ai_resp.aiter_lines():
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
                                            save_type = _ai_agent_msg_type(participant, msg_type_raw)
                                            await _save_message(participant, save_type, content)

                                    elif event_type == "round_update":
                                        new_round = STAGE_TO_ROUND.get(
                                            event_data.get("stage", ""), session.current_round
                                        )
                                        if new_round != session.current_round:
                                            await _save_round(new_round)

                                    elif event_type == "warning":
                                        vtype = event_data.get("violation_type", "")
                                        if vtype in FOUL_VIOLATIONS:
                                            new_count = await _bump_foul_count()
                                            event_data["foul_count"] = new_count
                                            event_data["category"]   = "foul"
                                        else:
                                            event_data["category"] = "off_topic"
                                        raw = json.dumps(event_data, ensure_ascii=False)

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


# ── 사용자 발언 입력 ──────────────────────────────────────────────────────

class DebateInputView(View):
    def post(self, request, debate_session_id):
        serializer = UserInputSerializer(data=_json_body(request))
        if not serializer.is_valid():
            return JsonResponse(serializer.errors, status=400)

        try:
            session = DebateSession.objects.get(debate_session_id=debate_session_id)
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)

        user_input = serializer.validated_data["user_input"]

        # AI vs User 모드 판별용: 유저 발언 저장 (AI agent 성공 여부와 무관)
        DebateMessage.objects.create(
            debate_session=session,
            role="USER",
            content=user_input,
            message_type="PRO",
        )

        try:
            with httpx.Client(timeout=30) as client:
                resp = client.post(
                    f"{AI_AGENT_URL}/debate/sessions/{debate_session_id}",
                    json={"action": "input", "user_input": user_input},
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPError as e:
            logger.error(f"ai_agent input 전달 실패: {e}")
            return JsonResponse({"error": "AI 서버 연결 실패"}, status=503)

        return JsonResponse(result)


# ── 사용자 선택 ───────────────────────────────────────────────────────────

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


# ── 질문 처리 (AI vs AI) ─────────────────────────────────────────────────────

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
                    json={"action": "question",
                          "user_input": data.get("user_input", ""),
                          "question_target": data.get("question_target", "pro")},
                )
                resp.raise_for_status()
                result = resp.json()
        except httpx.HTTPError as e:
            logger.error(f"ai_agent question 전달 실패: {e}")
            return JsonResponse({"error": "AI 서버 연결 실패"}, status=503)

        # 욕설/혐오 필터 차단 처리
        if result.get("error") == "profanity_detected":
            vtype = result.get("violation_type", "")
            if vtype in FOUL_VIOLATIONS:
                from apps.users.models import User as _UserModel
                _UserModel.objects.filter(pk=session.user_id).update(
                    foul_count=models.F("foul_count") + 1
                )
                new_count = _UserModel.objects.filter(pk=session.user_id).values_list(
                    "foul_count", flat=True
                ).first()
                return JsonResponse({
                    "error":      "profanity_detected",
                    "foul_count": new_count,
                    "message":    result.get("message", ""),
                })
            return JsonResponse({
                "error":   "off_topic",
                "message": result.get("message", ""),
            })

        # 질문(USER) + 답변(AI) DB 저장
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


# ── ai_agent 콜백 ─────────────────────────────────────────────────────────

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


# ── 토론용 카드 정보 (인증 불필요 — 카드 공개 정보) ─────────────────────────────

class DebateCardInfoView(View):
    """GET /api/debates/card-info/<card_id>/ — 토론 페이지 제목 로딩용"""
    def get(self, request, card_id):
        try:
            card = InfoCard.objects.only(
                "card_id", "card_title", "debate_topic"
            ).get(card_id=card_id)
        except InfoCard.DoesNotExist:
            return JsonResponse({"error": "카드를 찾을 수 없습니다."}, status=404)
        return JsonResponse({
            "card_id":      card.card_id,
            "card_title":   card.card_title,
            "debate_topic": card.debate_topic,
        })


# 0620 수정
# ── 토론 히스토리 조회 ────────────────────────────────────────────────────────────

class DebateHistoryView(View):
    def get(self, request):
        user_id = request.GET.get("user_id")
        if not user_id:
            return JsonResponse({"error": "user_id required"}, status=400)

        from django.db.models import Exists, OuterRef
        sessions = (
            DebateSession.objects
            .filter(user_id=user_id)
            .select_related("card")
            .annotate(
                has_user_msg=Exists(
                    DebateMessage.objects.filter(
                        debate_session=OuterRef("pk"), role="USER",
                        message_type__in=["PRO", "CON"]
                    )
                )
            )
            .order_by("-updated_at")
        )

        result = []
        for s in sessions:
            result.append({
                "debate_session_id": s.debate_session_id,
                "card_id":           s.card.card_id,
                "debate_topic":      s.card.debate_topic or "",
                "card_title":        s.card.card_title,
                "created_at":        s.created_at.isoformat(),
                "updated_at":        s.updated_at.isoformat(),
                "is_done":           s.current_round == "4",
                "current_round":     int(s.current_round),
                "mode":              "aiuser" if s.has_user_msg else "aiai",
            })

        return JsonResponse({"sessions": result})


# ── 토론 세션 삭제 ────────────────────────────────────────────────────────────────

class DebateDeleteView(View):
    def delete(self, request, debate_session_id):
        try:
            session = DebateSession.objects.get(debate_session_id=debate_session_id)
        except DebateSession.DoesNotExist:
            return JsonResponse({"error": "세션을 찾을 수 없습니다."}, status=404)

        session.delete()
        return JsonResponse({"ok": True})


# ── 테스트 페이지 ─────────────────────────────────────────────────────────────

def test_page(request):
    return render(request, "debates/test.html")
