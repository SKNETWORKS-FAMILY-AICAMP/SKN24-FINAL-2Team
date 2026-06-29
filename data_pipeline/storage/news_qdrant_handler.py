"""
storage/news_qdrant_handler.py
뉴스 기사 Qdrant 적재 핸들러

흐름:
  1. POST /embed (ai_agent EC2) → 청크별 벡터 수신
  2. qdrant_client SDK → EC2 Qdrant 직접 upsert

청킹:
  chunk_size=500, chunk_overlap=50 → /embed 요청 body에 포함
  point id = data_id * 10000 + chunk_index
"""
from __future__ import annotations

import logging
import requests
from typing import Optional

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

logger = logging.getLogger(__name__)

# ── 설정 ──────────────────────────────────────────────────────────────────
COLLECTION_NAME     = "policity_docs"
VECTOR_SIZE         = 768
CHUNK_SIZE          = 500
CHUNK_OVERLAP       = 50
CHUNK_ID_MULTIPLIER = 10_000
EMBED_TIMEOUT       = 120   # 임베딩 API 타임아웃 (초)

# ── 하드코딩 주소 ──────────────────────────────────────────────────────────
QDRANT_URL  = "http://3.36.216.80:6333"
EMBED_URL   = "http://3.36.216.80:8001"


class QdrantHandler:
    def __init__(self, **kwargs):
        # Qdrant 직접 연결
        self.client    = QdrantClient(url=QDRANT_URL)
        self.embed_url = EMBED_URL.rstrip("/")

        self._ensure_collection()
        logger.info(f"[QdrantHandler] Qdrant={QDRANT_URL} / embed={EMBED_URL}")

    # ── 컬렉션 초기화 ─────────────────────────────────────────────────────

    def _ensure_collection(self) -> None:
        existing = [c.name for c in self.client.get_collections().collections]
        if COLLECTION_NAME not in existing:
            self.client.create_collection(
                collection_name = COLLECTION_NAME,
                vectors_config  = qmodels.VectorParams(
                    size     = VECTOR_SIZE,
                    distance = qmodels.Distance.COSINE,
                ),
            )
            logger.info(f"[QdrantHandler] 컬렉션 생성: {COLLECTION_NAME}")
        else:
            logger.info(f"[QdrantHandler] 컬렉션 확인: {COLLECTION_NAME}")

    # ── 임베딩 API 호출 ───────────────────────────────────────────────────

    def _embed(self, text: str) -> list[dict]:
        """
        POST /embed 호출 → 청크별 임베딩 반환.
        반환: [{"chunk_index": int, "text": str, "embedding": [...], "dim": int}, ...]
        """
        resp = requests.post(
            f"{self.embed_url}/embed",
            json={
                "text":          text,
                "chunk_size":    CHUNK_SIZE,
                "chunk_overlap": CHUNK_OVERLAP,
            },
            timeout=EMBED_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("chunks", [])

    # ── 적재 ──────────────────────────────────────────────────────────────

    def upsert_articles(self, articles: list[dict]) -> int:
        """
        전처리된 기사 리스트를 청킹 임베딩 후 Qdrant에 upsert.
        articles: clean JSON 기사 리스트 (data_id 포함 필수)

        임베딩 텍스트: "제목 본문"
        point id:     data_id * 10000 + chunk_index
        payload:      data_id, chunk_index, chunk_total, chunk_text,
                      data_title, category_id, press, published_at, source_url
        """
        if not articles:
            return 0

        total_upserted = 0

        for art in articles:
            data_id = art.get("data_id")
            if data_id is None:
                logger.warning(f"[QdrantHandler] data_id 없음 스킵: {art.get('data_title', '')[:60]}")
                continue

            embed_text = f"{art.get('data_title', '')} {art.get('content', '')}"

            try:
                chunks = self._embed(embed_text)
            except Exception as e:
                logger.warning(f"[QdrantHandler] 임베딩 실패 (data_id={data_id}): {e}")
                continue

            if not chunks:
                logger.warning(f"[QdrantHandler] 청크 없음 (data_id={data_id})")
                continue

            chunk_total = len(chunks)
            points = [
                qmodels.PointStruct(
                    id      = data_id * CHUNK_ID_MULTIPLIER + c["chunk_index"],
                    vector  = c["embedding"],
                    payload = {
                        "data_id":      data_id,
                        "chunk_index":  c["chunk_index"],
                        "chunk_total":  chunk_total,
                        "chunk_text":   c["text"],
                        "data_title":   art.get("data_title", ""),
                        "category_id":  art.get("category_id", 5),
                        "press":        art.get("press", ""),
                        "published_at": art.get("published_at", ""),
                        "source_url":   art.get("source_url", ""),
                    },
                )
                for c in chunks
            ]

            try:
                self.client.upsert(collection_name=COLLECTION_NAME, points=points)
                total_upserted += len(points)
                logger.info(
                    f"[QdrantHandler] upsert data_id={data_id} "
                    f"{len(points)}청크 (누적 {total_upserted})"
                )
            except Exception as e:
                logger.warning(f"[QdrantHandler] upsert 실패 (data_id={data_id}): {e}")

        return total_upserted

    # ── 검색 ──────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        category_id: Optional[int] = None,
        top_k: int = 10,
    ) -> list[dict]:
        """
        쿼리 텍스트로 유사 청크 검색.
        반환: [{"score": float, "data_id": int, "chunk_text": str, ...}, ...]
        """
        try:
            chunks = self._embed(query)
        except Exception as e:
            logger.warning(f"[QdrantHandler] 검색 임베딩 실패: {e}")
            return []

        if not chunks:
            return []

        # 쿼리는 단일 텍스트 → 첫 번째 청크 벡터 사용
        query_vector = chunks[0]["embedding"]

        must = []
        if category_id is not None:
            must.append(qmodels.FieldCondition(
                key   = "category_id",
                match = qmodels.MatchValue(value=category_id),
            ))

        results = self.client.search(
            collection_name = COLLECTION_NAME,
            query_vector    = query_vector,
            query_filter    = qmodels.Filter(must=must) if must else None,
            limit           = top_k,
            with_payload    = True,
        )
        return [{"score": r.score, **r.payload} for r in results]