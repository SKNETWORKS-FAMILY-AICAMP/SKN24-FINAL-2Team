"""
debate_agent/graph.py
LangGraph StateGraph 조립 + SQLite Checkpoint 설정

사용 예시:
    from pipelines.debate_agent import build_debate_graph, make_initial_state

    graph, tools = build_debate_graph(
        vector_client=qdrant_client,
        openai_client=openai_client,
        engine=engine,
        checkpoint_path="./output/debate_checkpoint.db",
    )

    # AI vs AI 토론 시작
    state = make_initial_state(
        debate_id=1, mode="ai_vs_ai", difficulty="hard", policy_card=POLICY_CARD
    )
    config = {"configurable": {"thread_id": str(state["debate_id"])}}
    result = graph.invoke(state, config=config)

    # AI vs User 토론 (interrupt 활용)
    state = make_initial_state(
        debate_id=2, mode="ai_vs_user", difficulty="easy",
        policy_card=POLICY_CARD, user_stance="pro"
    )
    config = {"configurable": {"thread_id": "debate_2"}}

    # 그래프 실행 → user_turn 또는 user_choice에서 interrupt
    snapshot = graph.invoke(state, config=config)

    # 사용자 입력 주입 후 재개
    graph.update_state(config, {"user_input": "청년 주거비가 너무 높습니다."})
    snapshot = graph.invoke(None, config=config)
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from openai import OpenAI

from langgraph.graph import END, StateGraph

# langgraph 버전에 따라 checkpoint 위치가 다름
try:
    from langgraph.checkpoint.sqlite import SqliteSaver
except ImportError:
    try:
        from langgraph.checkpoint.sqlite.sync import SqliteSaver  # type: ignore
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver as SqliteSaver  # type: ignore[assignment]

from .nodes import (
    after_bias_check,
    after_evidence_check,
    after_user_choice,
    after_user_turn,
    make_nodes,
    route_decision,
)
from .state import DebateState, make_initial_state
from .tools import (
    ConAgent,
    DebateTools,
    ProAgent,
    ReviewAgent,
    SummaryAgent,
    UserFilterTools,
)


def build_debate_graph(
    vector_client,
    openai_client: OpenAI,
    engine=None,
    model_key: str = "large",
    strategy: str = "sentence",
    llm_model: str = "gpt-4o-mini",
    checkpoint_path: str = "./output/debate_checkpoint.db",
    checkpointer=None,  # 외부 checkpointer 주입 (AsyncSqliteSaver 등)
):
    """
    토론 멀티에이전트 그래프를 빌드해서 반환.

    Parameters
    ----------
    vector_client    : QdrantClient 또는 chromadb.ClientAPI
    openai_client    : OpenAI 클라이언트
    engine           : SQLAlchemy engine (None이면 RDB 저장 스킵)
    model_key        : 임베딩 모델 키
    strategy         : 청킹 전략 (현재 미사용, 하위 호환용)
    llm_model        : LLM 모델명
    checkpoint_path  : SQLite checkpoint 파일 경로

    Returns
    -------
    (compiled_graph, debate_tools)   — debate_tools는 하위 호환용 DebateTools 인스턴스
    """

    # ── 에이전트 생성 ────────────────────────────────────────────────────
    pro_agent     = ProAgent(openai_client, vector_client, model_key, llm_model)
    con_agent     = ConAgent(openai_client, vector_client, model_key, llm_model)
    review_agent  = ReviewAgent(openai_client, llm_model)
    summary_agent = SummaryAgent(openai_client, llm_model)
    user_filter   = UserFilterTools(openai_client, vector_client, llm_model)

    nodes = make_nodes(pro_agent, con_agent, review_agent, summary_agent, user_filter, engine)

    # 하위 호환용 (반환값으로 debate_tools를 사용하는 기존 코드 대비)
    debate_tools = DebateTools(
        vector_client=vector_client,
        openai_client=openai_client,
        model_key=model_key,
        llm_model=llm_model,
    )

    # ── 그래프 정의 ──────────────────────────────────────────────────────
    graph = StateGraph(DebateState)

    # 노드 등록
    graph.add_node("route",          nodes["route"])
    graph.add_node("pro_speech",     nodes["pro_speech"])
    graph.add_node("con_speech",     nodes["con_speech"])
    graph.add_node("bias_check",     nodes["bias_check"])
    graph.add_node("evidence_check", nodes["evidence_check"])
    graph.add_node("save_message",   nodes["save_message"])
    graph.add_node("user_turn",      nodes["user_turn"])
    graph.add_node("user_choice",    nodes["user_choice"])
    graph.add_node("summary",        nodes["summary"])

    # ── 엣지 연결 ────────────────────────────────────────────────────────

    # 진입점
    graph.set_entry_point("route")

    # route → 각 노드 (조건부)
    graph.add_conditional_edges(
        "route",
        route_decision,
        {
            "pro_speech":  "pro_speech",
            "con_speech":  "con_speech",
            "user_turn":   "user_turn",
            "user_choice": "user_choice",
            "summary":     "summary",
            "END":         END,
        },
    )

    # AI 발언 → 편향검토 → 근거확인 → 저장
    graph.add_edge("pro_speech", "bias_check")
    graph.add_edge("con_speech", "bias_check")

    graph.add_conditional_edges(
        "bias_check",
        after_bias_check,
        {
            "evidence_check": "evidence_check",
            "pro_speech":     "pro_speech",
            "con_speech":     "con_speech",
        },
    )

    graph.add_conditional_edges(
        "evidence_check",
        after_evidence_check,
        {
            "save_message": "save_message",
            "pro_speech":   "pro_speech",
            "con_speech":   "con_speech",
        },
    )

    # 저장 완료 → route (다음 턴 결정)
    graph.add_edge("save_message", "route")

    # 사용자 발언 → route
    graph.add_conditional_edges(
        "user_turn",
        after_user_turn,
        {
            "user_turn": "user_turn",   # 재입력 대기
            "route":     "route",
        },
    )

    # 사용자 선택 → 분기
    graph.add_conditional_edges(
        "user_choice",
        after_user_choice,
        {
            "route":   "route",
            "summary": "summary",
            "END":     END,
        },
    )

    # 주장 다지기 → 종료
    graph.add_edge("summary", END)

    # ── Checkpoint 설정 ──────────────────────────────────────────────────
    if checkpointer is None:
        Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(checkpoint_path, check_same_thread=False)
        checkpointer = SqliteSaver(conn)

    # ── 컴파일 ──────────────────────────────────────────────────────────
    compiled = graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["user_turn", "user_choice"],  # 사용자 입력 필요 지점에서 중단
    )

    return compiled, debate_tools
