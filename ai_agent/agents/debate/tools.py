"""
agents/debate/tools.py
멀티에이전트 구성:
  ProAgent    : 찬성 측 발언 생성 (RAG 검색 포함)
  ConAgent    : 반대 측 발언 생성 (RAG 검색 포함)
  ReviewAgent : AI 발언 편향·혐오 검토
  SummaryAgent: 토론 종료 후 요약 생성
  UserFilterTools: 사용자 입력 3단계 필터 (도구로 유지)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Dict, List

from openai import OpenAI

from .filters import quick_filter
from .hate_vector import vector_hate_filter
from .prompts import (
    CON_MSG_TYPE_INSTRUCTION,
    CON_SYSTEM_EASY,
    CON_SYSTEM_HARD,
    PRO_MSG_TYPE_INSTRUCTION,
    PRO_SYSTEM_EASY,
    PRO_SYSTEM_HARD,
    REVIEW_PROMPT,
    USER_INPUT_CHECK_PROMPT,
    NO_USER_FEEDBACK_INSTRUCTION,
    USER_FEEDBACK_INSTRUCTION,
    _SUMMARY_BASE,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# 내부 유틸
# ════════════════════════════════════════════════════════════════════════════

def _llm_text(messages: list, client: OpenAI, model: str = "gpt-4o-mini",
              max_tokens: int = 600, temperature: float = 0.7) -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content.strip()


def _llm_json(messages: list, client: OpenAI, model: str = "gpt-4o-mini",
              max_tokens: int = 600) -> dict:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        temperature=0,
    )
    return json.loads(resp.choices[0].message.content)


def _format_sources(results: List[Dict]) -> str:
    if not results:
        return "관련 자료 없음"
    lines = []
    for r in results:
        meta     = r.get("metadata", {})
        doc_type = meta.get("doc_type", "news")
        url      = meta.get("source_url", meta.get("url", ""))
        snippet  = r.get("content", "")[:200]

        if doc_type == "policy":
            lines.append(
                f"[정책] {meta.get('title','')} · {meta.get('department','')}\n"
                f"  내용: {snippet}\n  출처: {url or '(URL 없음)'}"
            )
        elif doc_type == "bill":
            lines.append(
                f"[법안] {meta.get('title','')} (의안번호 {meta.get('bill_num','')})\n"
                f"  내용: {snippet}\n  출처: {url or '(URL 없음)'}"
            )
        else:
            pub  = meta.get("publisher", meta.get("press", "?"))
            date = str(meta.get("published_at", ""))[:10]
            lines.append(
                f"[기사] {pub} ({date})\n"
                f"  내용: {snippet}\n  출처: {url or '(URL 없음)'}"
            )
    return "\n\n".join(lines)


def _search_evidence(query: str, vector_client, openai_client: OpenAI,
                     model_key: str, top_k: int = 5) -> List[Dict]:
    try:
        from db.retriever import retrieve_all
        sub_k = min(top_k, 3)
        return retrieve_all(
            query=query,
            client=vector_client,
            model_key=model_key,
            openai_client=openai_client,
            top_k=top_k,
            news_k=sub_k,
            policy_k=sub_k,
            bill_k=sub_k,
        )
    except Exception as e:
        logger.warning(f"search_evidence 오류: {e}")
        return []


# ════════════════════════════════════════════════════════════════════════════
# ProAgent — 찬성 측 발언 생성
# ════════════════════════════════════════════════════════════════════════════

def _build_rag_query(policy_title: str, stance: str, msg_type: str,
                     history: list) -> str:
    """
    발언 유형·진영·이전 이력에 맞는 동적 RAG 쿼리 생성.
    argument: LLM으로 동적 쿼리 생성 (_generate_argument_query 사용)
    rebuttal/response: 상대 직전 발언 핵심 내용 반영
    position: 진영 관점 키워드
    """
    # Fix C: stance_hint 제거 — 범용어가 임베딩을 복지/지원 쪽으로 끌어당기는 문제 방지
    # stance 방향성은 프롬프트 규칙이 담당하므로 검색 쿼리엔 policy_title만 사용
    if msg_type in ("rebuttal", "response", "extra_rebuttal", "extra_response"):
        # 내 측(stance)이 아닌 바로 전 발언을 검색 쿼리에 반영.
        # user 발언(participant=="user")도 상대로 포함해야 함 (AI vs User 버그 방지).
        for msg in reversed(history):
            if msg["participant"] in ("system", stance):
                continue
            if msg.get("content"):
                return f"{policy_title} {msg['content'][:150]}"

    return policy_title


def _generate_argument_query(policy_title: str, stance: str,
                             used_arguments: list[str],
                             client: OpenAI, model: str) -> tuple[str, str]:
    """
    argument 발언 전용 동적 RAG 쿼리 생성.
    (opinion_query, case_query) 튜플 반환.
    - opinion_query: 분석·의견·칼럼 검색용
    - case_query   : 실제 판결·사례·현장 사건 검색용
    used_arguments가 없으면 (첫 턴) 기본 stance_hint 사용.
    """
    stance_hint = {
        "pro": "찬성 지지 긍정 효과 필요성",
        "con": "반대 반론 문제점 부작용 우려",
    }.get(stance, "")
    default_opinion = f"{policy_title} {stance_hint}"
    default_case    = f"{policy_title} 판결 사례 현장 현황"

    covered   = ", ".join(used_arguments[-6:]) if used_arguments else ""
    stance_kr = "찬성" if stance == "pro" else "반대"

    prompt = (
        f"정책: {policy_title}\n"
        f"입장: {stance_kr}\n"
        + (f"이미 사용한 논거: {covered}\n" if covered else "")
        + "\n아래 두 가지 검색어를 제시하세요.\n"
        "1. opinion_query: 위 논거와 다른 새로운 관점의 분석·의견·칼럼 검색용 (10단어 이내)\n"
        "2. case_query: 실제 판결·교섭 사례·현장 사건 검색용 (10단어 이내)\n"
        '반드시 JSON 형식으로만: {"opinion_query": "...", "case_query": "..."}'
    )
    try:
        result = _llm_json(
            [{"role": "user", "content": prompt}],
            client, model=model, max_tokens=80,
        )
        opinion = result.get("opinion_query", "").strip()
        case    = result.get("case_query", "").strip()
        opinion_query = f"{policy_title} {opinion}" if opinion else default_opinion
        case_query    = f"{policy_title} {case}"    if case    else default_case
        logger.info(f"[동적쿼리] {stance} opinion={opinion_query[:60]} | case={case_query[:60]}")
        return opinion_query, case_query
    except Exception as e:
        logger.warning(f"_generate_argument_query 오류: {e}")

    return default_opinion, default_case


def _dedup_sources(sources: List[Dict]) -> List[Dict]:
    """URL 기준 중복 제거. URL 없으면 content 앞 100자로 대체."""
    seen: set = set()
    result = []
    for r in sources:
        meta = r.get("metadata", {})
        key = meta.get("source_url", meta.get("url", "")) or r.get("content", "")[:100]
        if key not in seen:
            seen.add(key)
            result.append(r)
    return result


def _build_history(messages: list) -> str:
    """공유 이력 텍스트 생성 (최근 8개)."""
    lines = []
    for m in messages[-8:]:
        label = {"pro": "찬성(PRO)", "con": "반대(CON)", "user": "사용자"}.get(
            m["participant"], m["participant"])
        lines.append(f"[{label} / {m['msg_type']}] {m['content'][:300]}")
    return "\n".join(lines)


def _get_last_opponent_speech(history: list, my_side: str) -> str:
    """
    '바로 전 상대 발언' 전문 반환 (rebuttal/response 시 직접 인용용).

    my_side(= 현재 발언자, "pro"|"con")가 아닌 가장 최근 발언을 반환한다.
    AI vs User 모드에선 상대가 participant=="user"일 수 있으므로
    pro/con으로 한정하지 않는다. (과거엔 user 발언을 못 잡아 반박 대상이 비어
    AI가 유저 말을 무시하고 반박하던 버그가 있었음)
    """
    for msg in reversed(history):
        if msg["participant"] in ("system", my_side):
            continue
        if msg["msg_type"] in ("argument", "rebuttal", "response", "position",
                               "extra_rebuttal", "extra_response"):
            return msg["content"]
    return ""


def _build_user_content(msg_type: str, history: list, my_side: str,
                        instruction: str, used_arguments: list[str]) -> str:
    """유저 메시지 조립 — 논거 추적 + rebuttal 직전 발언 주입."""
    parts = []

    # 1. 이전 토론 이력
    history_text = _build_history(history)
    if history_text:
        parts.append(f"[이전 토론 이력]\n{history_text}")

    # 2. rebuttal / response: 상대 직전 발언 명시적 인용
    if msg_type in ("rebuttal", "response", "extra_rebuttal", "extra_response"):
        opponent_speech = _get_last_opponent_speech(history, my_side)
        if opponent_speech:
            parts.append(
                f"[반박 대상 발언 — 반드시 이 내용을 직접 인용하며 반박할 것]\n{opponent_speech}"
            )

    # 3. 이미 사용한 논거 (반복 금지)
    if used_arguments:
        args_str = ", ".join(f'"{a}"' for a in used_arguments[-10:])
        parts.append(
            f"[이미 사용한 논거 — 반복 금지]\n{args_str}\n"
            "위 논거와 동일하거나 유사한 주장은 이번 발언에서 사용하지 마세요. "
            "반드시 새로운 관점이나 근거를 제시하세요."
        )

    # 4. 발언 지시 + JSON 응답 형식 요청
    #    단락 규칙은 system 규칙 끝에 묻혀 잘 안 지켜지므로 여기(지시 직전)에서 한 번 더,
    #    그리고 JSON 안전을 위해 줄바꿈을 반드시 \\n 으로 이스케이프하도록 명시한다.
    parts.append(
        f"[지시]\n{instruction}\n\n"
        "[형식 규칙]\n"
        "- speech는 반드시 두 단락으로 작성하고, 두 단락 사이는 줄바꿈으로 구분하세요.\n"
        "- JSON 문자열 안에서 줄바꿈은 실제 개행이 아니라 반드시 \\n 으로 표기하세요.\n"
        "  예: \"speech\": \"첫째 단락 내용.\\n둘째 단락 내용.\"\n\n"
        "반드시 아래 JSON 형식으로만 응답하세요:\n"
        '{"speech": "첫째 단락.\\n둘째 단락.", "key_arguments": ["핵심 논거1", "핵심 논거2", "핵심 논거3"]}'
    )

    return "\n\n".join(parts)


def _parse_speech_response(raw: str) -> tuple[str, list[str]]:
    """
    LLM 응답에서 speech와 key_arguments 추출.

    모델이 speech 안에 단락 구분 '리터럴 개행'을 넣으면 기본 json.loads는
    제어문자 거부로 실패한다(→ 과거엔 raw 전체를 speech로 반환해 JSON 찌꺼기 노출 +
    단락 깨짐 + key_arguments 유실). 이를 막기 위해:
      1) 코드펜스(```json ... ```) 제거
      2) strict=False 로 리터럴 개행 허용 파싱
      3) 그래도 실패하면 정규식으로 speech 값만 추출
    """
    text = raw.strip()

    # 1) 코드펜스 제거
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # 2) strict=False 파싱 (리터럴 개행 허용)
    try:
        data = json.loads(text, strict=False)
        speech = data.get("speech", text)
        args   = [str(a) for a in data.get("key_arguments", [])]
        return speech, args
    except Exception:
        pass

    # 3) 정규식 폴백 — speech 값만이라도 살린다 (정상 종료된 JSON)
    m = re.search(r'"speech"\s*:\s*"(.*?)"\s*(?:,\s*"key_arguments"|\}\s*$)',
                  text, re.DOTALL)
    if m:
        return _unescape(m.group(1)).strip(), []

    # 4) 닫는 따옴표 없이 잘린 경우 — '"speech": "' 이후를 본문으로 간주
    m = re.search(r'"speech"\s*:\s*"', text)
    if m:
        speech = text[m.end():]
        # 뒤쪽에 key_arguments 흔적이 있으면 잘라낸다
        speech = re.split(r'"\s*,\s*"key_arguments"', speech)[0]
        speech = speech.rstrip().rstrip('"}').rstrip()
        return _unescape(speech).strip(), []

    # 5) 최후 폴백 — 그대로 반환
    return text, []


def _unescape(s: str) -> str:
    """JSON 이스케이프(\\n, \\", \\\\) 복원."""
    return s.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')


def _resolve_debate_topic(policy: dict) -> tuple[str, str, str]:
    """
    policy_card에서 토론 주제를 해석해 (full_topic, pro_side, con_side) 반환.

    - full_topic : 필터·요약용 '전체 주제' (debate_topic 우선, 없으면 card title)
    - pro_side / con_side : RAG·프롬프트용 '진영별 입장 문구'
        · debate_topic이 'A다 vs B다' 형식이면 → pro=A(좌), con=B(우)  (프론트 표시와 동일)
        · vs가 없거나 debate_topic이 비면 → 양측 모두 full_topic (폴백: 기존 title 동작)
    """
    title = (policy.get("title") or "").strip()
    topic = (policy.get("debate_topic") or "").strip()
    full  = topic or title
    if topic:
        parts = re.split(r"\s+vs\.?\s+", topic, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            return full, parts[0].strip(), parts[1].strip()
    return full, full, full


def _stance_prompt_vars(stance: str, policy: dict) -> tuple[str, str, str]:
    """
    시스템 프롬프트용 (debate_topic_line, my_position) 과 RAG 검색 시드 반환.
    stance: "pro" | "con"
    Returns: (debate_topic_line, my_position, rag_seed)
    """
    full_topic, pro_side, con_side = _resolve_debate_topic(policy)
    is_vs   = pro_side != con_side          # vs 분리 성공 여부
    my_side = pro_side if stance == "pro" else con_side
    side_kr = "찬성(PRO)" if stance == "pro" else "반대(CON)"

    if is_vs:
        debate_topic_line = f"이 토론의 쟁점: {full_topic}"
        my_position       = f"당신({side_kr})이 옹호할 입장: 「{my_side}」 — 이 입장만 일관되게 대변하세요."
        rag_seed          = my_side
    else:
        debate_topic_line = f"토론 주제: {full_topic}"
        my_position       = f"당신은 이 주제에 대해 {side_kr} 입장을 옹호합니다."
        rag_seed          = full_topic
    return debate_topic_line, my_position, rag_seed


# ════════════════════════════════════════════════════════════════════════════
# ProAgent — 찬성 측 발언 생성
# ════════════════════════════════════════════════════════════════════════════

class ProAgent:
    """찬성(PRO) 측 발언을 생성하는 에이전트."""

    def __init__(self, openai_client: OpenAI, vector_client,
                 model_key: str = "ko-sroberta", llm_model: str = "gpt-4o-mini"):
        self.openai_client = openai_client
        self.vector_client = vector_client
        self.model_key     = model_key
        self.llm_model     = llm_model

    def generate(self, policy: dict, msg_type: str, history: list,
                 difficulty: str = "hard",
                 used_arguments: list[str] | None = None) -> tuple[str, list, list[str]]:
        """
        발언 생성.
        Returns: (speech, sources, key_arguments)
        """
        used_arguments = used_arguments or []
        debate_topic_line, my_position, rag_seed = _stance_prompt_vars("pro", policy)
        if msg_type == "argument":
            opinion_query, case_query = _generate_argument_query(
                rag_seed, "pro", used_arguments,
                self.openai_client, self.llm_model,
            )
            opinion_sources = _search_evidence(opinion_query, self.vector_client,
                                               self.openai_client, self.model_key, top_k=3)
            case_sources    = _search_evidence(case_query, self.vector_client,
                                               self.openai_client, self.model_key, top_k=2)
            sources = _dedup_sources(opinion_sources + case_sources)[:5]
            logger.info(f"[ProAgent RAG] opinion={opinion_query[:60]} | case={case_query[:60]}")
        else:
            query = _build_rag_query(rag_seed, "pro", msg_type, history)
            sources = _search_evidence(query, self.vector_client,
                                       self.openai_client, self.model_key)
            logger.info(f"[ProAgent RAG] query={query[:80]}")
        evidence_text  = _format_sources(sources)
        prompt_kwargs  = {
            "policy_title":      policy.get("title", ""),
            "policy_background": str(policy.get("background", policy.get("CORE", "")))[:600],
            "policy_summary":    " ".join(policy.get("summary_points", []))[:300],
            "evidence":          evidence_text,
            "debate_topic_line": debate_topic_line,
            "my_position":       my_position,
        }
        system = (PRO_SYSTEM_EASY if difficulty == "easy" else PRO_SYSTEM_HARD).format(**prompt_kwargs)
        instruction  = PRO_MSG_TYPE_INSTRUCTION.get(msg_type, "")
        user_content = _build_user_content(msg_type, history, "pro", instruction, used_arguments)

        raw = _llm_text(
            [{"role": "system", "content": system},
             {"role": "user",   "content": user_content}],
            self.openai_client, model=self.llm_model,
        )
        speech, key_args = _parse_speech_response(raw)
        return speech, sources, key_args


# ════════════════════════════════════════════════════════════════════════════
# ConAgent — 반대 측 발언 생성
# ════════════════════════════════════════════════════════════════════════════

class ConAgent:
    """반대(CON) 측 발언을 생성하는 에이전트."""

    def __init__(self, openai_client: OpenAI, vector_client,
                 model_key: str = "ko-sroberta", llm_model: str = "gpt-4o-mini"):
        self.openai_client = openai_client
        self.vector_client = vector_client
        self.model_key     = model_key
        self.llm_model     = llm_model

    def generate(self, policy: dict, msg_type: str, history: list,
                 difficulty: str = "hard",
                 used_arguments: list[str] | None = None) -> tuple[str, list, list[str]]:
        """
        발언 생성.
        Returns: (speech, sources, key_arguments)
        """
        used_arguments = used_arguments or []
        debate_topic_line, my_position, rag_seed = _stance_prompt_vars("con", policy)
        if msg_type == "argument":
            opinion_query, case_query = _generate_argument_query(
                rag_seed, "con", used_arguments,
                self.openai_client, self.llm_model,
            )
            opinion_sources = _search_evidence(opinion_query, self.vector_client,
                                               self.openai_client, self.model_key, top_k=3)
            case_sources    = _search_evidence(case_query, self.vector_client,
                                               self.openai_client, self.model_key, top_k=2)
            sources = _dedup_sources(opinion_sources + case_sources)[:5]
            logger.info(f"[ConAgent RAG] opinion={opinion_query[:60]} | case={case_query[:60]}")
        else:
            query = _build_rag_query(rag_seed, "con", msg_type, history)
            sources = _search_evidence(query, self.vector_client,
                                       self.openai_client, self.model_key)
            logger.info(f"[ConAgent RAG] query={query[:80]}")
        evidence_text  = _format_sources(sources)
        prompt_kwargs  = {
            "policy_title":      policy.get("title", ""),
            "policy_background": str(policy.get("background", policy.get("CORE", "")))[:600],
            "policy_summary":    " ".join(policy.get("summary_points", []))[:300],
            "evidence":          evidence_text,
            "debate_topic_line": debate_topic_line,
            "my_position":       my_position,
        }
        system = (CON_SYSTEM_EASY if difficulty == "easy" else CON_SYSTEM_HARD).format(**prompt_kwargs)
        instruction  = CON_MSG_TYPE_INSTRUCTION.get(msg_type, "")
        user_content = _build_user_content(msg_type, history, "con", instruction, used_arguments)

        raw = _llm_text(
            [{"role": "system", "content": system},
             {"role": "user",   "content": user_content}],
            self.openai_client, model=self.llm_model,
        )
        speech, key_args = _parse_speech_response(raw)
        return speech, sources, key_args


# ════════════════════════════════════════════════════════════════════════════
# ReviewAgent — AI 발언 편향·혐오 검토
# ════════════════════════════════════════════════════════════════════════════

class ReviewAgent:
    """AI 발언의 편향성과 혐오 표현을 검토하는 에이전트."""

    def __init__(self, openai_client: OpenAI, llm_model: str = "gpt-4o-mini"):
        self.openai_client = openai_client
        self.llm_model     = llm_model

    def review(self, speech: str) -> Dict:
        """편향·혐오 검토. {"passed": bool, "failed": str, "reason": str} 반환."""
        try:
            prompt = REVIEW_PROMPT.format(speech=speech)
            result = _llm_json(
                [{"role": "user", "content": prompt}],
                self.openai_client, model=self.llm_model,
            )
            passed = bool(result.get("passed", True))
            return {
                "passed": passed,
                "failed": result.get("failed", "none") if not passed else "none",
                "reason": result.get("reason", ""),
            }
        except Exception as e:
            logger.warning(f"ReviewAgent.review 오류: {e}")
            return {"passed": True, "failed": "none", "reason": f"검토 오류: {e}"}


# ════════════════════════════════════════════════════════════════════════════
# SummaryAgent — 토론 종료 후 요약 생성
# ════════════════════════════════════════════════════════════════════════════

class SummaryAgent:
    """토론 전체 이력을 받아 요약 JSON을 생성하는 에이전트."""

    def __init__(self, openai_client: OpenAI, llm_model: str = "gpt-4o-mini"):
        self.openai_client = openai_client
        self.llm_model     = llm_model

    def summarize(self, policy: dict, messages: list, has_user: bool = False) -> dict:
        transcript = "\n".join([
            f"[{'찬성(PRO)' if m['participant']=='pro' else '반대(CON)' if m['participant']=='con' else '사용자'}"
            f" / {m['msg_type']}] {m['content'][:200]}"
            for m in messages
            if m["msg_type"] != "summary"
        ])[:4000]

        user_feedback_instruction = (
            USER_FEEDBACK_INSTRUCTION if has_user else NO_USER_FEEDBACK_INSTRUCTION
        )
        prompt = _SUMMARY_BASE.format(
            policy_title=(policy.get("debate_topic") or policy.get("title", "")),
            transcript=transcript,
            user_feedback_instruction=user_feedback_instruction,
        )
        try:
            result = _llm_json(
                [{"role": "user", "content": prompt}],
                self.openai_client, model=self.llm_model, max_tokens=900,
            )
            return result
        except Exception as e:
            logger.warning(f"SummaryAgent.summarize 오류: {e}")
            return {"overview": str(e), "pro_summary": {}, "con_summary": {}, "user_feedback": {}}


# ════════════════════════════════════════════════════════════════════════════
# UserFilterTools — 사용자 입력 3단계 필터 (도구로 유지)
# ════════════════════════════════════════════════════════════════════════════

class UserFilterTools:
    """사용자 입력 검사: 1차 사전필터 → 2차 LLM → 3차 벡터유사도."""

    def __init__(self, openai_client: OpenAI, vector_client,
                 llm_model: str = "gpt-4o-mini"):
        self.openai_client = openai_client
        self.vector_client = vector_client
        self.llm_model     = llm_model

    def check(self, user_input: str, policy_title: str) -> Dict:
        # 1차: 사전 필터
        r1 = quick_filter(user_input)
        if not r1["passed"]:
            logger.info(f"사전필터 차단: [{r1['violation_type']}] '{r1['matched']}'")
            return {"passed": False, "violation_type": r1["violation_type"], "message": r1["message"]}

        # 2차: LLM 주제이탈·맥락혐오
        try:
            prompt = USER_INPUT_CHECK_PROMPT.format(
                policy_title=policy_title, user_input=user_input
            )
            result = _llm_json(
                [{"role": "user", "content": prompt}],
                self.openai_client, model=self.llm_model,
            )
            if not bool(result.get("passed", True)):
                return {
                    "passed": False,
                    "violation_type": result.get("violation_type", "none"),
                    "message": result.get("message", ""),
                }
        except Exception as e:
            logger.warning(f"UserFilterTools LLM 오류: {e}")

        # 3차: 벡터 유사도
        r3 = vector_hate_filter(user_input, self.vector_client, self.openai_client)
        if not r3["passed"]:
            logger.info(f"벡터필터 차단: [{r3['violation_type']}] score={r3['score']}")
            return {"passed": False, "violation_type": r3["violation_type"], "message": r3["message"]}

        return {"passed": True, "violation_type": "none", "message": ""}


# ════════════════════════════════════════════════════════════════════════════
# 하위 호환용 — 기존 코드에서 DebateTools를 직접 참조하는 경우 대비
# ════════════════════════════════════════════════════════════════════════════

class DebateTools:
    """
    하위 호환용 래퍼.
    새 코드에서는 ProAgent / ConAgent / ReviewAgent / SummaryAgent / UserFilterTools를 직접 사용하세요.
    """

    def __init__(self, vector_client, openai_client: OpenAI,
                 model_key: str = "ko-sroberta", llm_model: str = "gpt-4o-mini", **kwargs):
        self.pro_agent     = ProAgent(openai_client, vector_client, model_key, llm_model)
        self.con_agent     = ConAgent(openai_client, vector_client, model_key, llm_model)
        self.review_agent  = ReviewAgent(openai_client, llm_model)
        self.summary_agent = SummaryAgent(openai_client, llm_model)
        self.user_filter   = UserFilterTools(openai_client, vector_client, llm_model)
        # 하위 호환용 속성
        self.vector_client = vector_client
        self.openai_client = openai_client
        self.model_key     = model_key
        self.llm_model     = llm_model

    def search_evidence(self, query: str, **kwargs) -> List[Dict]:
        return _search_evidence(query, self.vector_client, self.openai_client, self.model_key)

    def check_ai_bias(self, speech: str) -> Dict:
        return self.review_agent.review(speech)

    def check_user_input(self, user_input: str, policy_title: str) -> Dict:
        return self.user_filter.check(user_input, policy_title)

    def format_sources_text(self, sources: List[Dict]) -> str:
        return _format_sources(sources)
