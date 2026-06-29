from typing import Annotated, Dict, List, Optional, TypedDict
import operator


class PolicyCardState(TypedDict, total=False):
    # ── 입력 ──────────────────────────────────────────────────────────────────
    source:           Dict          # gov24 정책 데이터
    related_articles: List[Dict]    # 관련 뉴스 (stance 포함)
    related_laws:     List[Dict]    # 관련 법령 조문
    card_type:        str           # "POLICY" | "BILL"

    # ── 사실 추출 ─────────────────────────────────────────────────────────────
    policy_facts:     Dict          # 정책 핵심 사실
    article_facts:    List[Dict]    # 기사별 핵심 사실

    # ── SUMMARY 탭 ────────────────────────────────────────────────────────────
    summary:          Dict

    # ── 병렬 생성 결과 수집 ────────────────────────────────────────────────────
    parallel_results: Annotated[List[Dict], operator.add]

    # ── 편향 로그 ─────────────────────────────────────────────────────────────
    bias_log:         Annotated[Dict, lambda a, b: {**a, **b}]

    # ── 제어 ──────────────────────────────────────────────────────────────────
    bias_skip_card:   bool
    save_to_db:       bool

    # ── 토론 주제 ─────────────────────────────────────────────────────────────
    debate_topic:     Optional[str]

    # ── 최종 결과 ─────────────────────────────────────────────────────────────
    card_data:        Optional[Dict]
    card_id:          Optional[int]
    error:            Optional[str]