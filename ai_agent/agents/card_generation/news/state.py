from typing import Annotated, Dict, List, Optional, TypedDict
import operator


class NewsCardState(TypedDict, total=False):
    # ── 입력 ──────────────────────────────────────────────────────────────────
    articles: List[Dict]

    # ── 사실 추출 ─────────────────────────────────────────────────────────────
    extracted_facts: List[Dict]

    # ── SUMMARY 탭 ────────────────────────────────────────────────────────────
    summary: Dict

    # ── 토론 주제 (뉴스 카드 전용) ────────────────────────────────────────────
    debate_topic: str

    # ── 병렬 생성 결과 수집 ────────────────────────────────────────────────────
    parallel_results: Annotated[List[Dict], operator.add]

    # ── 편향 로그 ─────────────────────────────────────────────────────────────
    bias_log: Annotated[Dict, lambda a, b: {**a, **b}]

    # ── 제어 ──────────────────────────────────────────────────────────────────
    bias_skip_card: bool
    save_to_db:     bool

    # ── 최종 결과 ─────────────────────────────────────────────────────────────
    card_data: Optional[Dict]
    card_id:   Optional[str]
    error:     Optional[str]