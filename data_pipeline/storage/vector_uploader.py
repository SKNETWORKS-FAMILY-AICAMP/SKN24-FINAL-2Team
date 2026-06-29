"""
벡터 임베딩 + RDS/Qdrant 업로드

역할:
  - 전처리 완료된 processed 데이터를 읽음
  - RunPod API로 임베딩 생성
  - RDS에 메타데이터 저장
  - Qdrant에 벡터 업로드

실행:
  python vector_uploader.py --type policy
  python vector_uploader.py --type news
  python vector_uploader.py --type law
  python vector_uploader.py --type all
"""

import sys
import json
import logging
import argparse
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))

from config import (
    LOG_DIR,
    RUNPOD_ENDPOINT_URL,
    RUNPOD_API_KEY,
    POLICY_PROCESSED_DIR,
    NEWS_PROCESSED_DIR,
    LAWS_PROCESSED_DIR,
    MOCK_MODE,
)

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "vector_uploader.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger("vector_uploader")


# ══════════════════════════════════════════════════════════════════════════
# 공통 유틸
# ══════════════════════════════════════════════════════════════════════════

def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _latest_file(directory: Path, pattern: str) -> Optional[Path]:
    files = sorted(directory.glob(pattern), reverse=True)
    return files[0] if files else None






# ══════════════════════════════════════════════════════════════════════════
# 정책 업로드
# ══════════════════════════════════════════════════════════════════════════

def upload_policies() -> None:
    """
    data/policies/processed/gov24_top5_clean_*.json
    + data/laws/processed/law_grouped_clean.json (법령 JSON → related_laws 컬럼)
    → RDS RAW_POLICIES (related_laws 포함)
    → Qdrant policy (정책 + 법령 조문 합쳐서 임베딩)
    """
    from storage.rds_handler import upsert_policy

    latest = _latest_file(POLICY_PROCESSED_DIR, "gov24_top5_clean_*.json")

    if not latest:
        logger.warning("[Policy] processed JSON 없음")
        return

    logger.info("[Policy] 업로드 대상: %s", latest)

    # 법령 데이터 로드 → service_id 기준으로 인덱싱
    laws_by_service_id = {}
    law_path = LAWS_PROCESSED_DIR / "law_grouped_clean.json"
    if law_path.exists():
        laws = _read_json(law_path)
        for law in laws:
            for related in law.get("관련정책", []):
                sid = related.get("서비스ID", "")
                if sid:
                    laws_by_service_id.setdefault(sid, []).append(law)
        logger.info("[Policy] 법령 데이터 로드 완료: %d개 법령", len(laws))
    else:
        logger.warning("[Policy] law_grouped_clean.json 없음 — 법령 없이 진행")

    data = _read_json(latest)
    count = 0

    for policy in data:
        service_id = policy.get("service_id", "")
        content = policy.get("content", "")

        if not content:
            continue

        # 해당 정책의 관련 법령
        related_laws = laws_by_service_id.get(service_id, []) or None

        upsert_policy(
            data_title=policy.get("title", ""),
            category_id=policy.get("category_id", 5),
            s3_path=policy.get("source_url", ""),
            source_url=policy.get("source_url", ""),
            department=policy.get("department", ""),
            apply_period=policy.get("apply_period"),
            policy_law=related_laws if related_laws else None,
            full_policy=policy.get("content", ""),
        )

        count += 1

    logger.info("[Policy] %d개 정책 RDS 적재 완료", count)


# ══════════════════════════════════════════════════════════════════════════
# 뉴스 업로드
# ══════════════════════════════════════════════════════════════════════════

def _find_latest_cleaned_news() -> Optional[Path]:
    """
    뉴스 processed 파일 탐색.

    지원 구조 1:
      data/news/processed/cleaned_news_YYYYMMDD.jsonl

    지원 구조 2:
      data/news/processed/YYYYMMDD/cleaned_news.jsonl
    """
    direct = _latest_file(NEWS_PROCESSED_DIR, "cleaned_news_*.jsonl")
    if direct:
        return direct

    date_dirs = sorted([p for p in NEWS_PROCESSED_DIR.iterdir() if p.is_dir()], reverse=True)

    for d in date_dirs:
        candidate = d / "cleaned_news.jsonl"
        if candidate.exists():
            return candidate

    return None


def upload_news() -> None:
    """
    data/news/processed/cleaned_news_*.jsonl
    또는 data/news/processed/{date}/cleaned_news.jsonl
    → RDS RAW_ARTICLES
    → Qdrant news
    """
    from storage.rds_handler import upsert_article, resolve_category_id

    cleaned_path = _find_latest_cleaned_news()

    if not cleaned_path:
        logger.warning("[News] cleaned_news 파일 없음")
        return

    logger.info("[News] 업로드 대상: %s", cleaned_path)

    count = 0

    for article in _read_jsonl(cleaned_path):
        category = article.get("category", "")
        category_id = resolve_category_id(category) or 5

        data_id = upsert_article(
            data_title=article.get("title", ""),
            category_id=category_id,
            s3_path=article.get("file_path", "") or article.get("url", ""),
            source_url=article.get("url", ""),
            press=article.get("publisher", ""),
            published_at=article.get("published_at"),
            full_article=article.get("content", ""),
        )

        if not data_id:
            continue

        count += 1

    logger.info("[News] %d개 뉴스 RDS 적재 완료", count)






# ══════════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RDS/Qdrant 벡터 업로더")

    parser.add_argument(
        "--type",
        choices=["policy", "news", "all"],
        default="all",
        help="업로드 대상 데이터 타입",
    )

    args = parser.parse_args()

    if args.type in ("policy", "all"):
        upload_policies()

    if args.type in ("news", "all"):
        upload_news()