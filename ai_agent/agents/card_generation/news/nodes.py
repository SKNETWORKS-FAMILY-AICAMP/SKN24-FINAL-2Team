import json
import logging
from typing import Dict

from openai import OpenAI
from qdrant_client import QdrantClient

from config import LLM_MODEL, LLM_MODEL_FAST, MAX_ARTICLES_PER_CARD
from utils import llm
from db.qdrant_upload import upsert_card
from agents.bias_check import BiasClassifier, check_content_bias, check_ai_bias

from .state import NewsCardState
from .prompts import (
    EXTRACT_PROMPT,
    SUMMARY_PROMPT,
    DEBATE_TOPIC_PROMPT,
    CORE_PART1_PROMPT,
    CORE_PART2_PROMPT,
    CORE_PART3_PROMPT,
    CORE_PART4_PROMPT,
    PERSPECTIVES_GENERATOR_PROMPT,
    PERSPECTIVES_SUPERVISOR_PROMPT,
    SINGLE_PERSPECTIVE_PROMPT,
)

logger = logging.getLogger(__name__)

MAX_RETRY         = 2   # 편향 검사 재시도 최대 횟수 (core 파트별)
MAX_SUPERVISOR    = 3   # OPINION Supervisor 루프 최대 횟수
MAX_OPINION_RETRY = 3   # OPINION 편향 검사 재시도 최대 횟수


class NodeManager:
    def __init__(self, qdrant_client: QdrantClient, openai_client: OpenAI):
        self.qdrant_client = qdrant_client
        self.client = openai_client
        self.classifier = BiasClassifier("BarryKim34/kr-electra-political-bias")

    # ── 헬퍼 ──────────────────────────────────────────────────────────────────
    def _supervisor_check(self, prompt: str) -> Dict:
        try:
            raw = llm(
                [{"role": "user", "content": prompt}],
                self.client, model=LLM_MODEL_FAST, max_tokens=300, json_mode=True,
            )
            result = json.loads(raw)
            return {
                "decision": result.get("decision", "FINISH"),
                "feedback": result.get("feedback", ""),
            }
        except Exception as e:
            logger.warning(f"Supervisor 호출 실패: {e}")
            return {"decision": "FINISH", "feedback": ""}

    def _generate_part(self, prompt_template: str, facts_text: str, part_name: str) -> str:
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

    # ══════════════════════════════════════════════════════════════════════════
    # 공통 노드
    # ══════════════════════════════════════════════════════════════════════════

    def extract_facts_node(self, state: NewsCardState) -> Dict:
        articles  = (state.get("articles") or [])[:MAX_ARTICLES_PER_CARD]
        extracted = []

        for art in articles:
            try:
                raw = llm(
                    [{"role": "user", "content": EXTRACT_PROMPT.format(
                        publisher = art.get("press", art.get("publisher", "")),
                        title = art.get("title", ""),
                        url = art.get("url", ""),
                        content = art.get("content", "")[:3000],
                    )}],
                    self.client, model=LLM_MODEL_FAST, max_tokens=800, json_mode=True,
                )
                extracted.append(json.loads(raw))
            except Exception as e:
                logger.warning(f"기사 추출 실패 ({art.get('title','')[:30]}): {e}")

        if len(extracted) < 2:
            return {"extracted_facts": extracted,
                    "error": f"사실 추출 부족: {len(extracted)}건"}

        logger.info(f"[extract_facts] {len(extracted)}개 기사 사실 추출")
        return {"extracted_facts": extracted}

    def generate_summary_node(self, state: NewsCardState) -> Dict:
        facts_text = json.dumps(state["extracted_facts"], ensure_ascii=False, indent=2)
        try:
            raw  = llm(
                [{"role": "user", "content": SUMMARY_PROMPT.format(
                    extracted_facts=facts_text)}],
                self.client, model=LLM_MODEL, max_tokens=600, json_mode=True,
            )
            data = json.loads(raw)
            logger.info(f"[generate_summary] title='{data.get('title','')}'")
            return {"summary": data}
        except Exception as e:
            logger.warning(f"SUMMARY 생성 실패: {e}")
            return {"summary": {}}

    def generate_debate_topic_node(self, state: NewsCardState) -> Dict:
        facts_text = json.dumps(state.get("extracted_facts", []), ensure_ascii=False, indent=2)
        try:
            topic = llm(
                [{"role": "user", "content": DEBATE_TOPIC_PROMPT.format(
                    extracted_facts=facts_text
                )}],
                self.client, model=LLM_MODEL, max_tokens=150, json_mode=False,
            )
            topic = (topic or "").strip().strip('"').strip("'")
            logger.info(f"[debate_topic] '{topic}'")
            return {"debate_topic": topic}
        except Exception as e:
            logger.warning(f"[debate_topic] 생성 실패: {e}")
            return {"debate_topic": ""}

    # ══════════════════════════════════════════════════════════════════════════
    # 병렬 브랜치 노드
    # ══════════════════════════════════════════════════════════════════════════
    def run_core_branch(self, state: NewsCardState) -> Dict:
        facts_text = json.dumps(state.get("extracted_facts", []), ensure_ascii=False, indent=2)

        PARTS = [
            ("①이슈배경", CORE_PART1_PROMPT, "■ 이슈 배경"),
            ("②무슨일", CORE_PART2_PROMPT, "■ 무슨 일이 있었나"),
            ("③언론사시각", CORE_PART3_PROMPT, "■ 언론사별 시각"),
            ("④청년영향", CORE_PART4_PROMPT, "■ 청년에게 어떤 영향이 있나"),
        ]

        sections = []
        bias_log = {}
        discussion = ""
        extra_instruction = ""

        for part_name, prompt_template, header in PARTS:
            part_text = ""

            # ④ 청년영향은 정책 영향 서술이라 ML 편향 분류기 오분류 잦음 → 검사 제외
            skip_bias = (part_name == "④청년영향")

            if skip_bias:
                part_text = self._generate_part(prompt_template, facts_text, f"{part_name}(시도1)")
                logger.info(f"[core_branch] {part_name} 편향 검사 스킵")
            else:
                for attempt in range(MAX_RETRY + 1):
                    prompt = prompt_template
                    if extra_instruction:
                        prompt = prompt + f"\n\n[추가 지시] {extra_instruction}"

                    part_text = self._generate_part(prompt, facts_text, f"{part_name}(시도{attempt+1})")

                    if not part_text:
                        break

                    result = check_content_bias(part_text, self.classifier)
                    bias_log[part_name] = result
                    logger.info(f"[core_branch] {part_name} bias "
                                f"label={result['label']} conf={result['confidence']} "
                                f"attempt={attempt+1}")
                    if not result["passed"]:
                        logger.warning(f"[core_branch] {part_name} 텍스트(앞 300자):\n{part_text[:300]}")

                    if result["passed"]:
                        break

                    if attempt >= MAX_RETRY:
                        logger.warning(f"[core_branch] {part_name} 편향 재시도 초과 → skipped")
                        return {
                            "parallel_results": [{"type": "core", "skipped": True,  "core_content": "", "discussion_question": ""}],
                            "bias_log": {"CORE": bias_log},
                        }

                    extra_instruction = (
                        f"이전 작성본:\n{part_text}\n\n"
                        "위 텍스트가 특정 입장을 옹호하는 사설/칼럼 느낌으로 작성됐습니다. "
                        "의견이나 주장 대신 사실과 현황을 객관적으로 전달하는 뉴스 본문 스타일로 다시 작성하세요. "
                        "예: '~해야 한다', '~이 옳다', '~이 필요하다' 같은 단정적 주장 표현을 제거하고 "
                        "'~이 논의되고 있다', '~라는 의견이 있다', '~가 발표됐다' 형식으로 서술하세요."
                    )

            extra_instruction = ""

            if part_text:
                sections.append(f"{header}\n{part_text}")

            if part_name == "④청년영향" and part_text:
                import re
                matches    = re.findall(r'[^.!?\n]+\?', part_text)
                discussion = matches[-1].strip() if matches else ""

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
                                  "core_content": core,
                                  "discussion_question": discussion}],
            "bias_log": {"CORE": bias_log},
        }

    def run_perspectives_branch(self, state: NewsCardState) -> Dict:
        parts = []
        for i, fact in enumerate(state.get("extracted_facts", []), 1):
            pub = fact.get("publisher", f"언론사{i}")
            parts.append(f"[기사 {i} | {pub}]\n"
                         f"{json.dumps(fact, ensure_ascii=False, indent=2)}")
        facts_by_media = "\n\n---\n\n".join(parts)

        feedback = ""
        pvs = []
        srcs = []

        for sup_turn in range(MAX_SUPERVISOR + 1):
            logger.info(f"[perspectives_branch] Generator 시도 {sup_turn+1}/{MAX_SUPERVISOR+1}")
            try:
                raw  = llm(
                    [{"role": "user", "content": PERSPECTIVES_GENERATOR_PROMPT.format(
                        extracted_facts_by_media = facts_by_media,
                        feedback = feedback or "없음",
                    )}],
                    self.client, model=LLM_MODEL, max_tokens=4000, json_mode=True,
                )
                data = json.loads(raw)
                pvs  = data.get("perspectives", [])
                srcs = data.get("sources", [])
            except Exception as e:
                logger.warning(f"PERSPECTIVES Generator 실패: {e}")
                break

            if not pvs:
                break

            draft_text = json.dumps(pvs, ensure_ascii=False, indent=2)
            sup_result = self._supervisor_check(
                PERSPECTIVES_SUPERVISOR_PROMPT.format(draft=draft_text)
            )
            logger.info(f"[perspectives_branch] Supervisor → {sup_result['decision']} "
                        f"({len(pvs)}개 언론사) turn={sup_turn+1}")

            if sup_result["decision"] == "FINISH":
                break

            if sup_turn >= MAX_SUPERVISOR:
                logger.warning("[perspectives_branch] Supervisor 루프 초과 → 현재 draft 사용")
                break

            feedback = sup_result["feedback"]

        if not pvs:
            return {
                "parallel_results": [{"type": "perspectives", "skipped": True, "perspectives": [], "sources": []}],
                "bias_log": {"OPINION": {"passed": False}},
            }

        # Constitutional AI 편향 검사 — 위반 언론사만 재생성
        for attempt in range(MAX_OPINION_RETRY + 1):
            failed_items = []
            for p in pvs:
                media = p.get("media", "?")
                stance = p.get("stance", "")
                p_result = check_ai_bias(stance, self.client, llm_model="gpt-4o-mini")
                if not p_result["passed"]:
                    failed_items.append({
                        "media": media,
                        "reason": p_result.get("reason", ""),
                        "article": p_result.get("violated_article"),
                    })
                    logger.warning(f"  └ [{media}] 위반 — {p_result.get('reason','')}")

            logger.info(f"[perspectives_branch] OPINION 편향 검사 "
                        f"attempt={attempt+1} 위반={len(failed_items)}개")

            if not failed_items:
                return {
                    "parallel_results": [{"type": "perspectives", "skipped": False, "perspectives": pvs, "sources": srcs}],
                    "bias_log": {"OPINION": {"passed": True}},
                }

            if attempt >= MAX_OPINION_RETRY:
                logger.warning("[perspectives_branch] 편향 재시도 초과 → skipped")
                break

            # 위반 언론사만 개별 재생성
            for failed in failed_items:
                media  = failed["media"]
                reason = failed["reason"]
                # 해당 언론사 팩트 찾기
                media_fact = next(
                    (f for f in state.get("extracted_facts", [])
                     if f.get("publisher", "") == media),
                    None
                )
                if not media_fact:
                    continue

                logger.info(f"[perspectives_branch] [{media}] 단독 재생성 (reason: {reason})")
                try:
                    raw = llm(
                        [{"role": "user", "content": SINGLE_PERSPECTIVE_PROMPT.format(
                            publisher = media,
                            fact = json.dumps(media_fact, ensure_ascii=False, indent=2),
                            violation = reason,
                        )}],
                        self.client, model=LLM_MODEL, max_tokens=400, json_mode=False,
                    )
                    new_stance = (raw or "").strip()
                    if new_stance:
                        for i, p in enumerate(pvs):
                            if p.get("media") == media:
                                pvs[i]["stance"] = new_stance
                                logger.info(f"[perspectives_branch] [{media}] 교체 완료 {len(new_stance)}자")
                                break
                except Exception as e:
                    logger.warning(f"[perspectives_branch] [{media}] 재생성 실패: {e}")

        return {
            "parallel_results": [{"type": "perspectives", "skipped": True, "perspectives": [], "sources": []}],
            "bias_log": {"OPINION": {"passed": False}},
        }

    # ══════════════════════════════════════════════════════════════════════════
    # assemble
    # ══════════════════════════════════════════════════════════════════════════

    def assemble_node(self, state: NewsCardState) -> Dict:
        results  = state.get("parallel_results", [])
        by_type  = {r["type"]: r for r in results}

        core_result = by_type.get("core", {})
        pvs_result = by_type.get("perspectives", {})

        if core_result.get("skipped") or pvs_result.get("skipped"):
            skipped = [k for k, v in by_type.items() if v.get("skipped")]
            logger.warning(f"[assemble] 편향 초과 브랜치: {skipped} → 카드 폐기")
            return {"bias_skip_card": True}

        summary = dict(state.get("summary", {}))

        # 최상위로 꺼낼 필드 — SUMMARY 탭에서 제거
        title = summary.pop("title", "")
        intro = summary.pop("intro", summary.get("youth_connection", "")[:255])
        debate_topic = state.get("debate_topic", "")
        summary.pop("discussion_question", None)

        tabs = {
            "SUMMARY": summary,
            "CORE": core_result.get("core_content", ""),
            "OPINION": pvs_result.get("perspectives", []),
            "SOURCE": pvs_result.get("sources", []),
        }

        logger.info(f"[assemble] 카드 조립 완료: '{title}' "
                    f"CORE={len(tabs['CORE'])}자 "
                    f"OPINION={len(tabs['OPINION'])}개")

        return {
            "card_data": {
                "title": title,
                "intro": intro,
                "debate_topic": debate_topic,
                "tabs": tabs,
            }
        }

    # ══════════════════════════════════════════════════════════════════════════
    # save
    # ══════════════════════════════════════════════════════════════════════════

    def save_node(self, state: NewsCardState) -> Dict:
        card_data = state.get("card_data", {})
        if not card_data:
            return {"error": "card_data 없음"}
        try:
            card_id = upsert_card(self.qdrant_client, card_data.get("tabs", card_data), "NEWS")
            logger.info(f"[save] 뉴스 카드 #{card_id} Qdrant 저장 완료")
            return {"card_id": card_id}
        except Exception as e:
            logger.error(f"[save] Qdrant 저장 실패: {e}")
            return {"error": str(e)}