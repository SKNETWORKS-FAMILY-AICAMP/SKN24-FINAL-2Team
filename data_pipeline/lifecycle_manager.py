"""
데이터 수명주기 관리
- 신규 / 업데이트 / 변경없음 분류
- 만료 데이터 처리
"""
import logging
from typing import Dict, List, Tuple

from storage.policy_rds_handler import db_cursor, run_expiry_jobs

logger = logging.getLogger(__name__)




# ══════════════════════════════════════════════════════════════════════════
# 뉴스 수명주기
# ══════════════════════════════════════════════════════════════════════════

def classify_articles(articles: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """뉴스 → 신규 / 중복 (append-only, 업데이트 없음)"""
    new, duplicates = [], []

    with db_cursor() as cursor:
        for article in articles:
            url = article.get("url", "")
            if not url:
                continue
            cursor.execute(
                """
                SELECT rd.data_id
                  FROM RAW_DATAS rd
                  JOIN RAW_ARTICLES ra ON rd.data_id = ra.data_id
                 WHERE rd.source_url = %s
                """,
                (url,)
            )
            if cursor.fetchone():
                duplicates.append(article)
            else:
                new.append(article)

    logger.info(f"[Lifecycle] 뉴스 — 신규: {len(new)}, 중복: {len(duplicates)}")
    return new, duplicates


# ══════════════════════════════════════════════════════════════════════════
# 정책 수명주기
# ══════════════════════════════════════════════════════════════════════════

def classify_policies(policies: List[Dict]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """정책 → 신규 / 업데이트 / 변경없음 (업데이트 기준: apply_end_date 변경)"""
    new, updated, unchanged = [], [], []

    with db_cursor() as cursor:
        for policy in policies:
            service_id = policy.get("service_id", "")
            if not service_id:
                continue
            cursor.execute(
                "SELECT data_id FROM RAW_POLICIES WHERE service_id = %s", (service_id,)
            )
            existing = cursor.fetchone()
            if not existing:
                new.append(policy)
            else:
                # apply_period는 텍스트라 변경 감지 없음 → unchanged 처리
                unchanged.append(policy)

    logger.info(f"[Lifecycle] 정책 — 신규: {len(new)}, 업데이트: {len(updated)}, 변경없음: {len(unchanged)}")
    return new, updated, unchanged


# ══════════════════════════════════════════════════════════════════════════
# 만료 처리
# ══════════════════════════════════════════════════════════════════════════

def run_expiry() -> None:
    """뉴스 30일 초과 / 정책 apply_end_date 지난 것 삭제"""
    result = run_expiry_jobs()
    logger.info(f"[Lifecycle] 만료 처리 — 뉴스: {result['articles']}건, 정책: {result['policies']}건 삭제")