import logging
from typing import Dict, List, Optional

from openai import OpenAI
from qdrant_client import QdrantClient
from langgraph.graph import StateGraph, END, START
from langgraph.types import Send

from .state import PolicyCardState
from .nodes import NodeManager

logger = logging.getLogger(__name__)


class PolicyCardGenerator:
    def __init__(
        self,
        qdrant_client: QdrantClient,
        openai_client: OpenAI,
        model_key: str = "large",
    ):
        self.node_manager = NodeManager(qdrant_client, openai_client, model_key)
        self.graph = self._build_graph()

    def run(
        self,
        source: Dict,
        related_articles: Optional[List[Dict]] = None,
        related_laws: Optional[List[Dict]] = None,
        card_type: str = "POLICY",
        save: bool = False,
    ) -> Optional[Dict]:
        initial: PolicyCardState = {
            "source": source,
            "related_articles": related_articles or [],
            "related_laws": related_laws or [],
            "card_type": card_type,
            "policy_facts": {},
            "article_facts": [],
            "summary": {},
            "parallel_results": [],
            "bias_log": {},
            "bias_skip_card": False,
            "save_to_db": save,
            "debate_topic": "",
            "card_data": None,
            "card_id": None,
            "error": None,
        }
        result = self.graph.invoke(initial)

        if result.get("bias_skip_card") or result.get("error") or not result.get("card_data"):
            return None

        card_data = result.get("card_data")
        return {
            "card_id":     result.get("card_id"),
            "card_type":   card_type,
            "title":       card_data.get("title", ""),
            "intro":       card_data.get("intro", ""),
            "debate_topic": card_data.get("debate_topic", result.get("debate_topic", "")),
            "tabs":        card_data.get("tabs", {}),
        }

    def _build_graph(self) -> StateGraph:
        g  = StateGraph(PolicyCardState)
        nm = self.node_manager

        g.add_node("extract_facts", nm.extract_facts_node)
        g.add_node("generate_summary", nm.generate_summary_node)
        g.add_node("run_core_branch", nm.run_core_branch)
        g.add_node("run_pro_branch", nm.run_pro_branch)
        g.add_node("run_con_branch", nm.run_con_branch)
        g.add_node("assemble", nm.assemble_node)
        g.add_node("generate_debate_topic", nm.generate_debate_topic_node)
        g.add_node("save", nm.save_node)

        g.add_edge(START, "extract_facts")

        g.add_conditional_edges(
            "extract_facts",
            lambda s: END if s.get("error") else "generate_summary",
        )

        g.add_conditional_edges(
            "generate_summary",
            lambda s: [
                Send("run_core_branch", {**s, "_branch": "core"}),
                Send("run_pro_branch", {**s, "_branch": "pro"}),
                Send("run_con_branch", {**s, "_branch": "con"}),
            ],
        )

        g.add_edge("run_core_branch", "assemble")
        g.add_edge("run_pro_branch", "assemble")
        g.add_edge("run_con_branch", "assemble")

        g.add_conditional_edges(
            "assemble",
            lambda s: END if s.get("bias_skip_card") or s.get("error") else "generate_debate_topic",
        )
        g.add_conditional_edges(
            "generate_debate_topic",
            lambda s: "save" if s.get("save_to_db") and not s.get("error") else END,
        )
        g.add_edge("save", END)

        return g.compile()