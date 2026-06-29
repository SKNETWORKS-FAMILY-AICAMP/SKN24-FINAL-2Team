"""
storage/rds.py
RDS MySQL 연결 + insert/select 공통 모듈

테이블 구조 (ERD 기준):
  RAW_DATAS:    data_id, category_id(FK), data_title, zsource_url, collected_at, updated_at
  RAW_ARTICLES: data_id(FK), press, published_at 
"""
import csv
import logging
from datetime import datetime, date as date_type
from email.utils import parsedate
from pathlib import Path

import pymysql
import pymysql.cursors

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# 연결
# ══════════════════════════════════════════════════════════════════════════

def get_connection(host: str, port: int, db: str, user: str, password: str):
    return pymysql.connect(
        host        = host,
        port        = port,
        database    = db,
        user        = user,
        password    = password,
        charset     = "utf8mb4",
        cursorclass = pymysql.cursors.DictCursor,
    )


# ══════════════════════════════════════════════════════════════════════════
# 유틸
# ══════════════════════════════════════════════════════════════════════════

def parse_pub_date(raw: str) -> str | None:
    """'Mon, 06 Jun 2026 12:00:00 +0900' → '2026-06-06'"""
    try:
        parsed = parsedate(raw)
        if parsed:
            return datetime(*parsed[:3]).strftime("%Y-%m-%d")
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════
# 중복 URL 조회
# ══════════════════════════════════════════════════════════════════════════

def get_existing_urls(conn) -> set[str]:
    """RAW_DATAS에 이미 있는 source_url 전체 반환."""
    with conn.cursor() as cur:
        cur.execute("SELECT source_url FROM RAW_DATAS")
        return {row["source_url"] for row in cur.fetchall()}


# ══════════════════════════════════════════════════════════════════════════
# RAW_DATAS + RAW_ARTICLES insert
# ══════════════════════════════════════════════════════════════════════════

def insert_articles(conn, articles: list[dict]) -> list[dict]:
    """
    RAW_DATAS + RAW_ARTICLES insert.
    source_url 중복 스킵.
    반환: data_id가 채워진 articles (메타데이터 CSV + Qdrant용)

    ERD 기준:
      RAW_DATAS.category_id  → GPT 분류값 (기사 단위)
      RAW_DATAS.file_path    → 전처리 JSON 파일 경로
      RAW_ARTICLES           → press, published_at만 저장 (본문은 Qdrant 관리)
    """
    inserted = 0
    result   = []

    with conn.cursor() as cur:
        cur.execute("SELECT source_url FROM RAW_DATAS")
        existing = {row["source_url"] for row in cur.fetchall()}

        for art in articles:
            # clean JSON: source_url / data_title / published_at
            # 구버전 호환: url / title / date
            url = (art.get("source_url") or art.get("url", ""))[:500]
            if not url or url in existing:
                continue

            try:
                # RAW_DATAS insert
                cur.execute(
                    """
                    INSERT INTO RAW_DATAS
                        (data_title, category_id, source_url, collected_at, updated_at)
                    VALUES (%s, %s, %s, NOW(), NOW())
                    """,
                    (
                        (art.get("data_title") or art.get("title", ""))[:255],
                        art.get("category_id", 5),
                        url,
                    ),
                )
                data_id = cur.lastrowid

                # RAW_ARTICLES insert (본문은 Qdrant 관리)
                pub_date = (
                    art.get("published_at")
                    or parse_pub_date(art.get("date", ""))
                )
                cur.execute(
                    """
                    INSERT INTO RAW_ARTICLES (data_id, press, published_at)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        data_id,
                        art.get("press", art.get("publisher", ""))[:100],
                        pub_date,
                    ),
                )

                existing.add(url)
                art_out            = dict(art)
                art_out["data_id"] = data_id
                result.append(art_out)
                inserted += 1
                conn.commit()

            except pymysql.err.IntegrityError:
                conn.rollback()
                continue
            except Exception as e:
                conn.rollback()
                logger.warning(f"[RDS] insert 실패 ({url[:60]}): {e}")

    logger.info(f"[RDS] insert 완료: {inserted}건 / 전체 {len(articles)}건")
    return result


# ══════════════════════════════════════════════════════════════════════════
# 메타데이터 CSV 저장
# ══════════════════════════════════════════════════════════════════════════

METADATA_FIELDNAMES = [
    "data_id", "keyword", "data_title", "category_id", "category_name",
    "press", "source_url", "published_at", "collected_at", "updated_at",
]

CATEGORY_MAP = {
    1: "일자리", 2: "주거", 3: "교육",
    4: "금융",  5: "생활복지", 6: "문화",
}


def save_metadata_csv(articles: list[dict], output_dir: Path) -> Path:
    """메타데이터 CSV 누적 저장."""
    today      = date_type.today().strftime("%Y-%m-%d")
    csv_path   = output_dir / "news_metadata.csv"
    file_exists = csv_path.exists()

    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=METADATA_FIELDNAMES)
        if not file_exists:
            writer.writeheader()

        for art in articles:
            cat_id = art.get("category_id", 5)
            writer.writerow({
                "data_id":       art.get("data_id", ""),
                "keyword":       art.get("keyword", ""),
                "data_title":    (art.get("data_title") or art.get("title", "")),
                "category_id":   cat_id,
                "category_name": CATEGORY_MAP.get(cat_id, ""),
                "press":         art.get("press", ""),
                "source_url":    (art.get("source_url") or art.get("url", "")),
                "published_at":  art.get("published_at") or parse_pub_date(art.get("date", "")) or "",
                "collected_at":  art.get("collected_at", today),
                "updated_at":    today,
            })

    logger.info(f"[메타데이터] CSV 저장 → {csv_path.name} ({len(articles)}건 append)")
    return csv_path


# ══════════════════════════════════════════════════════════════════════════
# 카드 생성 트리거
# ══════════════════════════════════════════════════════════════════════════

def count_articles_by_category(conn, category_id: int) -> int:
    """category_id 기준 누적 기사 수 조회."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM RAW_DATAS WHERE category_id = %s",
            (category_id,),
        )
        row = cur.fetchone()
        return row["cnt"] if row else 0


def get_articles_by_category(conn, category_id: int) -> list[dict]:
    """카드 생성용 — category_id 기준 기사 전체 조회."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                rd.data_id,
                rd.data_title   AS title,
                rd.source_url   AS url,
                rd.category_id,
                rd.collected_at,
                ra.press,
                ra.published_at
            FROM RAW_DATAS rd
            LEFT JOIN RAW_ARTICLES ra ON rd.data_id = ra.data_id
            WHERE rd.category_id = %s
            ORDER BY ra.published_at DESC
            """,
            (category_id,),
        )
        return cur.fetchall()
    
def load_keyword_cache(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT keyword, search_query, last_collected FROM NEWS_KEYWORDS")
        rows = cur.fetchall()
    return {
        row["keyword"]: {
            "search_query":   row["search_query"],
            "last_collected": str(row["last_collected"]),
        }
        for row in rows
    }

def upsert_keyword_cache(conn, keyword: str, search_query: str, last_collected: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO NEWS_KEYWORDS (keyword, search_query, last_collected)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                search_query   = VALUES(search_query),
                last_collected = VALUES(last_collected),
                updated_at     = NOW()
            """,
            (keyword, search_query, last_collected),
        )
    conn.commit()

def load_seen_policy_ids(conn) -> set:
    """POLICY_SEEN 전체 로드 → {service_id, ...}"""
    with conn.cursor() as cur:
        cur.execute("SELECT service_id FROM POLICY_IDS")
        rows = cur.fetchall()
    return {row["service_id"] for row in rows}

def upsert_seen_policies(conn, service_ids: list[str]) -> None:
    """신규 service_id 일괄 INSERT (중복 무시)"""
    if not service_ids:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT IGNORE INTO POLICY_IDS (service_id)
            VALUES (%s)
            """,
            [(sid,) for sid in service_ids],
        )
    conn.commit()