"""
pipeline/pipelines/policy_card/graph.py

변경사항:
  - evaluate_core, regenerate_core, evaluate_pros, regenerate_pros 노드 제거
  - 메인 그래프가 선형에 가깝게 단순화:
    extract_facts → generate_summary → generate_core → generate_pros → generate_cons → assemble → save
  - 품질/편향 판단은 각 팀 서브그래프 Supervisor가 담당
  - _route_core의 skip 미사용 버그 수정
"""
import logging
from typing import Dict, List, Optional

from sqlalchemy import Engine
import chromadb
from openai import OpenAI
from langgraph.graph import StateGraph, END, START

from .state import PolicyCardState
from .nodes import NodeManager

logger = logging.getLogger(__name__)


class PolicyCardGenerator:
    def __init__(
        self,
        engine: Engine,
        chroma: chromadb.ClientAPI,
        openai_client: OpenAI,
        model_key: str = "large",
    ):
        self.node_manager = NodeManager(engine, chroma, openai_client, model_key)
        self.graph        = self._build_graph()

    def run(
        self,
        source: Dict,
        related_articles: Optional[List[Dict]] = None,
        card_type: str = "POLICY",
        save: bool = False,
    ) -> Optional[Dict]:
        initial: PolicyCardState = {
            "source":                source,
            "related_articles":      related_articles or [],
            "card_type":             card_type,
            "policy_facts":          {},
            "article_facts":         [],
            "summary":               {},
            "core_content":          "",
            "discussion_question":   "",
            "core_retry":            0,
            "core_passed":           False,
            "core_feedback":         "",
            "perspectives":          [],
            "perspectives_retry":    0,
            "perspectives_passed":   False,
            "perspectives_feedback": "",
            "bias_log":              {},
            "bias_skip_card":        False,
            "save_to_db":            save,
            "card_data":             None,
            "card_id":               None,
            "error":                 None,
        }
        result = self.graph.invoke(initial)

        if result.get("bias_skip_card") or result.get("error") or not result.get("card_data"):
            return None

        return {
            "card_id":   result.get("card_id"),
            "card_type": card_type,
            "tabs":      result.get("card_data"),
            "title":     result.get("card_data", {}).get("SUMMARY", {}).get("title", ""),
        }

    def _build_graph(self) -> StateGraph:
        g  = StateGraph(PolicyCardState)
        nm = self.node_manager

        # ── 노드 등록 ────────────────────────────────────────────────────────
        # evaluate_* / regenerate_* 노드 없음 — Supervisor가 내부에서 처리
        g.add_node("extract_facts",     nm.extract_facts_node)
        g.add_node("generate_summary",  nm.generate_summary_node)
        g.add_node("generate_core",     nm.generate_core_node)
        g.add_node("generate_pros",     nm.generate_pros_node)
        g.add_node("generate_cons",     nm.generate_cons_node)
        g.add_node("assemble",          nm.assemble_node)
        g.add_node("save",              nm.save_node)

        # ── 엣지 연결 ────────────────────────────────────────────────────────
        g.add_edge(START, "extract_facts")

        # 사실 추출 실패 시 즉시 종료
        g.add_conditional_edges(
            "extract_facts",
            lambda s: END if s.get("error") else "generate_summary",
        )

        # 이후는 선형 흐름 — 품질/편향 루프는 서브그래프 내부에서 처리
        g.add_edge("generate_summary", "generate_core")
        g.add_edge("generate_core",    "generate_pros")
        g.add_edge("generate_pros",    "generate_cons")
        g.add_edge("generate_cons",    "assemble")

        # 저장 여부 분기
        g.add_conditional_edges(
            "assemble",
            lambda s: "save" if s.get("save_to_db") and not s.get("error") else END,
        )
        g.add_edge("save", END)

        return g.compile()