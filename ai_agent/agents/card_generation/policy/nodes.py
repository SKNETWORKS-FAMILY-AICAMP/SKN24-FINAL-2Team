"""
agents/card_generation/policy/nodes.py

뉴스 카드 고도화 내용 반영:
  - assemble_node: title/intro 최상위 분리 + tabs 구조 통일
  - run_core_branch: 편향 실패 시 텍스트 로그 + 구체적 피드백
  - graph.run() 반환 구조: title/intro/tabs 통일
  - related_laws 활용: 법령 조문 extract_facts에서 처리
  - 변수명 뉴스 카드 기준으로 통일 (bias_log 키 등)
"""
import json
import logging
from typing import Dict, List, TypedDict

from openai import OpenAI
from qdrant_client import QdrantClient

from config import LLM_MODEL, LLM_MODEL_FAST
from utils import llm
from db.qdrant_upload import upsert_card

from .state import PolicyCardState
from .prompts import (
    EXTRACT_POLICY_PROMPT, EXTRACT_ARTICLE_PROMPT,
    SUMMARY_PROMPT,
    CORE_PART1_PROMPT, CORE_PART2_PROMPT,
    CORE_PART3_PROMPT, CORE_PART4_PROMPT,
    CORE_SUPERVISOR_PROMPT,
    PROS_PROMPT, PROS_SUPERVISOR_PROMPT,
    CONS_PROMPT, CONS_SUPERVISOR_PROMPT,
    DEBATE_TOPIC_PROMPT,
)
from .tools import web_search
from agents.bias_check import BiasClassifier, check_content_bias, check_ai_bias

logger = logging.getLogger(__name__)

MAX_RETRY      = 2   # 편향 검사 재시도 최대 횟수
MAX_SUPERVISOR = 3   # 서브그래프 Supervisor 루프 최대 횟수


class SubTeamState(TypedDict, total=False):
    policy_facts:        str
    article_facts:       str
    search_context:      str
    draft:               str
    next_worker:         str
    supervisor_feedback: str
    loop_count:          int


class NodeManager:
    def __init__(self, qdrant_client: QdrantClient, openai_client: OpenAI,
                 model_key: str = "large"):
        self.qdrant_client = qdrant_client
        self.client        = openai_client
        self.model_key     = model_key

        self.core_team = self._build_team(
            supervisor_fn=self._core_supervisor,
            searcher_fn=self._core_searcher,
            generator_fn=self._core_generator,
        )
        self.pros_team = self._build_team(
            supervisor_fn=self._pros_supervisor,
            searcher_fn=None,
            generator_fn=self._pros_generator,
        )
        self.cons_team = self._build_team(
            supervisor_fn=self._cons_supervisor,
            searcher_fn=self._cons_searcher,
            generator_fn=self._cons_generator,
        )

        self.classifier = BiasClassifier("BarryKim34/kr-electra-political-bias")

    # ── 서브그래프 빌더 ───────────────────────────────────────────────────────
    def _build_team(self, supervisor_fn, generator_fn, searcher_fn=None):
        from langgraph.graph import StateGraph, END, START
        team = StateGraph(SubTeamState)
        team.add_node("Supervisor", supervisor_fn)
        team.add_node("Generator",  generator_fn)
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

    def _call_supervisor(self, prompt: str) -> Dict:
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
            logger.warning(f"[Supervisor] 파싱 실패: {e}")
            return {"next_worker": "FINISH", "supervisor_feedback": ""}

    # ── CORE 서브그래프 ───────────────────────────────────────────────────────
    def _core_supervisor(self, state: SubTeamState) -> Dict:
        if state.get("loop_count", 0) >= MAX_SUPERVISOR:
            return {"next_worker": "FINISH", "supervisor_feedback": "최대 반복 도달"}
        prompt = CORE_SUPERVISOR_PROMPT.format(
            draft          = state.get("draft", "아직 없음"),
            policy_facts   = state.get("policy_facts", ""),
            article_facts  = state.get("article_facts", ""),
            search_context = state.get("search_context", "없음"),
            loop_count     = state.get("loop_count", 0),
        )
        result = self._call_supervisor(prompt)
        logger.info(f"[core_branch] Supervisor → {result['next_worker']} | {result['supervisor_feedback'][:60]}")
        return result

    def _core_searcher(self, state: SubTeamState) -> Dict:
        query = state.get("supervisor_feedback", "")
        logger.info(f"[core_branch] Searcher '{query}'")
        result = web_search(query)
        return {
            "search_context": state.get("search_context", "") + f"\n[검색: {query}]\n{result}",
            "loop_count":     state.get("loop_count", 0) + 1,
        }

    def _core_generator(self, state: SubTeamState) -> Dict:
        feedback_prefix = f"[이전 피드백]: {state['supervisor_feedback']}\n\n" \
            if state.get("supervisor_feedback") else ""
        prompt = feedback_prefix + CORE_PROMPT.format(
            policy_facts   = state.get("policy_facts", ""),
            article_facts  = state.get("article_facts", ""),
            search_context = state.get("search_context", "없음"),
        )
        try:
            draft = llm(
                [{"role": "user", "content": prompt}],
                self.client, model=LLM_MODEL, max_tokens=3000, json_mode=True,
            )
            logger.info(f"[core_branch] Generator {len(draft)}자 생성")
            return {"draft": draft, "loop_count": state.get("loop_count", 0) + 1}
        except Exception as e:
            logger.warning(f"[core_branch] Generator 실패: {e}")
            return {"draft": "{}", "loop_count": state.get("loop_count", 0) + 1}

    # ── PROS 서브그래프 ───────────────────────────────────────────────────────
    def _pros_supervisor(self, state: SubTeamState) -> Dict:
        if state.get("loop_count", 0) >= 2:
            return {"next_worker": "FINISH", "supervisor_feedback": "최대 반복 도달"}
        prompt = PROS_SUPERVISOR_PROMPT.format(
            draft      = state.get("draft", "아직 없음"),
            loop_count = state.get("loop_count", 0),
        )
        result = self._call_supervisor(prompt)
        logger.info(f"[pro_branch] Supervisor → {result['next_worker']} | {result['supervisor_feedback'][:60]}")
        return result

    def _pros_generator(self, state: SubTeamState) -> Dict:
        feedback_prefix = f"[이전 피드백]: {state['supervisor_feedback']}\n\n" \
            if state.get("supervisor_feedback") else ""
        prompt = feedback_prefix + PROS_PROMPT.format(
            policy_facts  = state.get("policy_facts", ""),
            article_facts = state.get("article_facts", ""),
        )
        try:
            draft = llm(
                [{"role": "user", "content": prompt}],
                self.client, model=LLM_MODEL, max_tokens=1000, json_mode=True,
            )
            logger.info(f"[pro_branch] Generator {len(draft)}자 생성")
            return {"draft": draft, "loop_count": state.get("loop_count", 0) + 1}
        except Exception as e:
            logger.warning(f"[pro_branch] Generator 실패: {e}")
            return {"draft": "{}", "loop_count": state.get("loop_count", 0) + 1}

    # ── CONS 서브그래프 ───────────────────────────────────────────────────────
    def _cons_supervisor(self, state: SubTeamState) -> Dict:
        if state.get("loop_count", 0) >= MAX_SUPERVISOR:
            return {"next_worker": "FINISH", "supervisor_feedback": "최대 반복 도달"}
        prompt = CONS_SUPERVISOR_PROMPT.format(
            draft          = state.get("draft", "아직 없음"),
            policy_facts   = state.get("policy_facts", ""),
            search_context = state.get("search_context", "없음"),
            loop_count     = state.get("loop_count", 0),
        )
        result = self._call_supervisor(prompt)
        logger.info(f"[con_branch] Supervisor → {result['next_worker']} | {result['supervisor_feedback'][:60]}")
        return result

    def _cons_searcher(self, state: SubTeamState) -> Dict:
        raw_query = state.get("supervisor_feedback", "")
        query = raw_query.strip("'\"").split("',")[0].split("\",")[0].strip("'\" ")
        logger.info(f"[con_branch] Searcher '{query}'")
        result = web_search(query)
        return {
            "search_context": state.get("search_context", "") + f"\n[비판검색: {query}]\n{result}",
            "loop_count":     state.get("loop_count", 0) + 1,
        }

    def _cons_generator(self, state: SubTeamState) -> Dict:
        feedback_prefix = f"[이전 피드백]: {state['supervisor_feedback']}\n\n" \
            if state.get("supervisor_feedback") else ""
        prompt = feedback_prefix + CONS_PROMPT.format(
            policy_facts   = state.get("policy_facts", ""),
            article_facts  = state.get("article_facts", ""),
            search_context = state.get("search_context", "없음"),
        )
        try:
            draft = llm(
                [{"role": "user", "content": prompt}],
                self.client, model=LLM_MODEL, max_tokens=1500, json_mode=True,
            )
            logger.info(f"[con_branch] Generator {len(draft)}자 생성")
            return {"draft": draft, "loop_count": state.get("loop_count", 0) + 1}
        except Exception as e:
            logger.warning(f"[con_branch] Generator 실패: {e}")
            return {"draft": "{}", "loop_count": state.get("loop_count", 0) + 1}

    # ══════════════════════════════════════════════════════════════════════════
    # 공통 노드
    # ══════════════════════════════════════════════════════════════════════════

    def extract_facts_node(self, state: PolicyCardState) -> Dict:
        source   = state.get("source", {})
        doc_text = json.dumps(source, ensure_ascii=False, default=str)[:5000]

        # 법령 조문 추가 (related_laws가 있으면)
        laws = state.get("related_laws") or []
        if laws:
            law_text = "\n".join(
                f"[{l.get('법령명','')}] {l.get('조문','')[:300]}"
                for l in laws[:3]
            )
            doc_text += f"\n\n[관련 법령]\n{law_text}"

        try:
            policy_facts = json.loads(llm(
                [{"role": "user", "content": EXTRACT_POLICY_PROMPT.format(policy_doc=doc_text)}],
                self.client, model=LLM_MODEL_FAST, max_tokens=1000, json_mode=True,
            ))
        except Exception as e:
            return {"error": f"정책 사실 추출 실패: {e}"}

        # 관련 기사 — stance 없으면 keyword_matched 기준 상위 5건
        articles = state.get("related_articles") or []
        has_stance = any(a.get("stance") for a in articles)
        if has_stance:
            sorted_arts = (
                [a for a in articles if a.get("stance") == "neutral"] +
                [a for a in articles if a.get("stance") == "pro"] +
                [a for a in articles if a.get("stance") == "con"]
            )[:5]
        else:
            # 정책 관련 뉴스는 stance 없음 — keyword_matched 기준 최신순 상위 5건
            pol_name = policy_facts.get("name", "")
            matched = [a for a in articles if pol_name in a.get("keyword_matched", "")]
            sorted_arts = (matched or articles)[:5]

        article_facts = []
        for art in sorted_arts:
            try:
                article_facts.append(json.loads(llm(
                    [{"role": "user", "content": EXTRACT_ARTICLE_PROMPT.format(
                        publisher = art.get("press", art.get("publisher", "")),
                        title     = art.get("title", ""),
                        content   = art.get("content", "")[:2000],
                    )}],
                    self.client, model=LLM_MODEL_FAST, max_tokens=600, json_mode=True,
                )))
            except Exception:
                pass

        logger.info(f"[extract_facts] 정책: '{policy_facts.get('name','')}' | 기사: {len(article_facts)}건")
        return {"policy_facts": policy_facts, "article_facts": article_facts}

    def generate_summary_node(self, state: PolicyCardState) -> Dict:
        try:
            summary = json.loads(llm(
                [{"role": "user", "content": SUMMARY_PROMPT.format(
                    policy_facts=json.dumps(state.get("policy_facts", {}), ensure_ascii=False),
                )}],
                self.client, model=LLM_MODEL, max_tokens=800, json_mode=True,
            ))
            logger.info(f"[generate_summary] title='{summary.get('title','')}'")
            return {"summary": summary}
        except Exception:
            return {"summary": {}}

    # ══════════════════════════════════════════════════════════════════════════
    # 병렬 브랜치 노드
    # ══════════════════════════════════════════════════════════════════════════

    def _generate_core_part(self, prompt_template: str, facts_text: str, part_name: str) -> str:
        """파트별 CORE 생성 — 텍스트 직접 반환 (뉴스 카드와 동일)"""
        try:
            text = llm(
                [{"role": "user", "content": prompt_template.format(
                    extracted_facts=facts_text
                )}],
                self.client, model=LLM_MODEL, max_tokens=2000, json_mode=False,
            )
            text = (text or "").strip()
            logger.info(f"[core_branch] {part_name} {len(text)}자")
            return text
        except Exception as e:
            logger.warning(f"[core_branch] {part_name} 생성 실패: {e}")
            return ""

    def run_core_branch(self, state: PolicyCardState) -> Dict:
        """
        CORE 생성 브랜치 — 4개 파트 별도 생성 + 각각 KR-ELECTRA 편향 검사.

        ① 이 정책이 왜 생겼나   (300자+) → KR-ELECTRA
        ② 정책 내용이 뭔가       (500자+) → KR-ELECTRA
        ③ 관련 기관/단체 입장    (400자+) → KR-ELECTRA
        ④ 청년에게 어떤 영향이   (300자+) → 검사 제외
        """
        # 정책 + 기사 사실을 합쳐서 하나의 facts_text로
        policy_facts  = state.get("policy_facts", {})
        article_facts = state.get("article_facts", [])
        facts_text = json.dumps(
            {"policy": policy_facts, "articles": article_facts},
            ensure_ascii=False, indent=2
        )

        PARTS = [
            ("①도입배경",    CORE_PART1_PROMPT, "■ 이 정책이 왜 생겼나"),
            ("②정책내용",    CORE_PART2_PROMPT, "■ 정책 내용이 뭔가"),
            ("③이해관계자",  CORE_PART3_PROMPT, "■ 관련 기관·단체 입장"),
            ("④청년영향",    CORE_PART4_PROMPT, "■ 청년에게 어떤 영향이 있나"),
        ]

        sections          = []
        bias_log          = {}
        extra_instruction = ""

        for part_name, prompt_template, header in PARTS:
            part_text = ""
            skip_bias = (part_name == "④청년영향")

            if skip_bias:
                part_text = self._generate_core_part(prompt_template, facts_text, f"{part_name}(시도1)")
                logger.info(f"[core_branch] {part_name} 편향 검사 스킵")
            else:
                for attempt in range(MAX_RETRY + 1):
                    prompt = prompt_template
                    if extra_instruction:
                        prompt = prompt + f"\n\n[추가 지시] {extra_instruction}"

                    part_text = self._generate_core_part(prompt, facts_text, f"{part_name}(시도{attempt+1})")

                    if not part_text:
                        break

                    result = check_content_bias(part_text, self.classifier)
                    bias_log[part_name] = result
                    logger.info(f"[core_branch] {part_name} bias "
                                f"label={result['label']} conf={result['confidence']} "
                                f"attempt={attempt+1}")

                    if result["passed"]:
                        break

                    logger.warning(f"[core_branch] {part_name} 텍스트(앞 300자):\n{part_text[:300]}")

                    if attempt >= MAX_RETRY:
                        logger.warning(f"[core_branch] {part_name} 편향 재시도 초과 → skipped")
                        return {
                            "parallel_results": [{"type": "core", "skipped": True,
                                                   "core_content": "", "discussion_question": ""}],
                            "bias_log": {"CORE": bias_log},
                        }

                    extra_instruction = (
                        f"이전 작성본:\n{part_text}\n\n"
                        "위 텍스트가 특정 입장을 옹호하는 사설/칼럼 느낌으로 작성됐습니다. "
                        "의견이나 주장 대신 사실과 현황을 객관적으로 전달하는 정책 설명 스타일로 재작성하세요. "
                        "'~해야 한다', '~이 옳다' 같은 단정적 주장 표현을 제거하고 "
                        "'~이 논의되고 있다', '~가 발표됐다' 형식으로 서술하세요."
                    )

            extra_instruction = ""

            if part_text:
                sections.append(f"{header}\n{part_text}")

        core = "\n\n".join(sections)
        logger.info(f"[core_branch] 파트 합산 완료 {len(core)}자")

        if not core:
            return {
                "parallel_results": [{"type": "core", "skipped": True,
                                       "core_content": "", "discussion_question": ""}],
                "bias_log": {"CORE": bias_log},
            }

        return {
            "parallel_results": [{"type": "core", "skipped": False,
                                  "core_content": core, "discussion_question": ""}],
            "bias_log": {"CORE": bias_log},
        }

    def run_pro_branch(self, state: PolicyCardState) -> Dict:
        """찬성 의견 생성 + Constitutional AI 편향 검사."""
        policy_facts_str  = json.dumps(state.get("policy_facts", {}), ensure_ascii=False)
        article_facts_str = json.dumps(state.get("article_facts", []), ensure_ascii=False)
        feedback          = ""

        for attempt in range(MAX_RETRY + 1):
            logger.info(f"[pro_branch] 생성 시도 {attempt+1}/{MAX_RETRY+1}")
            initial: SubTeamState = {
                "policy_facts":        policy_facts_str,
                "article_facts":       article_facts_str,
                "search_context":      "",
                "draft":               "",
                "next_worker":         "",
                "supervisor_feedback": feedback,
                "loop_count":          0,
            }
            final = self.pros_team.invoke(initial, {"recursion_limit": 10})
            try:
                data     = json.loads(final.get("draft", "{}"))
                argument = data.get("argument", "")
            except Exception:
                argument = ""

            if not argument:
                logger.warning("[pro_branch] argument 없음")
                break

            result = check_ai_bias(argument, self.client, llm_model="gpt-4o-mini")
            logger.info(f"[pro_branch] bias passed={result['passed']} attempt={attempt+1}")

            if result["passed"]:
                return {
                    "parallel_results": [{"type": "pro", "skipped": False,
                                          "argument": argument}],
                    "bias_log": {"PRO": result},
                }

            if attempt >= MAX_RETRY:
                logger.warning("[pro_branch] 재시도 초과 → skipped")
                break

            feedback = (
                f"헌법 제{result.get('violated_article')}조 위반 ({result.get('reason', '')}). "
                "정당·정치세력 평가나 상대 낙인 없이 정책 효과 중심으로 재작성하세요."
            )

        return {
            "parallel_results": [{"type": "pro", "skipped": True, "argument": ""}],
            "bias_log": {"PRO": {"passed": False}},
        }

    def run_con_branch(self, state: PolicyCardState) -> Dict:
        """반대 의견 생성 + Constitutional AI 편향 검사."""
        policy_facts_str  = json.dumps(state.get("policy_facts", {}), ensure_ascii=False)
        article_facts_str = json.dumps(state.get("article_facts", []), ensure_ascii=False)
        feedback          = ""

        for attempt in range(MAX_RETRY + 1):
            logger.info(f"[con_branch] 생성 시도 {attempt+1}/{MAX_RETRY+1}")
            initial: SubTeamState = {
                "policy_facts":        policy_facts_str,
                "article_facts":       article_facts_str,
                "search_context":      "",
                "draft":               "",
                "next_worker":         "",
                "supervisor_feedback": feedback,
                "loop_count":          0,
            }
            final = self.cons_team.invoke(initial, {"recursion_limit": 20})
            try:
                data     = json.loads(final.get("draft", "{}"))
                argument = data.get("argument", "")
            except Exception:
                argument = ""

            if not argument:
                logger.warning("[con_branch] argument 없음")
                break

            result = check_ai_bias(argument, self.client, llm_model="gpt-4o-mini")
            logger.info(f"[con_branch] bias passed={result['passed']} attempt={attempt+1}")

            if result["passed"]:
                return {
                    "parallel_results": [{"type": "con", "skipped": False,
                                          "argument": argument}],
                    "bias_log": {"CON": result},
                }

            if attempt >= MAX_RETRY:
                logger.warning("[con_branch] 재시도 초과 → skipped")
                break

            feedback = (
                f"헌법 제{result.get('violated_article')}조 위반 ({result.get('reason', '')}). "
                "정당·정치세력 평가나 상대 낙인 없이 정책 문제점 중심으로 재작성하세요."
            )

        return {
            "parallel_results": [{"type": "con", "skipped": True, "argument": ""}],
            "bias_log": {"CON": {"passed": False}},
        }

    # ══════════════════════════════════════════════════════════════════════════
    # 토론 주제 생성
    # ══════════════════════════════════════════════════════════════════════════

    def generate_debate_topic_node(self, state: PolicyCardState) -> Dict:
        """찬성/반대 논거에서 A vs B 형식의 보편적 토론 주제 생성."""
        card_data = state.get("card_data", {})
        if not card_data:
            return {"debate_topic": ""}

        opinion = card_data.get("tabs", {}).get("OPINION", [])
        pro_argument = next((p["argument"] for p in opinion if p.get("stance") == "찬성"), "")
        con_argument = next((p["argument"] for p in opinion if p.get("stance") == "반대"), "")

        if not pro_argument or not con_argument:
            logger.warning("[debate_topic] PRO/CON argument 없음 → debate_topic 생략")
            return {"debate_topic": ""}

        policy_facts_str = json.dumps(
            state.get("policy_facts", {}), ensure_ascii=False, indent=2
        )
        try:
            topic = llm(
                [{"role": "user", "content": DEBATE_TOPIC_PROMPT.format(
                    policy_facts=policy_facts_str,
                    pro_argument=pro_argument,
                    con_argument=con_argument,
                )}],
                self.client, model=LLM_MODEL, max_tokens=150, json_mode=False,
            )
            topic = (topic or "").strip().strip('"').strip("'")
            logger.info(f"[debate_topic] '{topic}'")
            # card_data에도 반영
            updated_card_data = {**card_data, "debate_topic": topic}
            return {"debate_topic": topic, "card_data": updated_card_data}
        except Exception as e:
            logger.warning(f"[debate_topic] 생성 실패: {e}")
            return {"debate_topic": ""}

    # ══════════════════════════════════════════════════════════════════════════
    # assemble — 뉴스 카드와 동일한 구조로 통일
    # ══════════════════════════════════════════════════════════════════════════

    def assemble_node(self, state: PolicyCardState) -> Dict:
        results = state.get("parallel_results", [])
        by_type = {r["type"]: r for r in results}

        core_result = by_type.get("core", {})
        pro_result  = by_type.get("pro",  {})
        con_result  = by_type.get("con",  {})

        if core_result.get("skipped") or pro_result.get("skipped") or con_result.get("skipped"):
            skipped = [k for k, v in by_type.items() if v.get("skipped")]
            logger.warning(f"[assemble] 편향 초과 브랜치: {skipped} → 카드 폐기")
            return {"bias_skip_card": True}

        summary = dict(state.get("summary", {}))

        # 최상위로 꺼낼 필드 — 뉴스 카드와 동일 구조
        title    = summary.pop("title",            "")
        intro    = summary.pop("intro",            summary.get("youth_connection", "")[:255])
        summary.pop("discussion_question", None)

        # OPINION — 찬성/반대 구조화
        perspectives = []
        if pro_result.get("argument"):
            perspectives.append({"stance": "찬성", "argument": pro_result["argument"]})
        if con_result.get("argument"):
            perspectives.append({"stance": "반대", "argument": con_result["argument"]})

        source = state.get("source", {})
        tabs = {
            "SUMMARY": summary,
            "CORE":    core_result.get("core_content", ""),
            "OPINION": perspectives,
            "SOURCE":  {
                "url":  source.get("url", source.get("온라인신청사이트URL", "")),
                "name": source.get("name", source.get("서비스명", "")),
                "org":  source.get("org",  source.get("소관기관명", "")),
            },
        }

        logger.info(f"[assemble] 카드 조립 완료: '{title}' "
                    f"CORE={len(tabs['CORE'])}자 OPINION={len(tabs['OPINION'])}개")

        return {
            "card_data": {
                "title":       title,
                "intro":       intro,
                "debate_topic": "",   # generate_debate_topic 노드에서 채워짐
                "tabs":        tabs,
            }
        }

    def save_node(self, state: PolicyCardState) -> Dict:
        card_data = state.get("card_data", {})
        if not card_data:
            return {"error": "card_data 없음"}
        try:
            card_id = upsert_card(
                self.qdrant_client,
                card_data.get("tabs", card_data),
                state.get("card_type", "POLICY"),
            )
            logger.info(f"[save] {state.get('card_type')} 카드 #{card_id} Qdrant 저장 완료")
            return {"card_id": card_id}
        except Exception as e:
            return {"error": str(e)}