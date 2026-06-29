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
    "일자리":   1,
    "교육":     2,
    "주거":     3,
    "금융":     4,
    "생활복지": 5,
    "문화":     6,
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
# RAW_DATAS
# data_title = 정책명 or 기사 제목
# category_id = CATEGORIES FK (1~6)
# source_url = 중복 체크 기준
# ══════════════════════════════════════════════════════════════════════════

def upsert_raw_data(cursor, *, data_title: str, category_id: int,
                    source_url: str = "",
                    collected_at=None, updated_at=None) -> int:
    if MOCK_MODE:
        logger.info(f"[RDS][MOCK] RAW_DATAS upsert: {data_title}")
        return 0

    collected_at = collected_at or date.today()
    updated_at   = updated_at   or date.today()

    # source_url이 있을 때만 중복 체크 (없으면 바로 INSERT)
    if source_url:
        cursor.execute(
            "SELECT data_id FROM RAW_DATAS WHERE source_url = %s",
            (source_url,)
        )
        existing = cursor.fetchone()
    else:
        existing = None

    if existing:
        cursor.execute(
            "UPDATE RAW_DATAS SET data_title=%s, category_id=%s, updated_at=%s WHERE data_id=%s",
            (data_title, category_id, updated_at, existing["data_id"])
        )
        return existing["data_id"]

    cursor.execute(
        "INSERT INTO RAW_DATAS (data_title, category_id, source_url, collected_at, updated_at) VALUES (%s,%s,%s,%s,%s)",
        (data_title, category_id, source_url, collected_at, updated_at)
    )
    return cursor.lastrowid


# ══════════════════════════════════════════════════════════════════════════
# RAW_ARTICLES
# ══════════════════════════════════════════════════════════════════════════

def upsert_article(*, keyword_name: str, category_id: int,
                   source_url: str = "", press: str = "",
                   published_at=None, collected_at=None) -> Optional[int]:
    if MOCK_MODE:
        logger.info(f"[RDS][MOCK] RAW_ARTICLES upsert: {keyword_name[:40]}")
        return 0

    with db_cursor() as cursor:
        cursor.execute(
            "SELECT rd.data_id FROM RAW_DATAS rd JOIN RAW_ARTICLES ra ON rd.data_id=ra.data_id WHERE rd.source_url=%s",
            (source_url,)
        )
        if cursor.fetchone():
            return None

        data_id = upsert_raw_data(
            cursor,
            data_title=keyword_name,
            category_id=category_id,
            source_url=source_url,
            collected_at=collected_at,
        )
        cursor.execute(
            "INSERT INTO RAW_ARTICLES (data_id, press, published_at) VALUES (%s,%s,%s)",
            (data_id, press, published_at)
        )
        logger.info(f"[RDS] RAW_ARTICLES INSERT: {keyword_name[:40]}")
        return data_id


# ══════════════════════════════════════════════════════════════════════════
# RAW_POLICIES
# ══════════════════════════════════════════════════════════════════════════

def upsert_policy(*, keyword_name: str, category_id: int,
                  service_id: str = "",
                  source_url: str = "", department: str = "",
                  apply_period=None, policy_law=None,
                  collected_at=None, updated_at=None) -> int:
    if MOCK_MODE:
        logger.info(f"[RDS][MOCK] RAW_POLICIES upsert: {keyword_name[:40]}")
        return 0

    policy_law_json = (
        json.dumps(policy_law, ensure_ascii=False)
        if policy_law and not isinstance(policy_law, str)
        else policy_law
    )

    with db_cursor() as cursor:
        # service_id 기준 중복 체크
        cursor.execute(
            """SELECT rd.data_id FROM RAW_DATAS rd
               JOIN RAW_POLICIES rp ON rd.data_id=rp.data_id
               WHERE rd.data_title = %s""",
            (keyword_name,)
        )
        if cursor.fetchone():
            logger.debug(f"[RDS] RAW_POLICIES 중복 스킵: {keyword_name[:40]}")
            return 0

        data_id = upsert_raw_data(
            cursor,
            data_title=keyword_name,
            category_id=category_id,
            source_url=source_url,
            collected_at=collected_at,
            updated_at=updated_at,
        )

        # data_id가 이미 RAW_POLICIES에 있으면 스킵
        cursor.execute(
            "SELECT data_id FROM RAW_POLICIES WHERE data_id = %s",
            (data_id,)
        )
        if cursor.fetchone():
            logger.debug(f"[RDS] RAW_POLICIES data_id 중복 스킵: {data_id}")
            return data_id

        cursor.execute(
            """INSERT INTO RAW_POLICIES
               (data_id, department, apply_period, policy_law)
               VALUES (%s,%s,%s,%s)""",
            (data_id, department, apply_period, policy_law_json)
        )
        logger.info(f"[RDS] RAW_POLICIES INSERT: {keyword_name[:40]}")
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
