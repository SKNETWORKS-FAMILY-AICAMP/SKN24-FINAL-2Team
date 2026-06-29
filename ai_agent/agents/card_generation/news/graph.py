import logging
from typing import Dict, List, Optional

from openai import OpenAI
from qdrant_client import QdrantClient
from langgraph.graph import StateGraph, END, START
from langgraph.types import Send

from .state import NewsCardState
from .nodes import NodeManager

logger = logging.getLogger(__name__)


class NewsCardGenerator:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        openai_client: OpenAI,
    ):
        self.node_manager = NodeManager(qdrant_client, openai_client)
        self.graph = self._build_graph()

    def run(
        self,
        articles: List[Dict],
        save: bool = False,
    ) -> Optional[Dict]:
        initial: NewsCardState = {
            "articles": articles,
            "extracted_facts": [],
            "summary": {},
            "debate_topic": "",
            "parallel_results": [],
            "bias_log": {},
            "bias_skip_card": False,
            "save_to_db": save,
            "card_data": None,
            "card_id": None,
            "error": None,
        }
        result = self.graph.invoke(initial)

        if result.get("bias_skip_card") or result.get("error") or not result.get("card_data"):
            return None

        card_data = result.get("card_data")
        return {
            "card_id": result.get("card_id"),
            "card_type": "NEWS",
            "title": card_data.get("title", ""),
            "intro":  card_data.get("intro", ""),
            "debate_topic": card_data.get("debate_topic", ""),
            "tabs": card_data.get("tabs", {}),
        }

    def _build_graph(self) -> StateGraph:
        g  = StateGraph(NewsCardState)
        nm = self.node_manager

        g.add_node("extract_facts", nm.extract_facts_node)
        g.add_node("generate_summary", nm.generate_summary_node)
        g.add_node("generate_debate_topic", nm.generate_debate_topic_node)
        g.add_node("run_core_branch", nm.run_core_branch)
        g.add_node("run_perspectives_branch", nm.run_perspectives_branch)
        g.add_node("assemble", nm.assemble_node)
        g.add_node("save", nm.save_node)

        g.add_edge(START, "extract_facts")

        g.add_conditional_edges(
            "extract_facts",
            lambda s: END if s.get("error") else "generate_summary",
        )

        # generate_summary 완료 후 4개 브랜치 동시 팬아웃
        g.add_conditional_edges(
            "generate_summary",
            lambda s: [
                Send("generate_debate_topic", {**s, "_branch": "debate"}),
                Send("run_core_branch", {**s, "_branch": "core"}),
                Send("run_perspectives_branch", {**s, "_branch": "perspectives"}),
            ],
        )

        # 모든 브랜치 → assemble
        g.add_edge("generate_debate_topic", "assemble")
        g.add_edge("run_core_branch", "assemble")
        g.add_edge("run_perspectives_branch", "assemble")

        g.add_conditional_edges(
            "assemble",
            lambda s: "save" if s.get("save_to_db") and not s.get("error") else END,
        )
        g.add_edge("save", END)

        return g.compile()