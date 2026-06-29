"""
Qdrant 벡터 업로더
전처리 완료된 데이터를 읽어 임베딩 생성 후 Qdrant에 적재

실행:
  python qdrant_uploader.py --type policy
  python qdrant_uploader.py --type law
  python qdrant_uploader.py --type news
  python qdrant_uploader.py --type all
"""

import sys
import json
import logging
import argparse
import requests
from pathlib import Path
from typing import List, Optional, Tuple

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))

from config import (
    LOG_DIR,
    POLICY_PROCESSED_DIR,
    NEWS_PROCESSED_DIR,
    LAWS_PROCESSED_DIR,
    MOCK_MODE,
    # EMBEDDING_MODEL,        # 로컬 임베딩 사용 시 활성화
    # EMBEDDING_BATCH_SIZE,   # 로컬 임베딩 사용 시 활성화
    RUNPOD_ENDPOINT_URL,
)

# ── 청킹 설정 ──────────────────────────────────────────────────────────────
CHUNK_SIZE    = 500   # 청크당 최대 글자 수
CHUNK_OVERLAP = 50    # 청크 간 겹치는 글자 수

LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "qdrant_uploader.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("qdrant_uploader")


# ══════════════════════════════════════════════════════════════════════════
# 임베딩
# ══════════════════════════════════════════════════════════════════════════

# ── [기존] 로컬 모델 임베딩 (주석 처리) ────────────────────────────────────
# _embedding_model = None
#
# def get_embedding_model():
#     global _embedding_model
#     if _embedding_model is None:
#         from sentence_transformers import SentenceTransformer
#         logger.info("[Embedding] %s 모델 로딩 중...", EMBEDDING_MODEL)
#         _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
#         logger.info("[Embedding] 모델 로딩 완료")
#     return _embedding_model
#
# def embed_texts(texts: List[str]) -> List[List[float]]:
#     """텍스트 리스트 → 벡터 리스트 (로컬 모델)"""
#     if MOCK_MODE:
#         return [[0.0] * 768 for _ in texts]
#     model = get_embedding_model()
#     vectors = model.encode(texts, batch_size=EMBEDDING_BATCH_SIZE, show_progress_bar=False)
#     return [v.tolist() for v in vectors]
# ── [기존] 로컬 모델 임베딩 끝 ─────────────────────────────────────────────

# ── [기존] 로컬 청킹 함수 (주석 처리) ──────────────────────────────────────
# def chunk_text(text: str) -> List[str]:
#     """텍스트를 CHUNK_SIZE 글자 단위로 자르되 CHUNK_OVERLAP만큼 겹치게 청킹"""
#     if not text:
#         return []
#     chunks = []
#     start = 0
#     while start < len(text):
#         end = start + CHUNK_SIZE
#         chunks.append(text[start:end])
#         start += CHUNK_SIZE - CHUNK_OVERLAP
#     return chunks
# ── [기존] 로컬 청킹 함수 끝 ───────────────────────────────────────────────


# ── [신규] FastAPI 임베딩 서버 호출 ────────────────────────────────────────
def embed_texts(text: str) -> Tuple[List[str], List[List[float]]]:
    """
    텍스트 통째로 → FastAPI 임베딩 서버 호출
    서버가 청킹 + 임베딩 모두 처리
    반환: (청크 텍스트 리스트, 벡터 리스트)
    """
    if MOCK_MODE:
        return ["mock chunk"], [[0.0] * 768]

    response = requests.post(
        f"{RUNPOD_ENDPOINT_URL}/embed",
        json={
            "text":          text,
            "chunk_size":    CHUNK_SIZE,
            "chunk_overlap": CHUNK_OVERLAP,  # overlap → chunk_overlap
        },
        timeout=60,
    )
    response.raise_for_status()

    chunks_data = response.json()["chunks"]
    chunks  = [c["text"]      for c in chunks_data]
    vectors = [c["embedding"] for c in chunks_data]
    return chunks, vectors
# ── [신규] FastAPI 임베딩 서버 호출 끝 ─────────────────────────────────────


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


def _get_data_id_by_url(source_url: str, title: str = "") -> Optional[int]:
    """RDS에서 source_url 기준으로 data_id 조회, 없으면 title로 조회"""
    from storage.policy_rds_handler import db_cursor
    with db_cursor() as cursor:
        if source_url:
            cursor.execute(
                "SELECT data_id FROM RAW_DATAS WHERE source_url = %s LIMIT 1",
                (source_url,)
            )
            row = cursor.fetchone()
            if row:
                return row["data_id"]
        if title:
            cursor.execute(
                "SELECT data_id FROM RAW_DATAS WHERE data_title = %s LIMIT 1",
                (title,)
            )
            row = cursor.fetchone()
            return row["data_id"] if row else None
        return None


# ══════════════════════════════════════════════════════════════════════════
# 정책 업로드
# ══════════════════════════════════════════════════════════════════════════

def upload_policies() -> None:
    """
    gov24_top5_clean_*.json → 임베딩 서버 → Qdrant
    """
    from storage.policy_qdrant_handler import QdrantHandler

    latest = _latest_file(POLICY_PROCESSED_DIR, "gov24_top5_clean_*.json")
    if not latest:
        logger.warning("[Policy] processed JSON 없음")
        return

    logger.info("[Policy] 업로드 대상: %s", latest)
    qdrant = QdrantHandler()
    data = _read_json(latest)
    count = 0

    for policy in data:
        source_url = policy.get("source_url", "")
        content    = policy.get("content", "")

        if not content:
            continue

        data_id = _get_data_id_by_url(source_url, title=policy.get("title", ""))
        if not data_id:
            logger.warning("[Policy] data_id 없음: %s", policy.get("title", "")[:60])
            continue

        chunks, vectors = embed_texts(content)  # 통째로 보내고 청크+벡터 받아옴
        qdrant.upsert(
            data_id  = data_id,
            doc_type = "policy",
            chunks   = chunks,
            vectors  = vectors,
            metadata = {
                "category_id": policy.get("category_id", 5),
                "title":       policy.get("title", ""),
                "source_url":  source_url,
            },
        )
        count += 1

    logger.info("[Policy] %d개 정책 Qdrant 적재 완료", count)


# ══════════════════════════════════════════════════════════════════════════
# 법령 업로드
# ══════════════════════════════════════════════════════════════════════════

def upload_laws() -> None:
    """
    law_grouped_clean_*.json → 조문내용 임베딩 서버 → Qdrant
    법령은 정책의 data_id에 연결
    """
    from storage.policy_qdrant_handler import QdrantHandler
    from storage.policy_rds_handler import db_cursor

    law_files = sorted(LAWS_PROCESSED_DIR.glob("law_grouped_clean_*.json"), reverse=True)
    law_path  = law_files[0] if law_files else None

    if not law_path or not law_path.exists():
        logger.warning("[Law] law_grouped_clean.json 없음")
        return

    logger.info("[Law] 업로드 대상: %s", law_path)
    qdrant = QdrantHandler()
    laws   = _read_json(law_path)
    count  = 0

    for law in laws:
        법령명  = law.get("법령명", "")
        조문들  = law.get("조문", [])
        관련정책 = law.get("관련정책", [])

        if not 조문들:
            continue

        # 관련 정책의 data_id 수집 (서비스명 기준 조회)
        data_ids = []
        for policy in 관련정책:
            service_name = policy.get("서비스명", "")
            if not service_name:
                continue
            with db_cursor() as cursor:
                cursor.execute(
                    "SELECT data_id FROM RAW_DATAS WHERE data_title = %s LIMIT 1",
                    (service_name,)
                )
                row = cursor.fetchone()
                if row:
                    data_ids.append(row["data_id"])

        if not data_ids:
            continue

        # 조문 텍스트 하나로 합치기
        full_text = "\n".join([
            f"{법령명} {a.get('조문번호', '')}조 {a.get('조문제목', '')}\n{a.get('조문내용', '')}"
            for a in 조문들
            if a.get("조문내용")
        ])

        if not full_text:
            continue

        chunks, vectors = embed_texts(full_text)  # 통째로 보내고 청크+벡터 받아옴

        # 관련 정책 data_id마다 업로드
        for data_id in data_ids:
            qdrant.upsert(
                data_id  = data_id,
                doc_type = "law",
                chunks   = chunks,
                vectors  = vectors,
                metadata = {"법령명": 법령명},
            )
            count += 1

    logger.info("[Law] %d개 법령 Qdrant 적재 완료", count)


# ══════════════════════════════════════════════════════════════════════════
# 기사 업로드
# ══════════════════════════════════════════════════════════════════════════

def upload_news() -> None:
    """
    cleaned_news.jsonl → 임베딩 서버 → Qdrant
    """
    from storage.policy_qdrant_handler import QdrantHandler

    date_dirs = sorted([p for p in NEWS_PROCESSED_DIR.iterdir() if p.is_dir()], reverse=True)
    cleaned_path = None
    for d in date_dirs:
        candidate = d / "cleaned_news.jsonl"
        if candidate.exists():
            cleaned_path = candidate
            break

    if not cleaned_path:
        cleaned_path = _latest_file(NEWS_PROCESSED_DIR, "cleaned_news_*.jsonl")

    if not cleaned_path:
        logger.warning("[News] cleaned_news 파일 없음")
        return

    logger.info("[News] 업로드 대상: %s", cleaned_path)
    qdrant = QdrantHandler()
    count  = 0

    for article in _read_jsonl(cleaned_path):
        source_url = article.get("url", "")
        content    = article.get("content", "")

        if not content:
            continue

        data_id = _get_data_id_by_url(source_url)
        if not data_id:
            logger.warning("[News] data_id 없음: %s", source_url[:60])
            continue

        chunks, vectors = embed_texts(content)  # 통째로 보내고 청크+벡터 받아옴
        qdrant.upsert(
            data_id  = data_id,
            doc_type = "news",
            chunks   = chunks,
            vectors  = vectors,
            metadata = {
                "category":  article.get("category", ""),
                "publisher": article.get("publisher", ""),
                "title":     article.get("title", ""),
                "source_url": source_url,
            },
        )
        count += 1

    logger.info("[News] %d개 기사 Qdrant 적재 완료", count)


# ══════════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Qdrant 벡터 업로더")
    parser.add_argument(
        "--type",
        choices=["policy", "law", "news", "all"],
        default="all",
        help="업로드 대상 데이터 타입",
    )
    args = parser.parse_args()

    if args.type in ("policy", "all"):
        upload_policies()

    if args.type in ("law", "all"):
        upload_laws()

    if args.type in ("news", "all"):
        upload_news()