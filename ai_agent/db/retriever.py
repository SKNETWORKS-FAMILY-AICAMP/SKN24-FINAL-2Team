"""
db/retriever.py
Qdrant 기반 RAG 검색 — 단일 컬렉션 (policity_docs)

컬렉션: policity_docs
  실제 DB 스키마:
    chunk_text   : 청크 본문
    data_title   : 기사 제목
    press        : 언론사
    published_at : 발행일
    source_url   : 원문 URL
    category_id  : 카테고리 (현재 뉴스만 존재)

모델: ko-sroberta (jhgan/ko-sroberta-multitask, 768d dense)

공개 함수:
    retrieve_all(query, client, top_k, ...) → List[Dict]
    retrieve(query, client, top_k) → List[Dict]
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

COLLECTION_NAME  = "policity_docs"
SCORE_THRESHOLD  = 0.35   # 코사인 하한. 운영 로그 보며 0.30~0.45 조정
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer("jhgan/ko-sroberta-multitask")
    return _embedder


def _embed(query: str) -> List[float]:
    return _get_embedder().encode(query).tolist()


def _normalize_point(r) -> Dict:
    """
    Qdrant 포인트를 통일된 dict로 변환.
    실제 DB 필드명(chunk_text, data_title, press)을 내부 표준(content, title, publisher)으로 매핑.
    반환 형태: {"content", "metadata", "score"}
    """
    payload = r.payload
    content = payload.get("chunk_text", payload.get("content", ""))
    metadata = {
        "title":       payload.get("data_title", payload.get("title", "")),
        "publisher":   payload.get("press", payload.get("publisher", "")),
        "published_at": payload.get("published_at", ""),
        "source_url":  payload.get("source_url", payload.get("url", "")),
        "doc_type":    "news",   # 현재 DB는 뉴스만 존재
        "category_id": payload.get("category_id"),
    }
    return {"content": content, "metadata": metadata, "score": r.score}


def _search(
    client: QdrantClient,
    query_vec: List[float],
    top_k: int,
) -> List[Dict]:
    """필터 없이 코사인 유사도 top_k 검색"""
    try:
        if hasattr(client, "query_points"):
            try:
                resp = client.query_points(
                    collection_name=COLLECTION_NAME,
                    query=query_vec,
                    limit=top_k,
                    with_payload=True,
                )
                results = resp.points
            except Exception:
                results = client.search(
                    collection_name=COLLECTION_NAME,
                    query_vector=query_vec,
                    limit=top_k,
                    with_payload=True,
                )
        else:
            results = client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vec,
                limit=top_k,
                with_payload=True,
            )
    except Exception as e:
        logger.warning(f"검색 실패: {e}")
        return []

    return [_normalize_point(r) for r in results]


def retrieve(
    query: str,
    client: QdrantClient,
    doc_type: Optional[str] = None,   # 하위 호환용 — 현재 미사용
    top_k: int = 5,
) -> List[Dict]:
    """단일 컬렉션 검색."""
    try:
        vec = _embed(query)
        return _search(client, vec, top_k)
    except Exception as e:
        logger.warning(f"retrieve 실패: {e}")
        return []


def retrieve_all(
    query: str,
    client: QdrantClient,
    top_k: int = 5,
    news_k: int = 3,
    policy_k: int = 3,
    bill_k: int = 3,
    **kwargs,  # 호환성 인자 무시
) -> List[Dict]:
    """
    score 내림차순 검색 후 threshold 필터링.
    현재 DB는 뉴스만 있으므로 news_k + policy_k + bill_k 합산을 후보로 검색.

    Parameters
    ----------
    query    : 검색 쿼리
    client   : QdrantClient
    top_k    : 최종 반환 개수
    news_k / policy_k / bill_k : 하위 호환용 (합산해서 후보 수로 사용)
    """
    try:
        vec = _embed(query)
    except Exception as e:
        logger.warning(f"임베딩 실패: {e}")
        return []

    candidate_k = news_k + policy_k + bill_k

    try:
        hits = _search(client, vec, candidate_k)
        # Fix A: 점수 하한 미만 제거 — 주제 무관 저점수 문서 차단
        hits = [h for h in hits if h.get("score", 0.0) >= SCORE_THRESHOLD - 1e-9]
        logger.info(
            f"retrieve_all top_score={hits[0]['score']:.3f} kept={len(hits)}"
            if hits else "retrieve_all kept=0 (all below threshold)"
        )
    except Exception as e:
        logger.warning(f"retrieve_all 실패: {e}")
        return []

    hits.sort(key=lambda x: x["score"], reverse=True)
    return hits[:top_k]
