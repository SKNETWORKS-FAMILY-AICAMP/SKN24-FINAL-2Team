"""
pipeline/pipelines/policy_card/nodes.py

변경사항:
  - evaluate_core_node, regenerate_core_node 제거 → Core 팀 Supervisor에 통합
  - evaluate_pros_node, regenerate_pros_node 제거 → Pros 팀 Supervisor에 통합
  - _core_supervisor except 블록 NameError 버그 수정
  - _EVALUATE_PERSPECTIVES_PROMPT NameError 버그 수정
  - 찬성 팀을 Core/Cons 팀과 동일한 Supervisor 패턴으로 통일
  - search_context를 _CORE_PROMPT, _CONS_PROMPT에 전달
"""
import json
import logging
from typing import Dict, TypedDict

from langgraph.graph import StateGraph, END, START

from config import LLM_MODEL, LLM_MODEL_FAST
from utils import llm
from db.rdb import save_card, save_bias_log
try:
    from db.vectordb import upsert_card as _chroma_upsert_card
    _HAS_CHROMA = True
except Exception:
    _HAS_CHROMA = False

try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', '..', 'embedding_hf'))
    from vectordb_qdrant import upsert_card as _qdrant_upsert_card
    _HAS_QDRANT = True
except Exception:
    _HAS_QDRANT = False

from .state import PolicyCardState
from .prompts import (
    _EXTRACT_POLICY_PROMPT, _EXTRACT_ARTICLE_PROMPT,
    _SUMMARY_PROMPT,
    _CORE_PROMPT, _CORE_SUPERVISOR_PROMPT,
    _PROS_PROMPT, _PROS_SUPERVISOR_PROMPT,
    _CONS_PROMPT, _CONS_SUPERVISOR_PROMPT,
)
from .tools import web_search

logger = logging.getLogger(__name__)


def _log(msg: str) -> None:
    """logger + print 동시 출력 — 노트북에서도 실시간으로 보임."""
    logger.info(msg)
    print(msg, flush=True)


# ── 서브그래프 공용 State ──────────────────────────────────────────────────
class SubTeamState(TypedDict, total=False):
    policy_facts:        str
    article_facts:       str
    search_context:      str
    draft:               str
    next_worker:         str
    supervisor_feedback: str
    loop_count:          int


# ══════════════════════════════════════════════════════════════════════════════
# NodeManager
# ══════════════════════════════════════════════════════════════════════════════
class NodeManager:
    def __init__(self, engine, chroma, client, model_key="large"):
        self.engine    = engine
        self.chroma    = chroma
        self.client    = client
        self.model_key = model_key

        # 서브그래프 3개 빌드
        self.core_team = self._build_team(
            supervisor_fn = self._core_supervisor,
            searcher_fn   = self._core_searcher,
            generator_fn  = self._core_generator,
        )
        self.pros_team = self._build_team(
            supervisor_fn = self._pros_supervisor,
            searcher_fn   = None,               # 찬성팀은 검색 없음
            generator_fn  = self._pros_generator,
        )
        self.cons_team = self._build_team(
            supervisor_fn = self._cons_supervisor,
            searcher_fn   = self._cons_searcher,
            generator_fn  = self._cons_generator,
        )

    # ─── 공용 서브그래프 빌더 ─────────────────────────────────────────────────
    def _build_team(self, supervisor_fn, generator_fn, searcher_fn=None) -> StateGraph:
        """
        Supervisor → (SEARCH →) Generator → Supervisor 루프를 만드는 공용 빌더.
        searcher_fn이 None이면 SEARCH 엣지 없이 GENERATE/FINISH만.
        """
        team = StateGraph(SubTeamState)
        team.add_node("Supervisor", supervisor_fn)
        team.add_node("Generator",  generator_fn)

        # START → Generator 먼저: 초안 없이 Supervisor가 FINISH 때리는 문제 방지
        team.add_edge(START, "Generator")
        team.add_edge("Generator", "Supervisor")

        if searcher_fn:
            team.add_node("Searcher", searcher_fn)
            team.add_edge("Searcher", "Supervisor")
            team.add_conditional_edges(
                "Supervisor",
                lambda s: s.get("next_worker", "FINISH"),
                {"SEARCH": "Searcher", "GENERATE": "Generator", "FINISH": END},
            )
        else:
            team.add_conditional_edges(
                "Supervisor",
                lambda s: s.get("next_worker", "FINISH"),
                {"GENERATE": "Generator", "FINISH": END},
            )

        return team.compile()

    # ─── 공용 Supervisor 호출 헬퍼 ───────────────────────────────────────────
    def _call_supervisor(self, prompt: str) -> Dict:
        """Supervisor LLM 호출. 파싱 실패 시 안전하게 FINISH 반환."""
        try:
            raw = llm(
                [{"role": "user", "content": prompt}],
                self.client, model=LLM_MODEL_FAST, max_tokens=300, json_mode=True,
            )
            res = json.loads(raw)
            return {
                "next_worker":         res.get("next_worker", "FINISH"),
                "supervisor_feedback": res.get("feedback", ""),
            }
        except Exception as e:
            _log(f"⚠️  Supervisor 파싱 실패, FINISH로 처리: {e}")
            return {"next_worker": "FINISH", "supervisor_feedback": ""}

    # ══════════════════════════════════════════════════════════════════════════
    # CORE 팀
    # ══════════════════════════════════════════════════════════════════════════
    def _core_supervisor(self, state: SubTeamState) -> Dict:
        if state.get("loop_count", 0) >= 3:
            return {"next_worker": "FINISH", "supervisor_feedback": "최대 반복 도달"}

        prompt = _CORE_SUPERVISOR_PROMPT.format(
            draft          = state.get("draft", "아직 없음"),
            policy_facts   = state.get("policy_facts", ""),
            article_facts  = state.get("article_facts", ""),
            search_context = state.get("search_context", "없음"),
            loop_count     = state.get("loop_count", 0),
        )
        result = self._call_supervisor(prompt)
        _log(f"⚖️  [CORE Supervisor] → {result['next_worker']} | {result['supervisor_feedback'][:60]}")
        return result

    def _core_searcher(self, state: SubTeamState) -> Dict:
        query = state.get("supervisor_feedback", "")
        _log(f"🔎 [CORE Searcher] '{query}'")
        result = web_search(query)
        return {
            "search_context": state.get("search_context", "") + f"\n[검색: {query}]\n{result}",
            "loop_count":     state.get("loop_count", 0) + 1,
        }

    def _core_generator(self, state: SubTeamState) -> Dict:
        prompt = f"[지시사항]: {state.get('supervisor_feedback', '')}\n\n" + \
                 _CORE_PROMPT.format(
                     policy_facts   = state.get("policy_facts", ""),
                     article_facts  = state.get("article_facts", ""),
                     search_context = state.get("search_context", "없음"),
                 )
        try:
            draft = llm(
                [{"role": "user", "content": prompt}],
                self.client, model=LLM_MODEL, max_tokens=3000, json_mode=True,
            )
            _log(f"✍️  [CORE Generator] {len(draft)}자 초안 생성")
            return {"draft": draft, "loop_count": state.get("loop_count", 0) + 1}
        except Exception as e:
            _log(f"⚠️  CORE Generator 실패: {e}")
            return {"draft": "{}", "loop_count": state.get("loop_count", 0) + 1}

    # ══════════════════════════════════════════════════════════════════════════
    # PROS 팀 (찬성 — 검색 없음)
    # ══════════════════════════════════════════════════════════════════════════
    def _pros_supervisor(self, state: SubTeamState) -> Dict:
        if state.get("loop_count", 0) >= 2:
            return {"next_worker": "FINISH", "supervisor_feedback": "최대 반복 도달"}

        prompt = _PROS_SUPERVISOR_PROMPT.format(
            draft      = state.get("draft", "아직 없음"),
            loop_count = state.get("loop_count", 0),
        )
        result = self._call_supervisor(prompt)
        _log(f"⚖️  [PROS Supervisor] → {result['next_worker']} | {result['supervisor_feedback'][:60]}")
        return result

    def _pros_generator(self, state: SubTeamState) -> Dict:
        prompt = f"[지시사항]: {state.get('supervisor_feedback', '')}\n\n" + \
                 _PROS_PROMPT.format(
                     policy_facts  = state.get("policy_facts", ""),
                     article_facts = state.get("article_facts", ""),
                 )
        try:
            draft = llm(
                [{"role": "user", "content": prompt}],
                self.client, model=LLM_MODEL, max_tokens=1000, json_mode=True,
            )
            _log(f"✍️  [PROS Generator] {len(draft)}자 초안 생성")
            return {"draft": draft, "loop_count": state.get("loop_count", 0) + 1}
        except Exception as e:
            _log(f"⚠️  PROS Generator 실패: {e}")
            return {"draft": "{}", "loop_count": state.get("loop_count", 0) + 1}

    # ══════════════════════════════════════════════════════════════════════════
    # CONS 팀 (반대 — 검색 있음)
    # ══════════════════════════════════════════════════════════════════════════
    def _cons_supervisor(self, state: SubTeamState) -> Dict:
        if state.get("loop_count", 0) >= 3:
            return {"next_worker": "FINISH", "supervisor_feedback": "최대 반복 도달"}

        prompt = _CONS_SUPERVISOR_PROMPT.format(
            draft          = state.get("draft", "아직 없음"),
            policy_facts   = state.get("policy_facts", ""),
            search_context = state.get("search_context", "없음"),
            loop_count     = state.get("loop_count", 0),
        )
        result = self._call_supervisor(prompt)
        _log(f"⚖️  [CONS Supervisor] → {result['next_worker']} | {result['supervisor_feedback'][:60]}")
        return result

    def _cons_searcher(self, state: SubTeamState) -> Dict:
        raw_query = state.get("supervisor_feedback", "")
        # Supervisor가 여러 키워드를 따옴표+쉼표로 묶어 반환하는 경우 첫 번째만 사용
        query = raw_query.strip("'\"").split("',")[0].split("\",")[0].strip("'\" ")
        _log(f"🔎 [CONS Searcher] '{query}'")
        result = web_search(query)
        return {
            "search_context": state.get("search_context", "") + f"\n[비판검색: {query}]\n{result}",
            "loop_count":     state.get("loop_count", 0) + 1,
        }

    def _cons_generator(self, state: SubTeamState) -> Dict:
        prompt = f"[지시사항]: {state.get('supervisor_feedback', '')}\n\n" + \
                 _CONS_PROMPT.format(
                     policy_facts   = state.get("policy_facts", ""),
                     article_facts  = state.get("article_facts", ""),
                     search_context = state.get("search_context", "없음"),
                 )
        try:
            draft = llm(
                [{"role": "user", "content": prompt}],
                self.client, model=LLM_MODEL, max_tokens=1500, json_mode=True,
            )
            _log(f"✍️  [CONS Generator] {len(draft)}자 초안 생성")
            return {"draft": draft, "loop_count": state.get("loop_count", 0) + 1}
        except Exception as e:
            _log(f"⚠️  CONS Generator 실패: {e}")
            return {"draft": "{}", "loop_count": state.get("loop_count", 0) + 1}

    # ══════════════════════════════════════════════════════════════════════════
    # 메인 그래프 노드들
    # ══════════════════════════════════════════════════════════════════════════

    def extract_facts_node(self, state: PolicyCardState) -> Dict:
        """정책/법안 원문 + 관련 기사 → 구조화 사실 추출"""
        source   = state.get("source", {})
        doc_text = json.dumps(source, ensure_ascii=False, default=str)[:5000]

        try:
            policy_facts = json.loads(llm(
                [{"role": "user", "content": _EXTRACT_POLICY_PROMPT.format(policy_doc=doc_text)}],
                self.client, model=LLM_MODEL_FAST, max_tokens=1000, json_mode=True,
            ))
        except Exception as e:
            return {"error": f"정책 사실 추출 실패: {e}"}

        article_facts = []
        for art in (state.get("related_articles") or [])[:5]:
            try:
                article_facts.append(json.loads(llm(
                    [{"role": "user", "content": _EXTRACT_ARTICLE_PROMPT.format(
                        publisher = art.get("press", art.get("publisher", "")),
                        title     = art.get("title", ""),
                        content   = art.get("content", "")[:2000],
                    )}],
                    self.client, model=LLM_MODEL_FAST, max_tokens=600, json_mode=True,
                )))
            except Exception:
                pass

        _log(f"📋 [extract_facts] 정책: '{policy_facts.get('name','')}' | 기사: {len(article_facts)}건")
        return {"policy_facts": policy_facts, "article_facts": article_facts}

    def generate_summary_node(self, state: PolicyCardState) -> Dict:
        """SUMMARY 탭 생성"""
        try:
            summary = json.loads(llm(
                [{"role": "user", "content": _SUMMARY_PROMPT.format(
                    policy_facts=json.dumps(state.get("policy_facts", {}), ensure_ascii=False),
                )}],
                self.client, model=LLM_MODEL, max_tokens=800, json_mode=True,
            ))
            _log(f"📝 [generate_summary] title='{summary.get('title','')}'")
            return {"summary": summary}
        except Exception:
            return {"summary": {}}

    def generate_core_node(self, state: PolicyCardState) -> Dict:
        """CORE 탭 — Core 팀 서브그래프 가동"""
        _log("🚀 [generate_core] Core 팀 서브그래프 시작")
        initial: SubTeamState = {
            "policy_facts":        json.dumps(state.get("policy_facts", {}), ensure_ascii=False),
            "article_facts":       json.dumps(state.get("article_facts", []), ensure_ascii=False),
            "search_context":      "",
            "draft":               "",
            "next_worker":         "",
            "supervisor_feedback": "",
            "loop_count":          0,
        }
        final = self.core_team.invoke(initial, {"recursion_limit": 20})
        try:
            data = json.loads(final.get("draft", "{}"))
            core = data.get("core_content", "")
            _log(f"✅ [generate_core] 완료: {len(core)}자")
            return {
                "core_content":        core,
                "discussion_question": data.get("discussion_question", ""),
            }
        except Exception:
            return {"core_content": "", "discussion_question": ""}

    def generate_pros_node(self, state: PolicyCardState) -> Dict:
        """찬성 의견 — Pros 팀 서브그래프 가동"""
        _log("🚀 [generate_pros] Pros 팀 서브그래프 시작")
        initial: SubTeamState = {
            "policy_facts":        json.dumps(state.get("policy_facts", {}), ensure_ascii=False),
            "article_facts":       json.dumps(state.get("article_facts", []), ensure_ascii=False),
            "search_context":      "",
            "draft":               "",
            "next_worker":         "",
            "supervisor_feedback": "",
            "loop_count":          0,
        }
        final = self.pros_team.invoke(initial, {"recursion_limit": 10})
        try:
            data = json.loads(final.get("draft", "{}"))
            argument = data.get("argument", "")
            _log(f"✅ [generate_pros] 완료: {len(argument)}자")
            return {"perspectives": [{"stance": "찬성", "argument": argument}]}
        except Exception:
            return {"perspectives": []}

    def generate_cons_node(self, state: PolicyCardState) -> Dict:
        """반대 의견 — Cons 팀 서브그래프 가동 후 기존 perspectives에 병합"""
        _log("🚀 [generate_cons] Cons 팀 서브그래프 시작")
        initial: SubTeamState = {
            "policy_facts":        json.dumps(state.get("policy_facts", {}), ensure_ascii=False),
            "article_facts":       json.dumps(state.get("article_facts", []), ensure_ascii=False),
            "search_context":      "",
            "draft":               "",
            "next_worker":         "",
            "supervisor_feedback": "",
            "loop_count":          0,
        }
        final = self.cons_team.invoke(initial, {"recursion_limit": 20})
        try:
            data = json.loads(final.get("draft", "{}"))
            argument = data.get("argument", "")
            _log(f"✅ [generate_cons] 완료: {len(argument)}자")
            current = list(state.get("perspectives", []))
            current.append({"stance": "반대", "argument": argument})
            return {"perspectives": current}
        except Exception:
            return {}

    def assemble_node(self, state: PolicyCardState) -> Dict:
        """최종 카드 데이터 조립"""
        summary = dict(state.get("summary", {}))
        if state.get("discussion_question"):
            summary["discussion_question"] = state["discussion_question"]

        card_data = {
            "SUMMARY": summary,
            "CORE":    state.get("core_content", ""),
            "OPINION": state.get("perspectives", []),
            "SOURCE":  {
                "url":  state.get("source", {}).get("url", ""),
                "name": state.get("source", {}).get("name", ""),
            },
        }
        _log(f"🗂️  [assemble] 카드 조립 완료: '{summary.get('title', '')}'")
        return {"card_data": card_data}

    def save_node(self, state: PolicyCardState) -> Dict:
        """RDB + ChromaDB 저장"""
        card_data = state.get("card_data", {})
        if not card_data:
            return {"error": "card_data 없음"}

        source_id  = state.get("source", {}).get("id")
        serialized = {
            k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
            for k, v in card_data.items()
        }
        try:
            card_id = save_card(
                self.engine,
                state.get("card_type", "POLICY"),
                serialized,
                [source_id] if source_id else [],
            )
        except Exception as e:
            return {"error": str(e)}

        for tab_type, log_entry in state.get("bias_log", {}).items():
            is_det, det_text, action = log_entry
            save_bias_log(self.engine, card_id, tab_type, is_det, det_text, action)

        # VectorDB 저장 — None이면 스킵
        if self.chroma is not None:
            try:
                from qdrant_client import QdrantClient as _QC
                if _HAS_QDRANT and isinstance(self.chroma, _QC):
                    _qdrant_upsert_card(
                        self.chroma, card_id, card_data,
                        state.get("card_type", "POLICY"),
                    )
                elif _HAS_CHROMA:
                    _chroma_upsert_card(self.chroma, self.client, card_id, serialized,
                                        self.model_key, state.get("card_type", "POLICY"))
            except Exception as e:
                _log(f"⚠️  [save] VectorDB upsert 실패 (무시): {e}")

        _log(f"💾 [save] {state.get('card_type')} 카드 #{card_id} 저장 완료")
        return {"card_id": card_id}