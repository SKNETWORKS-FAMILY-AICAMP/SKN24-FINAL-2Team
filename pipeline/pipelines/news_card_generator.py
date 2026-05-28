"""
pipeline/pipelines/news_card_generator.py
뉴스 카드 생성기 — 멀티에이전트 (LangGraph)

[변경 사항 — team2_final 기준]
  기존: card_generation.py의 _generate_news_card() 단일 함수
        → 편향 검사가 맨 끝 1회, 재생성 없음

  개선: LangGraph 멀티에이전트
        extract_facts → generate_summary → generate_core → check_bias_core →
        generate_perspectives → check_bias_perspectives → assemble → save

[team2_final 연동]
  - config.py : LLM_MODEL, LLM_MODEL_FAST, MAX_ARTICLES_PER_CARD 그대로 사용
  - utils.py  : llm() 그대로 사용
  - db/rdb.py : save_card(), save_bias_log() 그대로 사용
  - DB 탭 키: SUMMARY / CORE / OPINION / SOURCE (기존 스키마 유지)
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, TypedDict

from sqlalchemy import Engine
import chromadb
from openai import OpenAI

from langgraph.graph import StateGraph, END, START

from config import LLM_MODEL, LLM_MODEL_FAST, MAX_ARTICLES_PER_CARD
from utils import llm
from db.rdb import save_card, save_bias_log
try:
    from db.vectordb import upsert_card as _chroma_upsert_card
    _HAS_CHROMA = True
except Exception:
    _HAS_CHROMA = False

try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', 'embedding_hf'))
    from vectordb_qdrant import upsert_card as _qdrant_upsert_card
    _HAS_QDRANT = True
except Exception:
    _HAS_QDRANT = False

logger = logging.getLogger(__name__)

MAX_RETRY = 1


# ══════════════════════════════════════════════════════════════════════════════
# State
# ══════════════════════════════════════════════════════════════════════════════

class NewsCardState(TypedDict, total=False):
    # ── 입력 ──────────────────────────────────────────────────────────────────
    articles: List[Dict]

    # ── 사실 추출 ─────────────────────────────────────────────────────────────
    extracted_facts: List[Dict]

    # ── SUMMARY 탭 ────────────────────────────────────────────────────────────
    summary: Dict

    # ── CORE 탭 (탭별 편향 검토) ─────────────────────────────────────────────
    core_content:     str
    core_retry:       int
    core_bias_passed: bool

    # ── OPINION 탭 (탭별 편향 검토) ──────────────────────────────────────────
    perspectives:             List[Dict]
    perspectives_retry:       int
    perspectives_bias_passed: bool

    # ── 편향 로그 ─────────────────────────────────────────────────────────────
    bias_log: Dict

    # ── 제어 ──────────────────────────────────────────────────────────────────
    bias_skip_card: bool
    save_to_db:     bool

    # ── 최종 결과 ─────────────────────────────────────────────────────────────
    card_data: Optional[Dict]
    card_id:   Optional[int]
    error:     Optional[str]


# ══════════════════════════════════════════════════════════════════════════════
# 프롬프트
# ══════════════════════════════════════════════════════════════════════════════

_EXTRACT_PROMPT = """\
다음 기사에서 카드 작성에 필요한 핵심 사실을 추출하세요.
기사에 명시된 내용만 추출하고, 추측하거나 창작하지 마세요.

[기사]
언론사: {publisher}
제목: {title}
URL: {url}
본문: {content}

JSON 응답:
{{
  "publisher":  "언론사명",
  "title":      "기사 제목",
  "url":        "URL",
  "main_claim": "핵심 주장 (2-3문장)",
  "dates":      ["언급된 날짜/기간"],
  "numbers":    ["언급된 수치/통계 — 단위 포함"],
  "entities":   ["언급된 기관명/인물명/정책명"],
  "key_points": ["핵심 포인트 3-5개"],
  "stance":     "이 언론사의 논조 (1-2문장, 없으면 '중립')"
}}"""

_SUMMARY_PROMPT = """\
다음 기사 사실 시트를 바탕으로 SUMMARY 탭을 JSON으로 작성하세요.

[기사별 핵심 사실]
{extracted_facts}

[절대 원칙] 위 사실에 없는 내용 창작/추측 금지.

JSON 응답:
{{
  "title":            "카드 제목 (30자 이내, 청년 친화적)",
  "category":         "일자리|교육|주거|금융|생활복지|문화 중 하나",
  "summary_points":   ["핵심 요약 1", "핵심 요약 2", "... (3-5개)"],
  "youth_connection": "청년 일상과의 연관성 (3-4문장, 구체적으로)"
}}"""

_CORE_PROMPT = """\
다음 기사 사실 시트를 바탕으로 CORE 탭(본문)을 작성하세요.

[기사별 핵심 사실]
{extracted_facts}

[절대 원칙] 위 사실(dates/numbers/entities/key_points)에 명시된 내용만 사용.

[CORE 작성 기준]
- 분량: 1000자 이상 ~ 최대 5000자
- 구성:
  ① 이슈가 생긴 사회적 맥락과 배경 — 왜 지금 이게 중요한가?
  ② 현재 상황과 핵심 쟁점
  ③ 이해관계자별 입장 — 실제 단체/기관을 주어로
  ④ 생각해볼 것: 이 이슈의 핵심 쟁점을 담은 질문 하나
- dates·numbers·entities 데이터 적극 활용해 구체성 확보
- 청년도 이해할 수 있는 대화체, 전문 용어는 괄호로 설명

JSON 응답:
{{
  "core_content":        "본문 전문 (1000자 이상)",
  "discussion_question": "토론으로 이어질 핵심 질문 (1-2문장)"
}}"""

_PERSPECTIVES_PROMPT = """\
다음 언론사별 기사 사실 시트를 바탕으로 OPINION 탭을 JSON으로 작성하세요.

{extracted_facts_by_media}

[절대 원칙]
1. 각 언론사 입장은 해당 언론사 사실 시트 내용만 바탕으로 작성.
2. 사실 시트에 없는 내용 창작/추측 금지.
3. 특정 정당·정치인 지지/비방 표현 금지.
4. "media" 값은 반드시 해당 언론사 publisher 값 그대로.

[작성 지침]
- 각 언론사 입장 200자 이상, 최소 2개 언론사 포함

JSON 응답:
{{
  "perspectives": [
    {{"media": "언론사명", "stance": "입장 (200자 이상)"}},
    {{"media": "언론사명", "stance": "입장 (200자 이상)"}}
  ],
  "sources": [
    {{"media": "언론사명", "url": "원문링크"}}
  ]
}}"""

_BIAS_PROMPT = """\
다음 콘텐츠에 특정 정당·후보·정치인을 지지하거나 비방하는 표현이 있는지 검토하세요.

[콘텐츠]
{content}

JSON 응답:
{{
  "has_bias":      true 또는 false,
  "detected_text": "감지된 편향 표현 (없으면 빈 문자열)",
  "corrected":     "수정된 내용 (편향 없으면 원문 그대로)"
}}"""


# ══════════════════════════════════════════════════════════════════════════════
# NewsCardGenerator
# ══════════════════════════════════════════════════════════════════════════════

class NewsCardGenerator:
    """
    뉴스 카드 생성기 — LangGraph 멀티에이전트

    team2_final card_generation.py에서 호출 방법:
        gen = NewsCardGenerator(engine, chroma, client, model_key="large")
        result = gen.run(articles, save=True)
        # result = {"card_id": int, "card_type": "NEWS", "tabs": dict, "title": str}
    """

    def __init__(
        self,
        engine: Engine,
        chroma: chromadb.ClientAPI,
        openai_client: OpenAI,
        model_key: str = "large",
    ):
        self.engine    = engine
        self.chroma    = chroma
        self.client    = openai_client
        self.model_key = model_key
        self.graph     = self._build_graph()

    # ─── 공개 인터페이스 ──────────────────────────────────────────────────────

    def run(
        self,
        articles: List[Dict],
        save: bool = False,
    ) -> Optional[Dict]:
        initial: NewsCardState = {
            "articles":               articles,
            "extracted_facts":        [],
            "summary":                {},
            "core_content":           "",
            "core_retry":             0,
            "core_bias_passed":       False,
            "perspectives":           [],
            "perspectives_retry":     0,
            "perspectives_bias_passed": False,
            "bias_log":               {},
            "bias_skip_card":         False,
            "save_to_db":             save,
            "card_data":              None,
            "card_id":                None,
            "error":                  None,
        }
        result = self.graph.invoke(initial)

        if result.get("bias_skip_card") or result.get("error"):
            return None

        card_data = result.get("card_data")
        if not card_data:
            return None

        return {
            "card_id":   result.get("card_id"),
            "card_type": "NEWS",
            "tabs":      card_data,
            "title":     card_data.get("SUMMARY", {}).get("title", ""),
        }

    # ─── 그래프 빌드 ──────────────────────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        g = StateGraph(NewsCardState)

        g.add_node("extract_facts",           self._extract_facts_node)
        g.add_node("generate_summary",        self._generate_summary_node)
        g.add_node("generate_core",           self._generate_core_node)
        g.add_node("check_bias_core",         self._check_bias_core_node)
        g.add_node("regenerate_core",         self._regenerate_core_node)
        g.add_node("generate_perspectives",   self._generate_perspectives_node)
        g.add_node("check_bias_perspectives", self._check_bias_perspectives_node)
        g.add_node("regenerate_perspectives", self._regenerate_perspectives_node)
        g.add_node("assemble",                self._assemble_node)
        g.add_node("save",                    self._save_node)

        g.add_edge(START, "extract_facts")
        g.add_conditional_edges(
            "extract_facts",
            lambda s: "end" if s.get("error") else "generate_summary",
            {"generate_summary": "generate_summary", "end": END},
        )
        g.add_edge("generate_summary", "generate_core")
        g.add_edge("generate_core",    "check_bias_core")

        g.add_conditional_edges(
            "check_bias_core",
            self._route_core_bias,
            {"ok": "generate_perspectives", "retry": "regenerate_core", "skip": END},
        )
        g.add_edge("regenerate_core",        "check_bias_core")
        g.add_edge("generate_perspectives",  "check_bias_perspectives")

        g.add_conditional_edges(
            "check_bias_perspectives",
            self._route_perspectives_bias,
            {"ok": "assemble", "retry": "regenerate_perspectives", "skip": END},
        )
        g.add_edge("regenerate_perspectives", "check_bias_perspectives")
        g.add_conditional_edges(
            "assemble",
            lambda s: "save" if s.get("save_to_db") and not s.get("error") else "end",
            {"save": "save", "end": END},
        )
        g.add_edge("save", END)

        return g.compile()

    # ─── 라우팅 ───────────────────────────────────────────────────────────────

    def _route_core_bias(self, state: NewsCardState) -> str:
        if state.get("core_bias_passed"):
            return "ok"
        if state.get("core_retry", 0) < MAX_RETRY:
            return "retry"
        logger.warning("CORE 편향 재생성 후에도 통과 실패 → 카드 스킵")
        return "skip"

    def _route_perspectives_bias(self, state: NewsCardState) -> str:
        if state.get("perspectives_bias_passed"):
            return "ok"
        if state.get("perspectives_retry", 0) < MAX_RETRY:
            return "retry"
        logger.warning("PERSPECTIVES 편향 재생성 후에도 통과 실패 → 카드 스킵")
        return "skip"

    # ─── 노드: 사실 추출 ──────────────────────────────────────────────────────

    def _extract_facts_node(self, state: NewsCardState) -> Dict:
        articles  = (state.get("articles") or [])[:MAX_ARTICLES_PER_CARD]
        extracted = []

        for art in articles:
            try:
                raw = llm(
                    [{"role": "user", "content": _EXTRACT_PROMPT.format(
                        publisher = art.get("press", art.get("publisher", "")),
                        title     = art.get("title", ""),
                        url       = art.get("url", ""),
                        content   = art.get("content", "")[:3000],
                    )}],
                    self.client, model=LLM_MODEL_FAST, max_tokens=800, json_mode=True,
                )
                extracted.append(json.loads(raw))
            except Exception as e:
                logger.warning(f"기사 추출 실패 ({art.get('title','')[:30]}): {e}")

        if len(extracted) < 2:
            return {"extracted_facts": extracted, "error": f"사실 추출 부족: {len(extracted)}건"}

        logger.info(f"  [extract_facts] {len(extracted)}개 기사 사실 추출")
        return {"extracted_facts": extracted}

    # ─── 노드: SUMMARY 생성 ───────────────────────────────────────────────────

    def _generate_summary_node(self, state: NewsCardState) -> Dict:
        facts_text = json.dumps(state["extracted_facts"], ensure_ascii=False, indent=2)
        try:
            raw  = llm(
                [{"role": "user", "content": _SUMMARY_PROMPT.format(extracted_facts=facts_text)}],
                self.client, model=LLM_MODEL, max_tokens=600, json_mode=True,
            )
            data = json.loads(raw)
            logger.info(f"  [generate_summary] title='{data.get('title','')}'")
            return {"summary": data}
        except Exception as e:
            logger.warning(f"SUMMARY 생성 실패: {e}")
            return {"summary": {}}

    # ─── 노드: CORE 생성 ──────────────────────────────────────────────────────

    def _generate_core_node(self, state: NewsCardState) -> Dict:
        return self._do_generate_core(state)

    def _regenerate_core_node(self, state: NewsCardState) -> Dict:
        logger.info("  [regenerate_core] 편향 감지 → 재생성")
        result = self._do_generate_core(state)
        result["core_retry"] = state.get("core_retry", 0) + 1
        return result

    def _do_generate_core(self, state: NewsCardState) -> Dict:
        facts_text   = json.dumps(state.get("extracted_facts", []), ensure_ascii=False, indent=2)
        summary_pts  = "\n".join(f"- {p}" for p in state.get("summary", {}).get("summary_points", []))
        try:
            raw  = llm(
                [{"role": "user", "content": _CORE_PROMPT.format(
                    extracted_facts=facts_text,
                    summary_points=summary_pts,
                )}],
                self.client, model=LLM_MODEL, max_tokens=3000, json_mode=True,
            )
            data = json.loads(raw)
            core = data.get("core_content", "")
            logger.info(f"  [generate_core] {len(core)}자")
            return {
                "core_content":        core,
                "discussion_question": data.get("discussion_question", ""),
            }
        except Exception as e:
            logger.warning(f"CORE 생성 실패: {e}")
            return {"core_content": "", "discussion_question": ""}

    # ─── 노드: CORE 편향 검토 ────────────────────────────────────────────────

    def _check_bias_core_node(self, state: NewsCardState) -> Dict:
        passed, log_entry = self._check_bias(state.get("core_content", ""), "CORE")
        bias_log = dict(state.get("bias_log", {}))
        bias_log["CORE"] = log_entry
        logger.info(f"  [check_bias_core] {'통과' if passed else '편향 감지'}")
        return {"core_bias_passed": passed, "bias_log": bias_log}

    # ─── 노드: PERSPECTIVES 생성 ──────────────────────────────────────────────

    def _generate_perspectives_node(self, state: NewsCardState) -> Dict:
        return self._do_generate_perspectives(state)

    def _regenerate_perspectives_node(self, state: NewsCardState) -> Dict:
        logger.info("  [regenerate_perspectives] 편향 감지 → 재생성")
        result = self._do_generate_perspectives(state)
        result["perspectives_retry"] = state.get("perspectives_retry", 0) + 1
        return result

    def _do_generate_perspectives(self, state: NewsCardState) -> Dict:
        # 언론사별로 헤더 붙여서 분리 (cross-contamination 방지)
        parts = []
        for i, fact in enumerate(state.get("extracted_facts", []), 1):
            pub = fact.get("publisher", f"언론사{i}")
            parts.append(f"[기사 {i} | {pub}]\n{json.dumps(fact, ensure_ascii=False, indent=2)}")
        facts_by_media = "\n\n---\n\n".join(parts)

        try:
            raw  = llm(
                [{"role": "user", "content": _PERSPECTIVES_PROMPT.format(
                    extracted_facts_by_media=facts_by_media,
                )}],
                self.client, model=LLM_MODEL, max_tokens=1200, json_mode=True,
            )
            data = json.loads(raw)
            pvs  = data.get("perspectives", [])
            logger.info(f"  [generate_perspectives] {len(pvs)}개 언론사")
            return {
                "perspectives": pvs,
                "sources":      data.get("sources", []),
            }
        except Exception as e:
            logger.warning(f"PERSPECTIVES 생성 실패: {e}")
            return {"perspectives": [], "sources": []}

    # ─── 노드: PERSPECTIVES 편향 검토 ────────────────────────────────────────

    def _check_bias_perspectives_node(self, state: NewsCardState) -> Dict:
        pvs_str = json.dumps(state.get("perspectives", []), ensure_ascii=False)
        passed, log_entry = self._check_bias(pvs_str, "OPINION")
        bias_log = dict(state.get("bias_log", {}))
        bias_log["OPINION"] = log_entry
        logger.info(f"  [check_bias_perspectives] {'통과' if passed else '편향 감지'}")
        return {"perspectives_bias_passed": passed, "bias_log": bias_log}

    # ─── 노드: 조립 ───────────────────────────────────────────────────────────

    def _assemble_node(self, state: NewsCardState) -> Dict:
        """team2_final DB 탭 구조: SUMMARY / CORE / OPINION / SOURCE"""
        summary = state.get("summary", {})
        if state.get("discussion_question"):
            summary["discussion_question"] = state["discussion_question"]

        card_data = {
            "SUMMARY": summary,
            "CORE":    state.get("core_content", ""),
            "OPINION": state.get("perspectives", []),
            "SOURCE":  state.get("sources", []),
        }
        logger.info(f"  [assemble] 카드 조립 완료: '{summary.get('title', '')}'")
        return {"card_data": card_data}

    # ─── 노드: DB 저장 ────────────────────────────────────────────────────────

    def _save_node(self, state: NewsCardState) -> Dict:
        card_data = state.get("card_data", {})
        if not card_data:
            return {"error": "card_data 없음"}

        article_ids = [
            a.get("id", a.get("data_id"))
            for a in (state.get("articles") or [])
            if a.get("id") or a.get("data_id")
        ]

        serialized = {
            k: (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
            for k, v in card_data.items()
        }

        try:
            card_id = save_card(self.engine, "NEWS", serialized, article_ids)
        except Exception as e:
            logger.error(f"save_card 실패: {e}")
            return {"error": str(e)}

        for tab_type, log_entry in state.get("bias_log", {}).items():
            is_det, det_text, action = log_entry
            save_bias_log(self.engine, card_id, tab_type, is_det, det_text, action)

        if self.chroma is not None:
            try:
                from qdrant_client import QdrantClient as _QC
                if _HAS_QDRANT and isinstance(self.chroma, _QC):
                    _qdrant_upsert_card(self.chroma, card_id, card_data, "NEWS")
                elif _HAS_CHROMA:
                    _chroma_upsert_card(self.chroma, self.client, card_id, serialized, self.model_key, "NEWS")
            except Exception as e:
                _log(f"⚠️  [save] VectorDB upsert 실패 (무시): {e}")

        logger.info(f"  [save] 뉴스 카드 #{card_id} 저장 완료")
        return {"card_id": card_id}

    # ─── 편향 검사 헬퍼 ───────────────────────────────────────────────────────

    def _check_bias(
        self, content: str, tab_type: str
    ) -> tuple[bool, tuple[bool, str, Optional[str]]]:
        try:
            raw = llm(
                [{"role": "user", "content": _BIAS_PROMPT.format(content=content[:3000])}],
                self.client, model=LLM_MODEL_FAST, max_tokens=800, json_mode=True,
            )
            result   = json.loads(raw)
            is_det   = result.get("has_bias", False)
            det_text = result.get("detected_text", "")
            action   = "DETECTED" if is_det else None
        except Exception:
            is_det, det_text, action = False, "", None

        return not is_det, (is_det, det_text, action)
