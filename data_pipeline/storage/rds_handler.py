"""
RDS MySQL 핸들러
MOCK_MODE=true 시 실제 DB 연결 없이 로그만 출력
"""
import json
import logging
from contextlib import contextmanager
from datetime import date
from typing import Optional

from config import (
    MOCK_MODE,
    RDS_DATABASE,
    RDS_HOST,
    RDS_PASSWORD,
    RDS_PORT,
    RDS_USER,
)

logger = logging.getLogger(__name__)

CATEGORY_MAP = {
    "일자리": 1,
    "교육":   2,
    "주거":   3,
    "금융":   4,
    "생활복지": 5,
    "복지":   5,
    "문화":   6,
}


# ══════════════════════════════════════════════════════════════════════════
# 커넥션 관리
# ══════════════════════════════════════════════════════════════════════════

def get_connection():
    if MOCK_MODE:
        raise RuntimeError("목업 모드에서는 get_connection() 직접 호출 불가")
    import pymysql
    import pymysql.cursors
    return pymysql.connect(
        host=RDS_HOST,
        port=RDS_PORT,
        user=RDS_USER,
        password=RDS_PASSWORD,
        database=RDS_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


@contextmanager
def db_cursor():
    if MOCK_MODE:
        class MockCursor:
            def execute(self, *a, **k): pass
            def fetchone(self): return None
            def fetchall(self): return []
            lastrowid = 0
        yield MockCursor()
        return

    conn = get_connection()
    try:
        with conn.cursor() as cursor:
            yield cursor
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"[RDS] 트랜잭션 롤백: {e}")
        raise
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# DATA_KEYWORDS 초기화 / 조회
# ══════════════════════════════════════════════════════════════════════════

def init_keywords() -> None:
    if MOCK_MODE:
        return

    keywords = [
        (1, "일자리"),
        (2, "교육"),
        (3, "주거"),
        (4, "금융"),
        (5, "생활복지"),
        (6, "문화"),
    ]

    with db_cursor() as cursor:
        for category_id, keyword_name in keywords:
            cursor.execute(
                "SELECT keyword_id FROM DATA_KEYWORDS WHERE category_id=%s AND keyword_name=%s",
                (category_id, keyword_name)
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO DATA_KEYWORDS (category_id, keyword_name) VALUES (%s, %s)",
                    (category_id, keyword_name)
                )
    logger.info("[RDS] DATA_KEYWORDS 초기화 완료")


def get_keyword_id(category_id: int) -> Optional[int]:
    if MOCK_MODE:
        return 0

    with db_cursor() as cursor:
        cursor.execute(
            "SELECT keyword_id FROM DATA_KEYWORDS WHERE category_id=%s LIMIT 1",
            (category_id,)
        )
        row = cursor.fetchone()
        return row["keyword_id"] if row else None


# ══════════════════════════════════════════════════════════════════════════
# RAW_DATAS
# ══════════════════════════════════════════════════════════════════════════

def upsert_raw_data(cursor, *, data_title, category_id, file_path,
                    source_url="", collected_at=None, updated_at=None) -> int:
    if MOCK_MODE:
        logger.info(f"[RDS][MOCK] RAW_DATAS upsert: {data_title[:40]}")
        return 0

    collected_at = collected_at or date.today()
    updated_at   = updated_at   or date.today()

    keyword_id = get_keyword_id(category_id)

    cursor.execute(
        "SELECT data_id FROM RAW_DATAS WHERE data_title = %s AND file_path = %s",
        (data_title, file_path,)
    )
    existing = cursor.fetchone()

    if existing:
        cursor.execute(
            "UPDATE RAW_DATAS SET data_title=%s, keyword_id=%s, source_url=%s, updated_at=%s WHERE data_id=%s",
            (data_title, keyword_id, source_url, updated_at, existing["data_id"])
        )
        return existing["data_id"]

    cursor.execute(
        "INSERT INTO RAW_DATAS (data_title, keyword_id, file_path, source_url, collected_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s)",
        (data_title, keyword_id, file_path, source_url, collected_at, updated_at)
    )
    return cursor.lastrowid


# ══════════════════════════════════════════════════════════════════════════
# RAW_ARTICLES
# ══════════════════════════════════════════════════════════════════════════

def upsert_article(*, data_title, category_id, s3_path, source_url="",
                   press="", published_at=None, collected_at=None, full_article="") -> Optional[int]:
    if MOCK_MODE:
        logger.info(f"[RDS][MOCK] RAW_ARTICLES upsert: {data_title[:40]}")
        return 0

    with db_cursor() as cursor:
        cursor.execute(
            "SELECT rd.data_id FROM RAW_DATAS rd JOIN RAW_ARTICLES ra ON rd.data_id=ra.data_id WHERE rd.source_url=%s",
            (source_url,)
        )
        if cursor.fetchone():
            return None

        data_id = upsert_raw_data(cursor, data_title=data_title, category_id=category_id,
                                   file_path=s3_path, source_url=source_url,
                                   collected_at=collected_at)
        cursor.execute(
            "INSERT INTO RAW_ARTICLES (data_id, press, published_at, full_article) VALUES (%s,%s,%s,%s)",
            (data_id, press, published_at, full_article)
        )
        logger.info(f"[RDS] RAW_ARTICLES INSERT: {data_title[:40]}")
        return data_id


# ══════════════════════════════════════════════════════════════════════════
# RAW_POLICIES
# ══════════════════════════════════════════════════════════════════════════

def upsert_policy(*, data_title, category_id, s3_path, source_url="",
                  department="", apply_period=None, policy_law=None,
                  full_policy="", collected_at=None, updated_at=None) -> int:
    if MOCK_MODE:
        logger.info(f"[RDS][MOCK] RAW_POLICIES upsert: {data_title[:40]}")
        return 0

    policy_law_json = json.dumps(policy_law, ensure_ascii=False) if policy_law and not isinstance(policy_law, str) else policy_law

    with db_cursor() as cursor:
        data_id = upsert_raw_data(cursor, data_title=data_title, category_id=category_id,
                                   file_path=s3_path, source_url=source_url,
                                   collected_at=collected_at, updated_at=updated_at)
        cursor.execute(
            """INSERT INTO RAW_POLICIES
               (data_id, department, apply_period, policy_law, full_policy)
               VALUES (%s,%s,%s,%s,%s)""",
            (data_id, department, apply_period, policy_law_json, full_policy)
        )
        logger.info(f"[RDS] RAW_POLICIES INSERT: {data_title[:40]}")
        return data_id


# ══════════════════════════════════════════════════════════════════════════
# 만료 처리
# ══════════════════════════════════════════════════════════════════════════

def expire_old_articles(days: int = 30) -> int:
    if MOCK_MODE:
        logger.info(f"[RDS][MOCK] 뉴스 만료 처리 스킵 ({days}일 초과)")
        return 0
    with db_cursor() as cursor:
        cursor.execute(
            "DELETE rd FROM RAW_DATAS rd JOIN RAW_ARTICLES ra ON rd.data_id=ra.data_id WHERE rd.collected_at < DATE_SUB(CURDATE(), INTERVAL %s DAY)",
            (days,)
        )
        return cursor.rowcount


def expire_old_policies() -> int:
    logger.info("[RDS] 정책 만료 처리 없음 (apply_end_date 컬럼 미사용)")
    return 0


def run_expiry_jobs() -> dict:
    return {
        "articles": expire_old_articles(days=30),
        "policies": expire_old_policies(),
    }


def resolve_category_id(category_str: str) -> Optional[int]:
    return CATEGORY_MAP.get(category_str)