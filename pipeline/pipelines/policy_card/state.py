from typing import Dict, List, Optional, TypedDict

class PolicyCardState(TypedDict, total=False):
    # ── 입력 ──────────────────────────────────────────────────────────────────
    source:           Dict
    related_articles: List[Dict]
    card_type:        str

    # ── 사실 추출 ─────────────────────────────────────────────────────────────
    policy_facts:     Dict
    article_facts:    List[Dict]

    # ── SUMMARY 탭 ────────────────────────────────────────────────────────────
    summary:          Dict

    # ── CORE 탭 (작성 및 평가) ───────────────────────────────────────────────
    core_content:     str
    discussion_question: str
    core_retry:       int
    core_passed:      bool          # 퀄리티+편향 모두 통과했는지 여부
    core_feedback:    str           # Critic의 수정 지시사항

    # ── OPINION 탭 (작성 및 평가) ────────────────────────────────────────────
    perspectives:           List[Dict]
    perspectives_retry:     int
    perspectives_passed:    bool
    perspectives_feedback:  str

    # ── 편향 로그 ─────────────────────────────────────────────────────────────
    bias_log:         Dict

    # ── 제어 ──────────────────────────────────────────────────────────────────
    bias_skip_card:   bool
    save_to_db:       bool

    # ── 최종 결과 ─────────────────────────────────────────────────────────────
    card_data:        Optional[Dict]
    card_id:          Optional[int]
    error:            Optional[str]