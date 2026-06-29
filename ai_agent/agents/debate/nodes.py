"""
debate_agent/nodes.py
LangGraph 노드 함수 모음 — 멀티에이전트 구조

노드 목록:
  route_node          : 오케스트레이터 — 다음 노드 결정
  pro_speech_node     : ProAgent 발언 생성
  con_speech_node     : ConAgent 발언 생성
  bias_check_node     : ReviewAgent 편향·혐오 검토
  evidence_check_node : pass-through (ReviewAgent에서 통합 처리)
  save_message_node   : 승인된 발언 저장 + 진행 상태 업데이트
  user_turn_node      : 사용자 발언 처리 (AI vs User)
  user_choice_node    : 라운드 종료 후 사용자 선택 대기
  summary_node        : SummaryAgent 요약 생성
"""
from __future__ import annotations

import json
import logging
import time as _time
from typing import Dict, Optional

from openai import OpenAI

from .state import DebateMessage, DebateState
from .tools import ConAgent, ProAgent, ReviewAgent, SummaryAgent, UserFilterTools

logger = logging.getLogger(__name__)

MAX_BIAS_RETRY   = 3
MAX_ROUND_TURNS  = 3
MAX_EXTRA_ROUNDS = 2


# ════════════════════════════════════════════════════════════════════════════
# 헬퍼
# ════════════════════════════════════════════════════════════════════════════

def _get_msg_type(state: DebateState) -> str:
    stage = state["current_stage"]
    step  = state["current_turn_step"]
    if stage == "position":
        return "position"
    return {
        "argument":       "argument",
        "rebuttal":       "rebuttal",
        "response":       "response",
        "extra_rebuttal": "extra_rebuttal",
        "extra_response": "extra_response",
    }.get(step, "argument")


# ════════════════════════════════════════════════════════════════════════════
# 노드 팩토리
# ════════════════════════════════════════════════════════════════════════════

def make_nodes(
    pro_agent:     ProAgent,
    con_agent:     ConAgent,
    review_agent:  ReviewAgent,
    summary_agent: SummaryAgent,
    user_filter:   UserFilterTools,
    engine=None,
):
    """
    모든 노드 함수를 생성해 dict로 반환.

    Parameters
    ----------
    pro_agent     : ProAgent 인스턴스
    con_agent     : ConAgent 인스턴스
    review_agent  : ReviewAgent 인스턴스
    summary_agent : SummaryAgent 인스턴스
    user_filter   : UserFilterTools 인스턴스
    engine        : SQLAlchemy engine (None이면 RDB 저장 스킵)
    """

    # ── RDB 저장 헬퍼 ────────────────────────────────────────────────────
    def _save_to_rdb(state: DebateState, content: str, msg_type: str, participant: str):
        if engine is None:
            return
        try:
            from db.rdb import save_debate_message
            p_map = {
                "pro":  state.get("pro_participant_id"),
                "con":  state.get("con_participant_id"),
                "user": state.get("user_participant_id"),
            }
            save_debate_message(engine, state["debate_id"], p_map.get(participant, 0) or 0,
                                content, msg_type.upper())
        except Exception as e:
            logger.warning(f"RDB 저장 오류: {e}")

    # ────────────────────────────────────────────────────────────────────
    # 1. route_node
    # ────────────────────────────────────────────────────────────────────
    def route_node(state: DebateState) -> dict:
        return {}

    # ────────────────────────────────────────────────────────────────────
    # 2. pro_speech_node
    # ────────────────────────────────────────────────────────────────────
    def pro_speech_node(state: DebateState) -> dict:
        msg_type = _get_msg_type(state)
        logger.info(f"[ProAgent] 발언 생성 (stage={state['current_stage']}, msg_type={msg_type})")

        t0 = _time.time()
        speech, sources, key_args = pro_agent.generate(
            policy         = state["policy_card"],
            msg_type       = msg_type,
            history        = state["messages"],
            difficulty     = state["difficulty"],
            used_arguments = state.get("pro_used_arguments", []),
        )
        gen_time_ms = round((_time.time() - t0) * 1000)
        logger.info(f"[타이밍] ProAgent 발언생성: {gen_time_ms}ms")
        logger.info(f"[ProAgent] 추출된 논거: {key_args}")

        return {
            "pending_speech":       speech,
            "pending_sources":      sources,
            "regenerate_speaker":   "pro",
            "pro_used_arguments":   state.get("pro_used_arguments", []) + key_args,
            "last_speech_time_ms":  gen_time_ms,
        }

    # ────────────────────────────────────────────────────────────────────
    # 3. con_speech_node
    # ────────────────────────────────────────────────────────────────────
    def con_speech_node(state: DebateState) -> dict:
        msg_type = _get_msg_type(state)
        logger.info(f"[ConAgent] 발언 생성 (stage={state['current_stage']}, msg_type={msg_type})")

        t0 = _time.time()
        speech, sources, key_args = con_agent.generate(
            policy         = state["policy_card"],
            msg_type       = msg_type,
            history        = state["messages"],
            difficulty     = state["difficulty"],
            used_arguments = state.get("con_used_arguments", []),
        )
        gen_time_ms = round((_time.time() - t0) * 1000)
        logger.info(f"[타이밍] ConAgent 발언생성: {gen_time_ms}ms")
        logger.info(f"[ConAgent] 추출된 논거: {key_args}")

        return {
            "pending_speech":       speech,
            "pending_sources":      sources,
            "regenerate_speaker":   "con",
            "con_used_arguments":   state.get("con_used_arguments", []) + key_args,
            "last_speech_time_ms":  gen_time_ms,
        }

    # ────────────────────────────────────────────────────────────────────
    # 4. bias_check_node — ReviewAgent 편향·혐오 검토
    # ────────────────────────────────────────────────────────────────────
    def bias_check_node(state: DebateState) -> dict:
        speech = state.get("pending_speech", "")

        t0 = _time.time()
        result = review_agent.review(speech)
        logger.info(f"[타이밍] ReviewAgent 검토: {_time.time()-t0:.2f}s")
        logger.info(f"[ReviewAgent] passed={result['passed']}, failed={result['failed']}, reason={result['reason']}")

        passed    = result["passed"]
        new_retry = state["bias_retry_count"] + (0 if passed else 1)
        if new_retry >= MAX_BIAS_RETRY and not passed:
            logger.warning("[ReviewAgent] 최대 재시도 도달 → 강제 통과")
            passed = True

        return {
            "bias_check_passed":     passed,
            "bias_retry_count":      new_retry,
            "evidence_check_passed": passed,
            "evidence_retry_count":  0,
        }

    # ────────────────────────────────────────────────────────────────────
    # 5. evidence_check_node — pass-through (ReviewAgent에서 통합 처리)
    # ────────────────────────────────────────────────────────────────────
    def evidence_check_node(state: DebateState) -> dict:
        return {}

    # ────────────────────────────────────────────────────────────────────
    # 6. save_message_node
    # ────────────────────────────────────────────────────────────────────
    def save_message_node(state: DebateState) -> dict:
        speech   = state.get("pending_speech", "")
        sources  = state.get("pending_sources", [])
        speaker  = state["current_speaker"]
        msg_type = _get_msg_type(state)

        msg = DebateMessage(
            participant=speaker,
            msg_type=msg_type,
            content=speech,
            sources=sources,
        )
        _save_to_rdb(state, speech, msg_type, speaker)

        updates = _advance_state(state)
        updates["messages"]              = [msg]
        updates["pending_speech"]        = None
        updates["pending_sources"]       = None
        updates["bias_retry_count"]      = 0
        updates["evidence_retry_count"]  = 0
        updates["bias_check_passed"]     = False
        updates["evidence_check_passed"] = False
        updates["regenerate_speaker"]    = None
        # SSE로 응답시간을 전달하기 위해 state에서 읽어 그대로 포함
        updates["last_speech_time_ms"]   = state.get("last_speech_time_ms", 0)

        logger.info(f"[저장] {speaker}/{msg_type} → 다음: {updates.get('current_stage')}/{updates.get('current_speaker')}")
        return updates

    def _advance_state(state: DebateState) -> dict:
        stage       = state["current_stage"]
        step        = state["current_turn_step"]
        round_      = state["current_round"]
        mode        = state["mode"]
        user_stance = state.get("user_stance")
        difficulty  = state.get("difficulty", "easy")
        updates: dict = {}

        if stage == "position":
            speaker = state["current_speaker"]
            # PHASE 2: hard + ai_vs_user 여부 플래그
            is_hard_user = (mode == "ai_vs_user" and difficulty == "hard")

            # 찬성 입장 완료 조건:
            #   - AI pro가 발언했거나
            #   - user가 pro 입장으로 직접 작성했을 때 (hard + stance=pro)
            if speaker == "pro" or (speaker == "user" and user_stance == "pro"):
                updates["position_pro_done"] = True
                # 다음 발언자 결정: 반대가 유저(hard+con)면 user, 아니면 AI con
                updates["current_speaker"] = (
                    "user" if (is_hard_user and user_stance == "con") else "con"
                )
            else:
                # 반대 입장 완료 → PHASE 3: 바로 pro_round 가지 않고 user_choice 게이트로
                # (pro_round 시작은 user_choice_node 'next'에서)
                updates["position_con_done"] = True
                updates["current_stage"]     = "user_choice"
            return updates

        if stage in ("pro_round", "con_round"):
            lead = "pro" if stage == "pro_round" else "con"
            opp  = "con" if lead == "pro" else "pro"

            if step == "argument":
                next_spk = "user" if mode == "ai_vs_user" and user_stance == opp else opp
                updates["current_turn_step"] = "rebuttal"
                updates["current_speaker"]   = next_spk

            elif step == "rebuttal":
                next_spk = "user" if mode == "ai_vs_user" and user_stance == lead else lead
                updates["current_turn_step"] = "response"
                updates["current_speaker"]   = next_spk

            elif step == "response":
                # 매 턴 종료 후 user_choice로 (턴 번호는 current_round에 유지)
                updates["current_stage"] = "user_choice"

            elif step == "extra_rebuttal":
                next_spk = "user" if mode == "ai_vs_user" and user_stance == lead else lead
                updates["current_turn_step"] = "extra_response"
                updates["current_speaker"]   = next_spk

            elif step == "extra_response":
                # 추가 토론 완료 후 user_choice로
                updates["current_stage"] = "user_choice"

        return updates

    # ────────────────────────────────────────────────────────────────────
    # 7. user_turn_node
    # ────────────────────────────────────────────────────────────────────
    def user_turn_node(state: DebateState) -> dict:
        user_input = state.get("user_input", "")
        if not user_input:
            return {}

        # 주제이탈 판정 기준: card 제목이 아니라 '토론 주제(debate_topic)' 전체를 사용.
        # (card_title은 "강좌신청"처럼 행정적 제목이라 정상 발언도 off_topic 오탐 발생)
        _pc = state["policy_card"]
        topic = _pc.get("debate_topic") or _pc.get("title", "") or ""
        result = user_filter.check(user_input, topic)

        if not result["passed"]:
            logger.info(f"[사용자 입력 거부] type={result['violation_type']}")
            return {
                "user_input_valid":     False,
                "user_input_warning":   result["message"],
                "user_input_violation": result["violation_type"],
                "user_input":           None,
            }

        msg_type = _get_msg_type(state)
        _save_to_rdb(state, user_input, msg_type, "user")

        msg = DebateMessage(
            participant="user",
            msg_type=msg_type,
            content=user_input,
            sources=[],
        )
        updates = _advance_state(state)
        updates["messages"]           = [msg]
        updates["user_input"]         = None
        updates["user_input_valid"]   = True
        updates["user_input_warning"] = None
        updates["pending_speech"]     = None
        updates["bias_retry_count"]   = 0
        updates["evidence_retry_count"] = 0
        return updates

    # ────────────────────────────────────────────────────────────────────
    # 8. user_choice_node
    # ────────────────────────────────────────────────────────────────────
    def user_choice_node(state: DebateState) -> dict:
        action = state.get("user_action")
        if not action:
            return {}

        last_stage = _infer_last_round_stage(state)

        if action == "next":
            # PHASE 3: 입장제시 직후(라운드 발언 없음) → 찬성세부주장 시작
            has_round_speech = any(
                m["msg_type"] in ("argument", "rebuttal", "response",
                                  "extra_rebuttal", "extra_response")
                for m in state.get("messages", [])
            )
            if state.get("position_con_done") and not has_round_speech:
                _mode = state.get("mode", "ai_vs_ai")
                _us   = state.get("user_stance")
                return {
                    "current_stage":     "pro_round",
                    "current_round":     1,
                    "current_turn_step": "argument",
                    "current_speaker":   "user" if (_mode == "ai_vs_user" and _us == "pro") else "pro",
                    "user_action":       None,
                }

            current_round = state.get("current_round", 1)
            mode          = state.get("mode", "ai_vs_ai")
            user_stance   = state.get("user_stance")

            if current_round < MAX_ROUND_TURNS:
                # 턴 1·2 종료: 다음 턴으로 이동 (같은 라운드 유지)
                lead = "pro" if last_stage == "pro_round" else "con"
                next_spk = "user" if mode == "ai_vs_user" and user_stance == lead else lead
                return {
                    "current_stage":     last_stage,
                    "current_round":     current_round + 1,
                    "current_turn_step": "argument",
                    "current_speaker":   next_spk,
                    "user_action":       None,
                }
            else:
                # 턴 3 종료: 다음 라운드 또는 주장 다지기로 이동
                if last_stage == "pro_round":
                    lead_speaker = (
                        "user" if mode == "ai_vs_user" and user_stance == "con" else "con"
                    )
                    return {
                        "current_stage":     "con_round",
                        "current_round":     1,
                        "current_turn_step": "argument",
                        "current_speaker":   lead_speaker,
                        "user_action":       None,
                    }
                return {"current_stage": "summary", "user_action": None}

        if action == "extra":
            extra_key = "pro_extra_count" if last_stage == "pro_round" else "con_extra_count"
            cur_extra = state.get(extra_key, 0)
            if cur_extra >= MAX_EXTRA_ROUNDS:
                return {"user_action": None}
            lead    = "pro" if last_stage == "pro_round" else "con"
            opp     = "con" if lead == "pro" else "pro"
            next_spk = (
                "user" if state["mode"] == "ai_vs_user" and state.get("user_stance") == opp
                else opp
            )
            return {
                "current_stage":     last_stage,
                "current_turn_step": "extra_rebuttal",
                "current_speaker":   next_spk,
                extra_key:           cur_extra + 1,
                "user_action":       None,
            }

        if action == "question":
            return {
                "current_stage":   state["current_stage"],
                "user_action":     None,
                "current_speaker": "user",
            }

        return {"user_action": None}

    def _infer_last_round_stage(state: DebateState) -> str:
        for msg in reversed(state.get("messages", [])):
            if msg["msg_type"] in ("argument", "rebuttal", "response",
                                   "extra_rebuttal", "extra_response"):
                if msg["participant"] == "pro":
                    return "pro_round"
                if msg["participant"] == "con":
                    return "con_round"
                if msg["participant"] == "user":
                    stance = state.get("user_stance")
                    return "pro_round" if stance == "pro" else "con_round"
        return "pro_round"

    # ────────────────────────────────────────────────────────────────────
    # 9. summary_node — SummaryAgent
    # ────────────────────────────────────────────────────────────────────
    def summary_node(state: DebateState) -> dict:
        logger.info("[SummaryAgent] 요약 생성 중...")
        has_user     = state["mode"] == "ai_vs_user"
        summary_data = summary_agent.summarize(
            policy   = state["policy_card"],
            messages = state["messages"],
            has_user = has_user,
        )

        summary_msg = DebateMessage(
            participant="system",
            msg_type="summary",
            content=json.dumps(summary_data, ensure_ascii=False),
            sources=[],
        )
        _save_to_rdb(state, json.dumps(summary_data, ensure_ascii=False), "summary", "pro")

        return {"messages": [summary_msg], "current_stage": "done"}

    # ────────────────────────────────────────────────────────────────────
    return {
        "route":          route_node,
        "pro_speech":     pro_speech_node,
        "con_speech":     con_speech_node,
        "bias_check":     bias_check_node,
        "evidence_check": evidence_check_node,
        "save_message":   save_message_node,
        "user_turn":      user_turn_node,
        "user_choice":    user_choice_node,
        "summary":        summary_node,
    }


# ════════════════════════════════════════════════════════════════════════════
# 조건부 엣지 함수
# ════════════════════════════════════════════════════════════════════════════

def route_decision(state: DebateState) -> str:
    stage   = state["current_stage"]
    speaker = state["current_speaker"]
    mode    = state["mode"]

    if stage == "done":
        return "END"
    if stage == "summary":
        return "summary"
    if stage == "user_choice":
        return "user_choice"
    if mode == "ai_vs_user" and speaker == "user":
        return "user_turn"
    return "pro_speech" if speaker == "pro" else "con_speech"


def after_bias_check(state: DebateState) -> str:
    if state["bias_check_passed"]:
        return "evidence_check"
    if state["bias_retry_count"] >= MAX_BIAS_RETRY:
        return "evidence_check"
    return state.get("regenerate_speaker", "pro") + "_speech"


def after_evidence_check(state: DebateState) -> str:
    if state["evidence_check_passed"]:
        return "save_message"
    return "save_message"  # evidence_check는 pass-through이므로 항상 저장


def after_user_turn(state: DebateState) -> str:
    if state.get("user_input_valid") is False:
        return "user_turn"
    if state.get("user_input_valid") is True:
        return "route"
    return "user_turn"


def after_user_choice(state: DebateState) -> str:
    stage = state["current_stage"]
    if stage == "summary":
        return "summary"
    if stage == "done":
        return "END"
    return "route"
