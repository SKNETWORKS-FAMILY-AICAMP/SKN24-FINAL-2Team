"""
pipeline/pipelines/debate.py
AI vs AI / AI vs User 토론 파이프라인 (Qdrant 로컬 파일 스토리지 직결 완결본)

토론 흐름 (REQ-DEBATE-001 ~ REQ-DEBATE-006):

  [AI vs AI]
  입장 제시
  → 찬성 세부주장 라운드: 기본 3턴 (찬성 세부주장 + 반대 반박 + 찬성 답변) × 3
      └ 3턴 후: 사용자 질문 입력 / 추가 토론 요청 (최대 2회, 반박+답변만) / 다음 단계
  → 반대 세부주장 라운드: 기본 3턴 (반대 세부주장 + 찬성 반박 + 반대 답변) × 3
      └ 3턴 후: 사용자 질문 입력 / 추가 토론 요청 (최대 2회, 반박+답변만) / 다음 단계
  → 주장 다지기

  [AI vs User]
  입장 제시
  → 사용자 세부주장 라운드: 기본 3턴 (사용자 주장 → AI 반박 → 사용자 답변) × 3
      └ 3턴 후: 추가 토론 요청 (최대 2회) / 다음 단계
  → AI 세부주장 라운드: 기본 3턴 (AI 주장 → 사용자 반박 → AI 답변) × 3
      └ 3턴 후: 추가 토론 요청 (최대 2회) / 다음 단계
  → 주장 다지기

RDB: DEBATES → DEBATE_PARTICIPANTS → DEBATE_MESSAGES
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy import Engine
from openai import OpenAI
from qdrant_client import QdrantClient  # 👈 크로마 의존성 제거 및 Qdrant 명세로 변경

from config import (
    LLM_MODEL, LLM_MODEL_FAST,
    DEBATE_MAX_CHARS_PER_TURN, DEBATE_TURNS_PER_ROUND, DEBATE_MAX_EXTRA_ROUNDS,
)
from utils import llm
from db.rdb import (
    create_debate, add_debate_participant, save_debate_message,
    load_debate_history, update_debate_stage, end_debate, list_debates,
)

# 🔧 [핵심 교정] 기존 구형 크로마용 vectordb 대신 마이그레이션 완료된 Qdrant 검색엔진 결합
from pipeline.db.vectordb_qdrant import retrieve_all

logger = logging.getLogger(__name__)


# ─── 시스템 프롬프트 ────────────────────────────────────────────────────────────

_PRO_SYSTEM = f"""\
당신은 찬성 측 AI 토론자입니다.
- 주어진 정책에 대해 찬성 입장에서 논리적으로 주장하세요.
- 정책 자료와 기사 데이터를 근거로 활용하세요.
- 발언 하단에 관련 출처를 반드시 표시하세요. (예: [출처: OO일보 기사 제목])
- 출처가 불명확한 내용은 "(추가 확인 필요)"로 표시하세요.
- 각 발언은 {DEBATE_MAX_CHARS_PER_TURN}자 이내로 작성하세요."""

_CON_SYSTEM = f"""\
당신은 반대 측 AI 토론자입니다.
- 주어진 정책에 대해 반대 입장에서 논리적으로 주장하세요.
- 정책 자료와 기사 데이터를 근거로 활용하세요.
- 발언 하단에 관련 출처를 반드시 표시하세요. (예: [출처: OO일보 기사 제목])
- 출처가 불명확한 내용은 "(추가 확인 필요)"로 표시하세요.
- 각 발언은 {DEBATE_MAX_CHARS_PER_TURN}자 이내로 작성하세요."""

_EASY_SUFFIX = "\n- 이지 모드: 사용자가 반박할 여지가 있도록 논리 연결이 다소 약하거나 보완 여지가 있는 주장을 제시하세요."

_TYPE_INSTRUCTION = {
    "position":    "이 정책에 대한 기본 입장을 제시하세요. (200자 이내)",
    "argument":    "구체적 근거를 포함한 세부 주장을 제시하세요. (500자 이내) 발언 하단에 [출처: ...]를 반드시 표기하세요.",
    "rebuttal":    "상대방의 주장에 논리적으로 반박하세요. (500자 이내) 발언 하단에 [출처: ...]를 반드시 표기하세요.",
    "response":    "상대방의 반박에 명확히 답변하세요. (500자 이내) 발언 하단에 [출처: ...]를 반드시 표기하세요.",
    "extra_reb":   "앞선 주장에 대해 추가 반박 1회를 제시하세요. (500자 이내) 발언 하단에 [출처: ...]를 반드시 표기하세요.",
    "extra_res":   "앞선 반박에 대해 추가 답변 1회를 제시하세요. (500자 이내) 발언 하단에 [출처: ...]를 반드시 표기하세요.",
    "question_ans":"사용자의 질문에 성실하게 답변하세요. (500자 이내)",
}

_SUMMARY_PROMPT = """\
다음 토론 내용을 바탕으로 '주장 다지기' 요약을 JSON으로 작성하세요.
정책: {policy_title}
{user_context}

[토론 내용]
{transcript}

출력 JSON:
{{
  "pro_summary": {{
    "key_arguments": ["주요 주장 1", "주요 주장 2"],
    "key_evidence":  ["주요 근거 1"],
    "key_rebuttals": ["주요 반박 1"]
  }},
  "con_summary": {{
    "key_arguments": ["주요 주장 1", "주요 주장 2"],
    "key_evidence":  ["주요 근거 1"],
    "key_rebuttals": ["주요 반박 1"]
  }},
  "overview": "토론 전체 요약 (3-5문장)",
  "user_feedback": {{
    "strong_points": ["효과적으로 반박한 부분 (AI vs User 모드만)"],
    "weak_points":   ["보완이 필요한 부분 (AI vs User 모드만)"]
  }}
}}"""

_PROFANITY_PROMPT = """\
다음 사용자 입력에 금칙어(성적 비하·비속어 / 정치인·정당명 비하 / 인종·성별·종교 혐오)가 포함되어 있는지 확인하세요.
입력: {user_input}
JSON: {{"contains_profanity": true 또는 false, "reason": "이유"}}"""

_TOPIC_CHECK_PROMPT = """\
다음 사용자 입력이 아래 토론 정책 주제와 관련된 내용인지 판단하세요.
관련 내용 기준: 정책 자체, 정책의 근거·반박·영향·쟁점, 찬반 논거.

정책 주제: {policy_title}
사용자 입력: {user_input}

JSON: {{"is_relevant": true 또는 false, "reason": "이유"}}"""


class DebatePipeline:

    def __init__(
        self,
        engine: Engine,
        chroma: QdrantClient,  # 👈 파라미터 타입 QdrantClient 연동 보장
        openai_client: OpenAI,
        strategy: str = "sentence",
        model_key: str = "ko-sroberta",
    ):
        self.engine    = engine
        self.chroma    = chroma
        self.client    = openai_client
        self.strategy  = strategy
        self.model_key = model_key

    # ══════════════════════════════════════════════════════════════════════════
    # AI vs AI — 세션 생성 및 단계별 제어 메서드
    # ══════════════════════════════════════════════════════════════════════════

    def create_ai_vs_ai(self, selected_policy: str) -> Tuple[int, int, int]:
        debate_id = create_debate(self.engine, "AI vs AI", selected_policy)
        pro_id = add_debate_participant(self.engine, debate_id, "AI", "AGREE",   "찬성 AI")
        con_id = add_debate_participant(self.engine, debate_id, "AI", "DISAGREE","반대 AI")
        logger.info(f"AI vs AI 토론방 생성: #{debate_id}")
        return debate_id, pro_id, con_id

    def run_position_stage(self, debate_id: int, policy_card: Dict, pro_id: int, con_id: int) -> Dict:
        update_debate_stage(self.engine, debate_id, "position")

        history  = load_debate_history(self.engine, debate_id)
        pro_pos  = self._generate_debate_msg(policy_card, "pro", "position", history)
        save_debate_message(self.engine, debate_id, pro_id, pro_pos, "CLAIM")

        history  = load_debate_history(self.engine, debate_id)
        con_pos  = self._generate_debate_msg(policy_card, "con", "position", history)
        save_debate_message(self.engine, debate_id, con_id, con_pos, "CLAIM")

        logger.info(f"  입장 제시 완료")
        return {"pro": pro_pos, "con": con_pos}

    def run_base_turns(
        self,
        debate_id: int,
        policy_card: Dict,
        lead_side: str,
        lead_id: int,
        opp_id: int,
        turns: int = DEBATE_TURNS_PER_ROUND,
    ) -> List[Dict]:
        opp_side  = "con" if lead_side == "pro" else "pro"
        round_log = []

        stage = f"{lead_side}_round"
        update_debate_stage(self.engine, debate_id, stage)

        for t in range(1, turns + 1):
            history = load_debate_history(self.engine, debate_id)
            arg = self._generate_debate_msg(policy_card, lead_side, "argument", history)
            save_debate_message(self.engine, debate_id, lead_id, arg, "CLAIM", t)

            history = load_debate_history(self.engine, debate_id)
            reb = self._generate_debate_msg(policy_card, opp_side, "rebuttal", history)
            save_debate_message(self.engine, debate_id, opp_id, reb, "REBUTTAL", t)

            history = load_debate_history(self.engine, debate_id)
            res = self._generate_debate_msg(policy_card, lead_side, "response", history)
            save_debate_message(self.engine, debate_id, lead_id, res, "ANSWER", t)

            logger.info(f"  [{lead_side.upper()}] 기본 {t}턴 완료")
            round_log.append({"turn": t, "argument": arg, "rebuttal": reb, "response": res})

        return round_log

    def run_extra_debate(
        self,
        debate_id: int,
        policy_card: Dict,
        lead_side: str,
        lead_id: int,
        opp_id: int,
        extra_count: int = 1,
    ) -> Dict:
        if extra_count > DEBATE_MAX_EXTRA_ROUNDS:
            return {"error": f"추가 토론은 최대 {DEBATE_MAX_EXTRA_ROUNDS}회입니다."}

        opp_side = "con" if lead_side == "pro" else "pro"

        history = load_debate_history(self.engine, debate_id)
        reb = self._generate_debate_msg(policy_card, opp_side, "extra_reb", history)
        save_debate_message(self.engine, debate_id, opp_id, reb, "REBUTTAL")

        history = load_debate_history(self.engine, debate_id)
        res = self._generate_debate_msg(policy_card, lead_side, "extra_res", history)
        save_debate_message(self.engine, debate_id, lead_id, res, "ANSWER")

        logger.info(f"  [{lead_side.upper()}] 추가 토론 {extra_count}회 완료")
        return {"extra_num": extra_count, "rebuttal": reb, "response": res}

    def answer_user_question(
        self,
        debate_id: int,
        user_question: str,
        policy_card: Dict,
        question_target_id: int,
        policy_title: str = "",
    ) -> str:
        blocked, _ = self._check_profanity(user_question)
        if blocked:
            return "⚠️ 부적절한 표현이 포함되어 있어 답변할 수 없습니다."

        p_title = policy_title or policy_card.get("title", "")
        relevant, _ = self._check_topic_relevance(user_question, p_title)
        if not relevant:
            return "💬 토론 주제와 관련된 질문을 입력해 주세요."

        user_question = user_question[:DEBATE_MAX_CHARS_PER_TURN]
        role = self._get_ai_role_str(debate_id, question_target_id)

        history = load_debate_history(self.engine, debate_id)
        answer  = self._generate_debate_msg(policy_card, role, "question_ans", history)

        save_debate_message(self.engine, debate_id, question_target_id, answer, "ANSWER")
        logger.info(f"  사용자 질문 답변 완료 (답변 AI: {question_target_id})")
        return answer

    def run_ai_vs_ai_full(
        self,
        debate_id: int,
        policy_card: Dict,
        pro_id: int,
        con_id: int,
        pro_extra: int = 0,
        con_extra: int = 0,
    ) -> Dict:
        positions  = self.run_position_stage(debate_id, policy_card, pro_id, con_id)
        pro_rounds = self.run_base_turns(debate_id, policy_card, "pro", pro_id, con_id)
        pro_extras = [
            self.run_extra_debate(debate_id, policy_card, "pro", pro_id, con_id, i + 1)
            for i in range(min(pro_extra, DEBATE_MAX_EXTRA_ROUNDS))
        ]
        con_rounds = self.run_base_turns(debate_id, policy_card, "con", con_id, pro_id)
        con_extras = [
            self.run_extra_debate(debate_id, policy_card, "con", con_id, pro_id, i + 1)
            for i in range(min(con_extra, DEBATE_MAX_EXTRA_ROUNDS))
        ]

        update_debate_stage(self.engine, debate_id, "summary")
        summary = self.generate_summary(debate_id, policy_card)
        end_debate(self.engine, debate_id)

        return {
            "debate_id":  debate_id,
            "positions":  positions,
            "pro_rounds": pro_rounds,
            "pro_extras": pro_extras,
            "con_rounds": con_rounds,
            "con_extras": con_extras,
            "summary":    summary,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # AI vs User 토론 제어 파트
    # ══════════════════════════════════════════════════════════════════════════

    def create_ai_vs_user(
        self,
        user_id: int,
        selected_policy: str,
        user_stance: str,
        difficulty: str = "hard",
    ) -> Tuple[int, int, int]:
        user_stance_ = "AGREE"    if user_stance == "pro" else "DISAGREE"
        ai_stance    = "DISAGREE" if user_stance == "pro" else "AGREE"

        debate_id = create_debate(self.engine, "AI vs 사용자", selected_policy)
        user_p_id = add_debate_participant(
            self.engine, debate_id, "USER", user_stance_, "사용자",
            user_id=user_id, difficulty=difficulty,
        )
        ai_p_id = add_debate_participant(
            self.engine, debate_id, "AI",
            ai_stance, "찬성 AI" if ai_stance == "AGREE" else "반대 AI",
            difficulty=difficulty,
        )
        logger.info(f"AI vs User 토론방 생성: #{debate_id} (사용자={user_stance}, 난이도={difficulty})")
        return debate_id, user_p_id, ai_p_id

    def generate_ai_position(
        self,
        debate_id: int,
        policy_card: Dict,
        ai_participant_id: int,
        difficulty: str = "hard",
    ) -> str:
        update_debate_stage(self.engine, debate_id, "position")
        history = load_debate_history(self.engine, debate_id)
        role    = self._get_ai_role_str(debate_id, ai_participant_id)
        content = self._generate_debate_msg(policy_card, role, "position", history, difficulty)
        save_debate_message(self.engine, debate_id, ai_participant_id, content, "CLAIM")
        return content

    def process_user_claim(
        self,
        debate_id: int,
        user_participant_id: int,
        ai_participant_id: int,
        user_claim: str,
        policy_card: Dict,
        turn_num: int,
        difficulty: str = "hard",
    ) -> str:
        warning = self._validate_user_input(
            user_claim, policy_card.get("title", ""), "사용자 세부주장"
        )
        if warning:
            return warning

        user_claim = user_claim[:DEBATE_MAX_CHARS_PER_TURN]
        save_debate_message(
            self.engine, debate_id, user_participant_id, user_claim, "CLAIM", turn_num
        )

        history  = load_debate_history(self.engine, debate_id)
        ai_role  = self._get_ai_role_str(debate_id, ai_participant_id)
        ai_reb   = self._generate_debate_msg(
            policy_card, ai_role, "rebuttal", history, difficulty
        )
        save_debate_message(
            self.engine, debate_id, ai_participant_id, ai_reb, "REBUTTAL", turn_num
        )
        logger.info(f"  사용자 라운드 {turn_num}턴 - 사용자 주장 + AI 반박 완료")
        return ai_reb

    def save_user_response(
        self,
        debate_id: int,
        user_participant_id: int,
        user_response: str,
        turn_num: int,
        policy_title: str = "",
    ) -> Optional[str]:
        warning = self._validate_user_input(user_response, policy_title, "사용자 답변")
        if warning:
            return warning

        user_response = user_response[:DEBATE_MAX_CHARS_PER_TURN]
        save_debate_message(
            self.engine, debate_id, user_participant_id, user_response, "ANSWER", turn_num
        )
        logger.info(f"  사용자 라운드 {turn_num}턴 - 사용자 답변 저장 완료")
        return None

    def generate_ai_claim(
        self,
        debate_id: int,
        ai_participant_id: int,
        policy_card: Dict,
        turn_num: int,
        difficulty: str = "hard",
    ) -> str:
        history = load_debate_history(self.engine, debate_id)
        role    = self._get_ai_role_str(debate_id, ai_participant_id)
        content = self._generate_debate_msg(policy_card, role, "argument", history, difficulty)
        save_debate_message(
            self.engine, debate_id, ai_participant_id, content, "CLAIM", turn_num
        )
        logger.info(f"  AI 라운드 {turn_num}턴 - AI 세부주장 생성 완료")
        return content

    def process_user_rebuttal(
        self,
        debate_id: int,
        user_participant_id: int,
        ai_participant_id: int,
        user_rebuttal: str,
        policy_card: Dict,
        turn_num: int,
        difficulty: str = "hard",
    ) -> str:
        warning = self._validate_user_input(
            user_rebuttal, policy_card.get("title", ""), "사용자 반박"
        )
        if warning:
            return warning

        user_rebuttal = user_rebuttal[:DEBATE_MAX_CHARS_PER_TURN]
        save_debate_message(
            self.engine, debate_id, user_participant_id, user_rebuttal, "REBUTTAL", turn_num
        )

        history = load_debate_history(self.engine, debate_id)
        ai_role = self._get_ai_role_str(debate_id, ai_participant_id)
        ai_ans  = self._generate_debate_msg(
            policy_card, ai_role, "response", history, difficulty
        )
        save_debate_message(
            self.engine, debate_id, ai_participant_id, ai_ans, "ANSWER", turn_num
        )
        logger.info(f"  AI 라운드 {turn_num}턴 - 사용자 반박 + AI 답변 완료")
        return ai_ans

    def process_user_message(
        self,
        debate_id: int,
        user_participant_id: int,
        ai_participant_id: int,
        user_message: str,
        policy_card: Dict,
        msg_type: str,
        turn_num: int,
        difficulty: str = "hard",
    ) -> str:
        if msg_type == "rebuttal":
            return self.process_user_rebuttal(
                debate_id=debate_id,
                user_participant_id=user_participant_id,
                ai_participant_id=ai_participant_id,
                user_rebuttal=user_message,
                policy_card=policy_card,
                turn_num=turn_num,
                difficulty=difficulty,
            )
        else:
            return self.process_user_claim(
                debate_id=debate_id,
                user_participant_id=user_participant_id,
                ai_participant_id=ai_participant_id,
                user_claim=user_message,
                policy_card=policy_card,
                turn_num=turn_num,
                difficulty=difficulty,
            )

    def run_extra_debate_user_round(
        self,
        debate_id: int,
        user_participant_id: int,
        ai_participant_id: int,
        policy_card: Dict,
        extra_count: int = 1,
        difficulty: str = "hard",
    ) -> str:
        if extra_count > DEBATE_MAX_EXTRA_ROUNDS:
            return f"⛔ 추가 토론은 최대 {DEBATE_MAX_EXTRA_ROUNDS}회입니다."

        history = load_debate_history(self.engine, debate_id)
        ai_role = self._get_ai_role_str(debate_id, ai_participant_id)
        reb     = self._generate_debate_msg(policy_card, ai_role, "extra_reb", history, difficulty)
        save_debate_message(self.engine, debate_id, ai_participant_id, reb, "REBUTTAL")

        logger.info(f"  사용자 라운드 추가 토론 {extra_count}회 - AI 추가 반박 완료")
        return reb

    def run_extra_debate_ai_round(
        self,
        debate_id: int,
        user_participant_id: int,
        ai_participant_id: int,
        user_rebuttal: str,
        policy_card: Dict,
        extra_count: int = 1,
        difficulty: str = "hard",
    ) -> str:
        if extra_count > DEBATE_MAX_EXTRA_ROUNDS:
            return f"⛔ 추가 토론은 최대 {DEBATE_MAX_EXTRA_ROUNDS}회입니다."

        warning = self._validate_user_input(
            user_rebuttal, policy_card.get("title", ""), "사용자 반박"
        )
        if warning:
            return warning

        user_rebuttal = user_rebuttal[:DEBATE_MAX_CHARS_PER_TURN]
        save_debate_message(self.engine, debate_id, user_participant_id, user_rebuttal, "REBUTTAL")

        history = load_debate_history(self.engine, debate_id)
        ai_role = self._get_ai_role_str(debate_id, ai_participant_id)
        ans     = self._generate_debate_msg(policy_card, ai_role, "extra_res", history, difficulty)
        save_debate_message(self.engine, debate_id, ai_participant_id, ans, "ANSWER")

        logger.info(f"  AI 라운드 추가 토론 {extra_count}회 - AI 추가 답변 완료")
        return ans

    # ══════════════════════════════════════════════════════════════════════════
    # 주장 다지기 보고서 빌드
    # ══════════════════════════════════════════════════════════════════════════

    def generate_summary(self, debate_id: int, policy_card: Dict) -> Dict:
        history = load_debate_history(self.engine, debate_id)
        has_user = any(r.get("participant_type") == "USER" for r in history)
        user_ctx = (
            "사용자가 참여한 토론입니다. "
            "user_feedback.strong_points에 사용자가 효과적으로 반박한 부분, "
            "user_feedback.weak_points에 보완이 필요한 부분을 반드시 작성하세요."
            if has_user else ""
        )

        transcript = "\n".join([
            f"[{r['role_name']} / {r['message_type']}] {r['content'][:200]}"
            for r in history
        ])[:4000]

        try:
            raw = llm(
                [{"role": "user", "content": _SUMMARY_PROMPT.format(
                    policy_title=policy_card.get("title", ""),
                    user_context=user_ctx,
                    transcript=transcript,
                )}],
                self.client, max_tokens=900, json_mode=True,
            )
            summary = json.loads(raw)
        except Exception as e:
            logger.warning(f"주장 다지기 생성 실패: {e}")
            summary = {"error": str(e)}

        first_ai = next((r for r in history if r.get("participant_type") == "AI"), None)
        if first_ai:
            save_debate_message(
                self.engine, debate_id,
                first_ai.get("participant_id", 0),
                json.dumps(summary, ensure_ascii=False),
                "SUMMARY",
            )
        return summary

    def get_history(self, debate_id: int) -> List[Dict]:
        return load_debate_history(self.engine, debate_id)

    def list_user_debates(self, user_id: int) -> List[Dict]:
        return list_debates(self.engine, user_id)

    # ══════════════════════════════════════════════════════════════════════════
    # 내부 헬퍼 (Qdrant 통합 검색 인터페이스 결합 파트)
    # ══════════════════════════════════════════════════════════════════════════

    def _validate_user_input(self, text: str, policy_title: str, input_label: str = "입력") -> Optional[str]:
        blocked, _ = self._check_profanity(text)
        if blocked:
            return "⚠️ 부적절한 표현이 포함되어 있어 답변할 수 없습니다."

        if policy_title:
            relevant, _ = self._check_topic_relevance(text, policy_title)
            if not relevant:
                return f"💬 {input_label}은(는) 선택한 정책 주제와 관련된 내용이어야 합니다."

        return None

    def _generate_debate_msg(
        self,
        policy_card: Dict,
        role: str,
        msg_type: str,
        history: List[Dict],
        difficulty: str = "hard",
    ) -> str:
        base_system = _PRO_SYSTEM if role == "pro" else _CON_SYSTEM
        if difficulty == "easy":
            base_system += _EASY_SUFFIX

        evidence = self._retrieve_evidence(policy_card.get("title", ""))

        policy_ctx = (
            f"[정책 정보]\n제목: {policy_card.get('title','')}\n"
            f"요약: {' '.join(policy_card.get('summary_points', []))[:300]}\n"
            f"배경: {str(policy_card.get('background', policy_card.get('CORE', '')))[:600]}\n\n"
            f"[관련 자료 (출처 표기에 활용)]\n{evidence}"
        )

        messages = [{"role": "system", "content": base_system + "\n\n" + policy_ctx}]
        for turn in history[-8:]:
            is_same_side = (
                turn.get("participant_type") == "AI" and
                turn.get("stance", "").lower() == ("agree" if role == "pro" else "disagree")
            )
            r = "assistant" if is_same_side else "user"
            messages.append({"role": r, "content": turn["content"][:300]})

        messages.append({"role": "user", "content": _TYPE_INSTRUCTION.get(msg_type, "")})
        return llm(messages, self.client, max_tokens=350)

    def _retrieve_evidence(self, query: str, top_k: int = 5) -> str:
        """
        🔧 [정밀 교정] 기사 + 정책 + 법안 통합 Qdrant 하이브리드 검색 (retrieve_all 직결)
        """
        try:
            # 기존 Chroma용 retrieve_for_debate 명세를 완벽히 차단하고
            # 마이그레이션된 vectordb_qdrant의 순정 retrieve_all(query, client, model_key, top_k) 메서드 구동
            results = retrieve_all(
                query=query,
                client=self.chroma,       # app.py에서 캐시 주입한 qdrant_client
                model_key=self.model_key, # "ko-sroberta" 768d dense 통로
                top_k=top_k
            )
            if not results:
                return "관련 자료 없음"

            lines = []
            for r in results:
                # payload 정보 안전 맵핑
                meta     = r.get("metadata", {})
                doc_type = meta.get("doc_type", "news")  # vectordb_qdrant 규격 싱크
                url      = meta.get("source_url", meta.get("url", ""))
                snippet  = r.get("content", "")[:200]

                if doc_type == "policies":
                    title  = meta.get("title", "")
                    dept   = meta.get("department", "")
                    cat    = meta.get("category", "")
                    lines.append(
                        f"[정책] {title} ({cat}) · {dept}\n"
                        f"  내용: {snippet}\n"
                        f"  출처: {url or '(출처 없음)'}"
                    )
                elif doc_type == "bills":
                    title    = meta.get("title", "")
                    bill_num = meta.get("bill_num", "")
                    dept     = meta.get("department", "")
                    lines.append(
                        f"[법안] {title} (의안번호 {bill_num}) · {dept}\n"
                        f"  내용: {snippet}\n"
                        f"  출처: {url or '(출처 없음)'}"
                    )
                else:  # news / article
                    pub  = meta.get("publisher", meta.get("press", "?"))
                    date = str(meta.get("published_at", ""))[:10]
                    lines.append(
                        f"[기사] {pub} ({date})\n"
                        f"  내용: {snippet}\n"
                        f"  출처: {url or '(출처 없음)'}"
                    )

            return "\n\n".join(lines)
        except Exception as e:
            logger.warning(f"토론 RAG 증거 확보 실패: {e}")
            return "관련 자료 없음"

    def _get_ai_role_str(self, debate_id: int, ai_participant_id: int) -> str:
        history = load_debate_history(self.engine, debate_id)
        for r in history:
            if r.get("participant_id") == ai_participant_id:
                return "pro" if r.get("stance", "") == "AGREE" else "con"
        return "pro"

    def _check_profanity(self, text: str) -> Tuple[bool, str]:
        try:
            raw = llm(
                [{"role": "user", "content": _PROFANITY_PROMPT.format(user_input=text)}],
                self.client, model=LLM_MODEL_FAST, max_tokens=80, json_mode=True,
            )
            result = json.loads(raw)
            return result.get("contains_profanity", False), result.get("reason", "")
        except Exception:
            return False, ""

    def _check_topic_relevance(self, text: str, policy_title: str) -> Tuple[bool, str]:
        try:
            raw = llm(
                [{"role": "user", "content": _TOPIC_CHECK_PROMPT.format(
                    policy_title=policy_title, user_input=text
                )}],
                self.client, model=LLM_MODEL_FAST, max_tokens=80, json_mode=True,
            )
            result = json.loads(raw)
            return result.get("is_relevant", True), result.get("reason", "")
        except Exception:
            return True, ""