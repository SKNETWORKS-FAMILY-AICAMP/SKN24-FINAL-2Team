from __future__ import annotations

import json
import logging
import re
from typing import Dict, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# POLICITY 토론 태도 헌법 (Constitutional AI)
# ══════════════════════════════════════════════════════════════════════════════
_CONSTITUTION = """
너는 청년 정책 플랫폼 'POLICITY'의 상호존중 토론 문화를 수호하는 엄격한 가드레일 재판관이다.
너는 문장의 '정치적 입장(찬성/반대)'을 절대로 검열하지 않으며, 오직 '토론 태도와 논리적 격식'만을 기준으로 판결한다.

[POLICITY 토론 태도 헌법]

제1조 (표현의 자유와 입장 존중 - PASSED 기준)
- 사용자는 어떤 정책에 대해서도 자유롭게 찬성 또는 반대 입장을 표명할 수 있다.
- 특정 진영의 논리를 대변하거나 한쪽 입장에 치우친 주장이라도, 문체가 정중하고 타인을 공격하지 않는다면 PASS한다.
- 예외: 제3조, 제4조, 제5조의 금지 항목이 단 한 구절이라도 포함되면 즉시 BLOCKED한다.

제2조 (최소한의 논리적 격식 요구 - PASSED 기준)
- 입장을 표명할 때, [이유], [근거], [대안] 중 최소한 하나를 포함하면 '건전한 토론'으로 인정하여 PASSED한다.
- 단, 본 조항은 제3~5조 위반이 없는 경우에만 적용한다.

제3조 (정당·정권·정치세력 평가 및 찬사 금지 - BLOCKED 기준)
- 정책 자체의 장단점 토론은 100% 허용한다.
- 그러나 특정 정당, 정권, 정치 세력, 성별, 인종, 종교 자체를 주어로 삼아 직접 평가하거나 옹호/비방하면 BLOCKED한다.
- 단, 발의/발표/추진 등 팩트 서술이나 정책 효과에 대한 의견 표명은 PASSED한다.
  * 유능하다/무능하다/위선적이다/뻔뻔하다
  * 진심이다/의지가 분명하다/정치적 계산을 한다
  * 청년들에게 희망을 준다/절망을 준다
  * 정책 철학이 뚜렷하다/철학이 없다 등
- 정치 집단에 대한 은근한 찬사, 맹목적 옹호, 비난의 태도를 금지한다.

제4조 (독단적 정답주의 및 진리 강요 금지 - BLOCKED 기준)
- "유일한 답이다", "절대적인 선이다", "대안이 없다", "반론은 의미 없다"처럼 독단적으로 규정하는 문체는 BLOCKED한다.
- 자신의 의견을 절대적 진리로 강요하는 뉘앙스는 이유 여하를 막론하고 금지한다.

제5조 (정치적 프레이밍 및 상대 낙인 금지 - BLOCKED 기준)
- 반대 의견이나 특정 집단을 "무지함", "악의적임", "열등함", "기득권 편이다" 등으로 낙인짓는 표현은 즉시 BLOCKED한다.
- 노골적 욕설이 없어도 조롱, 비하, 인신공격, 은근히 비꼬는 태도가 감지되면 BLOCKED한다.
"""

_CONSTITUTION_SYSTEM = _CONSTITUTION + """

반드시 아래 JSON만 출력하라. 다른 텍스트 일절 금지.

{
  "decision": "PASSED" 또는 "BLOCKED",
  "violated_article": 위반 조항 번호 (없으면 null),
  "reason": "간단한 이유 (30자 이내)"
}
"""

# ── 기존 bias_check 프롬프트 (유저 주제이탈용, 하위 호환) ──────────────────────

_TOPIC_CHECK_PROMPT = """\
당신은 토론 플랫폼의 입력 필터입니다.

[토론 주제]
{policy_title}

[사용자 입력]
{user_input}

아래 세 가지를 순서대로 검사하세요.

1. 주제 이탈 검사 (가장 먼저):
   - 입력이 토론 주제와 완전히 무관한 경우만 실패 (매우 관대하게 판단)
   - 주제 관련으로 통과시켜야 하는 예시:
     * 정책에 대한 찬성·반대 주장
     * 정책의 효과·부작용·비용에 대한 의견
     * 정책과 관련된 사회·경제적 문제 지적
     * 상대 발언에 대한 반박
   - 일상 대화, 욕설, 정치와 전혀 무관한 내용만 실패

2. 맥락 의존형 혐오 검사:
   - 단어 자체는 일상어지만 정치적 맥락에서 특정 집단을 비하하는 경우 실패
   - 예: 정치 맥락에서 특정 지지자를 '수박'으로 낙인찍는 표현
   - 예: 지역·성별·세대 전체를 싸잡아 부정적으로 단정하는 표현
   - 풍자나 유머처럼 보여도 실제로 집단을 모욕·멸시하는 경우 실패

3. 금칙어 검사:
   - 성적 비하 표현 및 비속어
   - 특정 정치인·정당명을 포함한 비하 표현

반드시 아래 JSON 형식으로만 응답하세요:
{{"passed": true, "violation_type": "none", "message": ""}}
또는
{{"passed": false, "violation_type": "off_topic", "message": "토론 주제와 관련된 내용을 입력해주세요. 이 토론의 주제는 '{policy_title}'입니다."}}
또는
{{"passed": false, "violation_type": "context_hate", "message": "맥락상 특정 집단을 비하하는 표현이 포함되어 있습니다. 구체적인 정책 논거를 사용해주세요."}}
또는
{{"passed": false, "violation_type": "profanity", "message": "부적절한 표현이 포함되어 있습니다. 건전한 토론 문화를 위해 적절한 표현을 사용해주세요."}}"""


# ══════════════════════════════════════════════════════════════════════════════
# ML 분류기 (KR-ELECTRA)
# ══════════════════════════════════════════════════════════════════════════════

_PRESS_NAMES = [
    "한겨레", "중앙일보", "동아일보", "조선일보", "경향신문",
    "한겨레신문", "중앙", "동아", "조선",
]


def _clean_text(text: str) -> str:
    for name in _PRESS_NAMES:
        text = text.replace(name, "")
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", "", text)
    text = re.sub(r".{1,10}\s*(기자|특파원|논설위원|편집위원)", "", text)
    text = re.sub(r"\d{2,4}[-.]?\d{3,4}[-.]?\d{4}", "", text)
    text = re.sub(r"무단\s*전재.{0,20}재배포\s*금지", "", text)
    text = re.sub(r"ⓒ.{0,30}", "", text)
    return re.sub(r"\s+", " ", text).strip()


class BiasClassifier:
    """
    KR-ELECTRA 3진 분류기 (진보=0 / 보수=1 / 중립=2)

    적용 범위:
        - 카드 core_content
        - 카드 youth_connection
        - 챗봇 응답

    사용 예시:
        classifier = BiasClassifier("BarryKim34/kr-electra-political-bias")
        result = classifier.predict("청년 주거 지원 정책은...")
        # {"passed": True, "label": "중립", "confidence": 9.99, "probs": {...}}
    """

    LABEL_MAP = {0: "진보", 1: "보수", 2: "중립"}

    def __init__(self, model_dir: str = "BarryKim34/kr-electra-political-bias-v2",
                 max_length: int = 256):
        try:
            import torch
            import numpy as np
            from transformers import PreTrainedTokenizerFast, AutoModelForSequenceClassification

            self.torch = torch
            self.np    = np
            self.device    = "cuda" if torch.cuda.is_available() else "cpu"
            self.tokenizer = PreTrainedTokenizerFast.from_pretrained(model_dir)
            self.model     = AutoModelForSequenceClassification.from_pretrained(
                model_dir, use_safetensors=True
            )
            self.model.eval()
            self.model.to(self.device)
            self.max_length = max_length
            self._ready = True
            logger.info(f"BiasClassifier 로드 완료: {model_dir} / {self.device}")
        except Exception as e:
            logger.warning(f"BiasClassifier 로드 실패 — ML 검사 비활성화: {e}")
            self._ready = False

    def predict(self, text: str) -> Dict:
        """
        Returns:
            passed:     True if 중립
            label:      "진보" | "보수" | "중립"
            confidence: 0.0 ~ 10.0
            probs:      {"진보": float, "보수": float, "중립": float}
        """
        if not self._ready:
            return {"passed": True, "label": "중립", "confidence": 0.0,
                    "probs": {"진보": 0.0, "보수": 0.0, "중립": 1.0}}

        cleaned = _clean_text(text)
        inputs  = self.tokenizer(
            cleaned, return_tensors="pt",
            truncation=True, max_length=self.max_length,
        ).to(self.device)

        with self.torch.no_grad():
            logits = self.model(**inputs).logits

        probs    = self.torch.softmax(logits, dim=-1)[0].cpu().numpy()
        pred_idx = int(self.np.argmax(probs))
        label    = self.LABEL_MAP[pred_idx]

        return {
            "passed":     label == "중립",
            "label":      label,
            "confidence": round(float(probs[pred_idx]) * 10, 2),
            "probs": {
                "진보": round(float(probs[0]), 4),
                "보수": round(float(probs[1]), 4),
                "중립": round(float(probs[2]), 4),
            },
        }


# ══════════════════════════════════════════════════════════════════════════════
# 내부 헬퍼
# ══════════════════════════════════════════════════════════════════════════════

def _llm_json(messages: list, client: OpenAI, model: str) -> dict:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=0,
    )
    return json.loads(resp.choices[0].message.content)


# ══════════════════════════════════════════════════════════════════════════════
# 공개 함수
# ══════════════════════════════════════════════════════════════════════════════

def check_ai_bias(
    speech: str,
    openai_client: OpenAI,
    llm_model: str = "gpt-4o-mini",
) -> Dict:
    """
    토론 AI 발언의 편향·태도 검토 (Constitutional AI 방식).

    기존 단순 GPT 프롬프트 → POLICITY 토론 태도 헌법 5개 조항 기반으로 교체.
    편향 감지 시 debate 에이전트가 재생성 루프를 트리거한다 (최대 3회).

    Returns
    -------
    {"passed": bool, "reason": str}
    """
    try:
        resp = openai_client.chat.completions.create(
            model=llm_model,
            messages=[
                {"role": "system", "content": _CONSTITUTION_SYSTEM},
                {"role": "user",   "content": f"판결 대상 발언:\n{speech}"},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_completion_tokens=150,
        )
        result = json.loads(resp.choices[0].message.content)
        passed = result.get("decision") == "PASSED"
        return {
            "passed": passed,
            "reason": result.get("reason", ""),
            "violated_article": result.get("violated_article"),
        }
    except Exception as e:
        logger.warning(f"check_ai_bias 오류: {e}")
        return {"passed": True, "reason": f"검토 오류 (통과 처리): {e}",
                "violated_article": None}


def check_content_bias(text: str, classifier: BiasClassifier) -> Dict:
    """
    카드 core_content, youth_connection, 챗봇 응답의 정치 편향 검사.
    KR-ELECTRA ML 분류기 사용.

    Parameters
    ----------
    text       : 검사할 텍스트
    classifier : BiasClassifier 인스턴스 (앱 시작 시 1회 초기화 후 재사용)

    Returns
    -------
    {"passed": bool, "label": str, "confidence": float, "probs": dict}
    """
    return classifier.predict(text)


def check_user_topic(
    user_input: str,
    policy_title: str,
    openai_client: OpenAI,
    llm_model: str = "gpt-4o-mini",
) -> Dict:
    """
    사용자 발언의 주제이탈 + 맥락 의존형 혐오 검사 (기존 LLM 방식 유지).
    사전 필터(hate_detection)를 통과한 후 호출하는 2차 검사.

    Returns
    -------
    {"passed": bool, "violation_type": str, "message": str}
    """
    try:
        prompt = _TOPIC_CHECK_PROMPT.format(
            policy_title=policy_title,
            user_input=user_input,
        )
        result = _llm_json(
            [{"role": "user", "content": prompt}],
            openai_client,
            model=llm_model,
        )
        return {
            "passed":         bool(result.get("passed", True)),
            "violation_type": result.get("violation_type", "none"),
            "message":        result.get("message", ""),
        }
    except Exception as e:
        logger.warning(f"check_user_topic 오류: {e}")
        return {"passed": True, "violation_type": "none", "message": ""}