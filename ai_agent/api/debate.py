"""
api/debate.py
토론 API 라우터 (엔드포인트 통합 버전)

엔드포인트 (2개):
  POST /debate/sessions/{session_id}          통합 명령 — body의 action으로 분기
  GET  /debate/sessions/{session_id}/stream   SSE 스트리밍 (첫 이벤트로 현재 state 전송)

왜 2개인가:
  - create / input / action / question 은 전부 LangGraph state를 갱신하는
    JSON 명령(POST, 즉시 반환)이라 하나의 POST로 통합.
  - /stream 은 SSE(text/event-stream)로 서버가 다수 이벤트를 push 하는
    long-lived GET 연결 → 응답 모델·HTTP 메서드·프록시 설정이 근본적으로
    달라 단일 핸들러로 합칠 수 없으므로 분리 유지.
  - 구 GET /state 는 제거하고, /stream 의 첫 이벤트(type="state")로 흡수.

action 별 body:
  create   : {"action": "create",   "policy_card": {...}, "mode": "...", "difficulty"?: "...", "user_stance"?: "..."}
  input    : {"action": "input",    "user_input": "..."}
  choice   : {"action": "choice",   "user_action": "next|extra|question", "question_target"?: "pro|con"}
  question : {"action": "question", "user_input": "...", "question_target": "pro|con"}

흐름:
  1. POST .../{id} {action:"create"}                  → initial_state 저장
  2. GET  .../{id}/stream                             → SSE: state 이벤트 + interrupt까지 스트리밍
  3. POST .../{id} {action:"input"|"choice"|"question"} → state update
  4. 다시 GET .../{id}/stream → 재개 → 반복
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.debate.state import make_initial_state

logger = logging.getLogger(__name__)

debate_router = APIRouter(tags=["debate"])

# ── 통합 요청 스키마 ──────────────────────────────────────────────────────────

class DebateCommand(BaseModel):
    """POST /sessions/{id} 통합 명령. action에 따라 사용하는 필드가 달라진다."""
    action: str  # "create" | "input" | "choice" | "question"

    # create 전용
    policy_card: Optional[dict] = None
    mode:        Optional[str]  = None              # "ai_vs_ai" | "ai_vs_user"
    difficulty:  str            = "hard"
    user_stance: Optional[str]  = None              # "pro" | "con" (ai_vs_user 전용)

    # input / question 공용
    user_input:  Optional[str]  = None

    # choice 전용
    user_action: Optional[str]  = None              # "next" | "extra" | "question"

    # choice / question 공용
    question_target: Optional[str] = None           # "pro" | "con"


# ── 헬퍼 ────────────────────────────────────────────────────────────────────

def _get_graph():
    from main import _state
    graph = _state.get("graph")
    if graph is None:
        raise HTTPException(status_code=503, detail="그래프 초기화 중입니다.")
    return graph


def _thread_config(session_id: int) -> dict:
    return {"configurable": {"thread_id": str(session_id)}}


async def _raw_delete_thread(saver, thread_id: str):
    """adelete_thread가 없을 때 SQLite에서 thread 행 직접 삭제 (폴백)."""
    conn = saver.conn   # aiosqlite connection
    for tbl in ("writes", "checkpoints", "checkpoint_blobs"):
        try:
            await conn.execute(f"DELETE FROM {tbl} WHERE thread_id = ?", (thread_id,))
        except Exception:
            pass   # 버전마다 테이블명 상이 — 존재하는 것만 삭제
    await conn.commit()


def _stage_to_round(stage: str) -> str:
    mapping = {"position": "1", "pro_round": "2", "con_round": "3", "summary": "4", "done": "4"}
    return mapping.get(stage, "1")


def _make_sse(event_type: str, data: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **data}, ensure_ascii=False)}\n\n"


# ── 통합 명령 엔드포인트 ──────────────────────────────────────────────────────

@debate_router.post("/sessions/{session_id}")
async def debate_command(session_id: int, req: DebateCommand):
    """
    토론 진행 명령을 action으로 분기 처리.
      create   → LangGraph initial state 생성/저장
      input    → user_turn interrupt 재개용 user_input 주입
      choice   → user_choice interrupt 재개용 user_action 주입
      question → AI vs AI 질문 직접 처리 (그래프 우회)
    """
    handlers = {
        "create":   _cmd_create,
        "input":    _cmd_input,
        "choice":   _cmd_choice,
        "question": _cmd_question,
    }
    handler = handlers.get(req.action)
    if handler is None:
        raise HTTPException(status_code=400, detail=f"알 수 없는 action: {req.action}")
    return await handler(session_id, req)


# ── create: 세션 생성 ─────────────────────────────────────────────────────────

async def _cmd_create(session_id: int, req: DebateCommand):
    """
    LangGraph initial state 생성 후 checkpoint에 저장.
    실제 첫 발언 스트리밍은 이후 GET /stream 에서 진행.
    """
    if req.policy_card is None or req.mode is None:
        raise HTTPException(status_code=400, detail="create에는 policy_card와 mode가 필요합니다.")

    graph  = _get_graph()
    config = _thread_config(session_id)

    # ★ 같은 thread_id(=session_id)에 남아있을 수 있는 옛 체크포인트 제거 (메시지 누적 오염 방지)
    try:
        thread_id = str(session_id)
        if hasattr(graph, "adelete_thread"):
            await graph.adelete_thread(thread_id)
        elif hasattr(graph.checkpointer, "adelete_thread"):
            await graph.checkpointer.adelete_thread(thread_id)
        elif hasattr(graph.checkpointer, "delete_thread"):
            graph.checkpointer.delete_thread(thread_id)
        else:
            await _raw_delete_thread(graph.checkpointer, thread_id)
    except Exception as e:
        logger.warning(f"thread 체크포인트 삭제 실패(계속 진행) id={session_id}: {e}")

    initial_state = make_initial_state(
        debate_id   = session_id,
        mode        = req.mode,
        difficulty  = req.difficulty,
        policy_card = req.policy_card,
        user_stance = req.user_stance,
    )

    try:
        await graph.aupdate_state(config, initial_state)
    except Exception as e:
        logger.error(f"세션 생성 실패 (id={session_id}): {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"debate_session_id": session_id, "ok": True}


# ── input: 사용자 발언 입력 ───────────────────────────────────────────────────

async def _cmd_input(session_id: int, req: DebateCommand):
    """
    user_turn interrupt 재개용.
    user_input을 state에 주입 후 graph가 다음 interrupt까지 실행.
    유효성 결과는 이후 /stream에서 warning 이벤트로 전달됨.
    """
    if req.user_input is None:
        raise HTTPException(status_code=400, detail="input에는 user_input이 필요합니다.")

    graph  = _get_graph()
    config = _thread_config(session_id)

    try:
        # user_input만 state에 주입 (as_node 없음 → 그래프가 user_turn부터 재개)
        await graph.aupdate_state(config, {"user_input": req.user_input})
    except Exception as e:
        logger.error(f"user_input 처리 실패 (session={session_id}): {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"valid": True, "warning": None}


# ── choice: 사용자 선택 ───────────────────────────────────────────────────────

async def _cmd_choice(session_id: int, req: DebateCommand):
    """
    user_choice interrupt 재개용.
    user_action(next/extra/question)을 state에 주입.
    실제 AI 응답 스트리밍은 이후 GET /stream 호출 시 진행.
    """
    if req.user_action is None:
        raise HTTPException(status_code=400, detail="choice에는 user_action이 필요합니다.")

    graph  = _get_graph()
    config = _thread_config(session_id)

    update: dict = {"user_action": req.user_action}
    if req.question_target:
        update["question_target"] = req.question_target

    try:
        # user_action만 주입 (as_node 없음 → 그래프가 user_choice부터 재개해서 직접 처리)
        await graph.aupdate_state(config, update)
    except Exception as e:
        logger.error(f"user_action 처리 실패 (session={session_id}): {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True}


# ── question: AI vs AI 질문 처리 (그래프 우회) ───────────────────────────────

async def _cmd_question(session_id: int, req: DebateCommand):
    """
    AI vs AI 모드에서 사용자 질문을 직접 처리.
    LangGraph interrupt를 우회하여 대상 AI 에이전트에게 직접 질문하고 답변 반환.
    결과 메시지는 LangGraph state에 추가.
    """
    if req.user_input is None or req.question_target is None:
        raise HTTPException(status_code=400, detail="question에는 user_input과 question_target이 필요합니다.")

    from main import _state
    from agents.debate.tools import ConAgent, ProAgent, UserFilterTools
    from agents.debate.state import DebateMessage

    graph  = _get_graph()
    config = _thread_config(session_id)

    snapshot = await graph.aget_state(config)
    if not snapshot or not snapshot.values:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다.")

    state       = snapshot.values
    policy_card = state.get("policy_card", {})
    history     = list(state.get("messages", []))
    difficulty  = state.get("difficulty", "hard")

    qdrant_client = _state.get("qdrant_client")
    openai_client = _state.get("openai_client")

    # ── 입력 검증 (사용자 질문에도 동일한 필터 적용) ─────────────────────────
    user_filter  = UserFilterTools(openai_client, qdrant_client)
    # 주제이탈 판정은 card 제목이 아니라 '토론 주제(debate_topic)' 전체 기준 (off_topic 오탐 방지)
    policy_title = policy_card.get("debate_topic") or policy_card.get("title", "")
    filter_result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: user_filter.check(req.user_input, policy_title)
    )
    if not filter_result["passed"]:
        logger.info(f"질문 필터 차단 (session={session_id}): {filter_result.get('violation_type')}")
        return {
            "error":          "profanity_detected",
            "message":        filter_result["message"],
            "violation_type": filter_result.get("violation_type", ""),
        }

    # 질문 대상 에이전트 선택
    if req.question_target == "pro":
        agent = ProAgent(openai_client, qdrant_client)
    else:
        agent = ConAgent(openai_client, qdrant_client)

    # history에 사용자 질문 추가 후 답변 생성 (동기 → executor)
    question_msg = DebateMessage(
        participant="user",
        msg_type="position",
        content=req.user_input,
        sources=[],
    )
    history_with_q = history + [question_msg]

    loop = asyncio.get_event_loop()
    try:
        speech, sources, _ = await loop.run_in_executor(
            None,
            lambda: agent.generate(
                policy=policy_card,
                msg_type="question_ans",
                history=history_with_q,
                difficulty=difficulty,
            ),
        )
    except Exception as e:
        logger.error(f"질문 답변 생성 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    answer_msg = DebateMessage(
        participant=req.question_target,
        msg_type="question_ans",
        content=speech,
        sources=sources,
    )

    # LangGraph state에 두 메시지 추가 (질문 + 답변)
    await graph.aupdate_state(config, {
        "messages":      [question_msg, answer_msg],
        "user_action":   None,
    })

    return {
        "participant": req.question_target,
        "msg_type":    "question_ans",
        "content":     speech,
        "sources":     sources,
    }


# ── SSE 스트리밍 ─────────────────────────────────────────────────────────────

@debate_router.get("/sessions/{session_id}/stream")
async def stream_session(session_id: int):
    """
    LangGraph를 재개하여 다음 interrupt 지점까지 실행하고 각 노드 출력을 SSE로 전송.

    SSE 이벤트 타입:
      state            - (첫 이벤트) 현재 세션 state 스냅샷 — 구 GET /state 대체
      generation_start - AI 생성 구간 시작 시각
      message          - AI 발언 완료 (content, participant, msg_type, sources)
      summary          - 토론 요약 (data)
      round_update     - 라운드 변경 (stage, round)
      warning          - 사용자 입력 경고
      waiting          - interrupt 발생, 사용자 입력/선택 대기
      error            - 오류
    """
    graph  = _get_graph()
    config = _thread_config(session_id)

    async def event_generator():
        # ── 첫 이벤트: 현재 state 스냅샷 전송 (구 GET /state 대체) ──────────
        # 새로고침/재접속 시 클라이언트가 SSE 한 번으로 현재 상태를 복원 가능.
        try:
            init_snapshot = await graph.aget_state(config)
            if init_snapshot and init_snapshot.values:
                s = init_snapshot.values
                yield _make_sse("state", {
                    "debate_session_id": s.get("debate_id"),
                    "mode":              s.get("mode"),
                    "current_stage":     s.get("current_stage"),
                    "current_round":     s.get("current_round"),
                    "current_speaker":   s.get("current_speaker"),
                    "user_stance":       s.get("user_stance"),   # 이어하기 시 stance 복원용 (AI vs USER)
                    "next_node":         list(init_snapshot.next) if init_snapshot.next else [],
                    "messages":          s.get("messages", []),
                })
        except Exception as e:
            logger.warning(f"초기 state 전송 실패 (session={session_id}): {e}")

        # 스트리밍 시작 직전에 generation_start 이벤트 전송
        # 클라이언트는 이 시점부터 실제 AI 생성 구간을 측정 가능
        import time as _time
        yield _make_sse("generation_start", {"timestamp_ms": round(_time.time() * 1000)})

        try:
            async for chunk in graph.astream(None, config=config, stream_mode="updates"):
                for node_name, node_output in chunk.items():
                    if not isinstance(node_output, dict):
                        continue

                    # 새 메시지 이벤트
                    new_messages = node_output.get("messages", [])
                    for msg in new_messages:
                        if msg.get("msg_type") == "summary":
                            # summary는 content가 JSON 문자열
                            try:
                                summary_data = json.loads(msg["content"])
                            except Exception:
                                summary_data = {"raw": msg["content"]}
                            yield _make_sse("summary", {"data": summary_data})
                        else:
                            yield _make_sse("message", {
                                "participant":    msg.get("participant", ""),
                                "msg_type":       msg.get("msg_type", ""),
                                "content":        msg.get("content", ""),
                                "sources":        msg.get("sources", []),
                                "response_time_ms": node_output.get("last_speech_time_ms", 0),
                            })

                    # 라운드 변경 이벤트
                    stage = node_output.get("current_stage")
                    if stage:
                        yield _make_sse("round_update", {
                            "stage": stage,
                            "round": _stage_to_round(stage),
                        })

                    # 사용자 입력 경고
                    warning = node_output.get("user_input_warning")
                    if warning:
                        yield _make_sse("warning", {
                            "message":        warning,
                            "violation_type": node_output.get("user_input_violation", ""),
                        })

        except Exception as e:
            logger.error(f"스트리밍 오류 (session={session_id}): {e}")
            yield _make_sse("error", {"message": str(e)})
            return

        # 스트림 종료: interrupt 또는 완료
        try:
            snapshot = await graph.aget_state(config)
            if snapshot and snapshot.next:
                # interrupt 발생 — 사용자 입력/선택 대기
                next_node = snapshot.next[0] if snapshot.next else ""
                wait_type = "user_turn" if next_node == "user_turn" else "user_choice"
                current_state = snapshot.values or {}
                stage = current_state.get("current_stage", "")

                # _advance_state가 마지막 발언 후 current_stage를 "user_choice"로
                # 덮어쓰기 때문에, 인터럽트 시점의 stage는 항상 "user_choice".
                # 실제 라운드를 추론: "argument" 타입 메시지는 항상 그 라운드의
                # 공격측이 처음 발언 (pro_round→pro_agent, con_round→con_agent).
                inferred_stage = stage
                if stage == "user_choice":
                    messages = current_state.get("messages", [])
                    for msg in reversed(messages):
                        if msg.get("msg_type") == "argument":
                            p = msg.get("participant", "")
                            if p in ("pro", "pro_agent"):
                                inferred_stage = "pro_round"
                            elif p in ("con", "con_agent"):
                                inferred_stage = "con_round"
                            break

                if inferred_stage == "pro_round":
                    extra_used = current_state.get("pro_extra_count", 0)
                elif inferred_stage == "con_round":
                    extra_used = current_state.get("con_extra_count", 0)
                else:
                    extra_used = 0

                # PHASE 3: 입장제시 게이트 판정
                # position_con_done=True 이고 아직 라운드 발언(argument 등)이 없으면 입장제시 직후
                all_messages = current_state.get("messages", [])
                has_round_speech = any(
                    m.get("msg_type") in ("argument", "rebuttal", "response",
                                          "extra_rebuttal", "extra_response")
                    for m in all_messages
                )
                is_position_gate = (
                    current_state.get("position_con_done") and not has_round_speech
                )
                gate = "position_done" if is_position_gate else ""

                yield _make_sse("waiting", {
                    "wait_type":        wait_type,
                    "current_stage":    stage,
                    "current_round":    current_state.get("current_round", 1),
                    "extra_available":  extra_used < 2,   # MAX_EXTRA_ROUNDS = 2
                    "extra_remaining":  max(0, 2 - extra_used),
                    "gate":             gate,
                })
        except Exception as e:
            logger.warning(f"상태 조회 실패: {e}")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
