"""
debate_agent/state.py
LangGraph DebateState 정의
"""
from __future__ import annotations

import operator
from typing import Annotated, Dict, List, Literal, Optional, TypedDict


class DebateMessage(TypedDict):
    participant: Literal["pro", "con", "user", "system"]
    msg_type: Literal[
        "position",
        "argument",
        "rebuttal",
        "response",
        "question_ans",
        "extra_rebuttal",
        "extra_response",
        "summary",
    ]
    content: str
    sources: List[Dict]  # RAG 결과 [{"content": ..., "metadata": {"source_url": ..., "doc_type": ...}, "score": ...}]


class DebateState(TypedDict):
    # ── 토론 기본 정보 ────────────────────────────────────────────────────
    debate_id: int
    mode: Literal["ai_vs_ai", "ai_vs_user"]
    difficulty: Literal["easy", "hard"]
    policy_card: Dict  # {id, title, summary_points, background}

    # ── 참여자 ──────────────────────────────────────────────────────────
    user_stance: Optional[Literal["pro", "con"]]  # AI vs User: 사용자 입장

    # ── 진행 상태 ────────────────────────────────────────────────────────
    current_stage: Literal[
        "position",      # 입장 제시
        "pro_round",     # 찬성 세부주장 라운드
        "con_round",     # 반대 세부주장 라운드
        "user_choice",   # 라운드 종료 후 사용자 선택 대기
        "summary",       # 주장 다지기
        "done",          # 토론 종료
    ]
    current_round: int                                            # 현재 라운드 내 턴 (1~3)
    current_turn_step: Literal["argument", "rebuttal", "response",
                               "extra_rebuttal", "extra_response"]  # 턴 내 단계
    current_speaker: Literal["pro", "con", "user"]               # 현재 발언 주체
    position_pro_done: bool                                       # 찬성 입장 제시 완료
    position_con_done: bool                                       # 반대 입장 제시 완료

    # ── 메시지 이력 (자동 누적) ──────────────────────────────────────────
    messages: Annotated[List[DebateMessage], operator.add]

    # ── 발언 파이프라인 (생성 → 편향검토 → 근거확인 → 저장) ────────────
    pending_speech: Optional[str]          # 생성된 발언 초안 (검증 대기)
    pending_sources: Optional[List[Dict]]  # 검색된 RAG 근거
    bias_retry_count: int                  # 편향 재생성 횟수 (최대 3)
    evidence_retry_count: int              # 근거 재생성 횟수 (최대 3)
    bias_check_passed: bool                # 편향검토 통과 여부
    evidence_check_passed: bool            # 근거확인 통과 여부
    regenerate_speaker: Optional[Literal["pro", "con"]]  # 재생성 대상

    # ── 추가 토론 횟수 ───────────────────────────────────────────────────
    pro_extra_count: int   # 찬성 라운드 추가 토론 횟수 (최대 2)
    con_extra_count: int   # 반대 라운드 추가 토론 횟수 (최대 2)

    # ── 논거 추적 (중복 방지) ─────────────────────────────────────────────
    pro_used_arguments: List[str]   # ProAgent가 사용한 핵심 논거 누적 리스트
    con_used_arguments: List[str]   # ConAgent가 사용한 핵심 논거 누적 리스트

    # ── 사용자 입력 ──────────────────────────────────────────────────────
    user_input: Optional[str]                                         # 사용자가 입력한 텍스트
    user_input_valid: Optional[bool]                                  # 유효성 검사 결과
    user_input_warning: Optional[str]                                 # 경고/안내 문구
    user_input_violation: Optional[str]                               # 위반 유형 (profanity/off_topic 등)
    user_action: Optional[Literal["question", "extra", "next"]]      # 라운드 종료 후 선택
    question_target: Optional[Literal["pro", "con"]]                  # 질문 대상 에이전트

    # ── 성능 측정 ─────────────────────────────────────────────────────────────
    last_speech_time_ms: int   # 마지막 AI 발언 생성 시간 (ms, LLM 호출 구간만)


# ── 초기 상태 헬퍼 ──────────────────────────────────────────────────────────
def make_initial_state(
    debate_id: int,
    mode: Literal["ai_vs_ai", "ai_vs_user"],
    difficulty: Literal["easy", "hard"],
    policy_card: Dict,
    user_stance: Optional[Literal["pro", "con"]] = None,
) -> DebateState:
    """DebateState 초기값 생성"""
    # PHASE 2: hard + ai_vs_user + user_stance == "pro"
    # → 찬성 유저가 먼저 입장을 직접 작성하므로 첫 발언자를 user로
    first_speaker = (
        "user"
        if (mode == "ai_vs_user" and difficulty == "hard" and user_stance == "pro")
        else "pro"
    )
    return DebateState(
        debate_id=debate_id,
        mode=mode,
        difficulty=difficulty,
        policy_card=policy_card,
        user_stance=user_stance,
        current_stage="position",
        current_round=1,
        current_turn_step="argument",
        current_speaker=first_speaker,
        position_pro_done=False,
        position_con_done=False,
        messages=[],
        pending_speech=None,
        pending_sources=None,
        bias_retry_count=0,
        evidence_retry_count=0,
        bias_check_passed=False,
        evidence_check_passed=False,
        regenerate_speaker=None,
        pro_extra_count=0,
        con_extra_count=0,
        pro_used_arguments=[],
        con_used_arguments=[],
        user_input=None,
        user_input_valid=None,
        user_input_warning=None,
        user_input_violation=None,
        user_action=None,
        question_target=None,
        last_speech_time_ms=0,
    )
