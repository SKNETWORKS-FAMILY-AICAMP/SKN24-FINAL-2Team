"""
upload_cards.py
RDS에서 카드 데이터를 읽어 임베딩 엔드포인트 호출 후 Qdrant policity_cards 컬렉션에 저장.

카드 생성 흐름:
  AI서버: 카드 생성만 (save=False)
  → RDS INFO_CARDS 저장 (card_id AUTO INCREMENT)
  → 이 모듈: 임베딩 + Qdrant 저장 (card_id 기반 UUID point_id)
"""

import logging
import os
import sys
import uuid
from pathlib import Path

import requests
from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from config import (
    MOCK_MODE,
    QDRANT_HOST,
    QDRANT_PORT,
    QDRANT_COLLECTION_NAME,
    RUNPOD_ENDPOINT_URL,
)

# 카드 전용 컬렉션 — policity_docs(문서)와 분리
COLLECTION = "policity_cards"

# 임베딩 서버 — RUNPOD_ENDPOINT_URL 사용 (config → .env)
EMBED_URL = RUNPOD_ENDPOINT_URL.rstrip("/")

# Qdrant URL — QDRANT_HOST에 http:// 포함 여부 처리
_qdrant_host = QDRANT_HOST.rstrip("/")
QDRANT_URL = (
    _qdrant_host if _qdrant_host.startswith("http")
    else f"http://{_qdrant_host}"
)
QDRANT_URL = f"{QDRANT_URL}:{QDRANT_PORT}"

logger = logging.getLogger("upload_cards")


# ══════════════════════════════════════════════════════════════════════════
# Qdrant 컬렉션 보장
# ══════════════════════════════════════════════════════════════════════════

def _ensure_collection(client: QdrantClient, dim: int = 768) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION not in existing:
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=qmodels.VectorParams(
                size=dim, distance=qmodels.Distance.COSINE
            ),
        )
        logger.info("[Qdrant] 컬렉션 생성: %s", COLLECTION)
    else:
        logger.info("[Qdrant] 컬렉션 확인: %s", COLLECTION)


# ══════════════════════════════════════════════════════════════════════════
# 임베딩
# ══════════════════════════════════════════════════════════════════════════

def _embed(text: str) -> list[float]:
    """RUNPOD 임베딩 서버 /embed 호출"""
    if not EMBED_URL:
        raise ValueError("RUNPOD_ENDPOINT_URL이 설정되지 않았습니다.")

    res = requests.post(
        f"{EMBED_URL}/embed",
        json={
            "text":          text,
            "chunk_size":    len(text) + 1,  # 청킹 없이 전체 임베딩
            "chunk_overlap": 0,
        },
        timeout=30,
    )
    res.raise_for_status()
    chunks = res.json().get("chunks", [])
    if not chunks:
        raise ValueError("임베딩 결과 없음")
    return chunks[0]["embedding"]


# ══════════════════════════════════════════════════════════════════════════
# 카드 1건 업로드
# ══════════════════════════════════════════════════════════════════════════

def upload_card_to_qdrant(card: dict) -> None:
    """
    RDS INFO_CARDS에서 가져온 카드 1건을 Qdrant policity_cards에 업로드.

    card 스키마:
    {
        "card_id":   123,        # RDS AUTO INCREMENT ID (필수)
        "card_type": "NEWS",     # NEWS | POLICY | BILL
        "title":     "...",
        "tabs": {
            "SUMMARY": {"summary_points": [...]},
            "CORE":    "...",
        }
    }
    """
    if MOCK_MODE:
        logger.info("[MOCK] Qdrant 업로드 생략: card_id=%s", card.get("card_id"))
        return

    card_id   = card["card_id"]
    card_type = card.get("card_type", "NEWS")
    title     = card.get("title", "")
    tabs      = card.get("tabs", {})

    summary        = tabs.get("SUMMARY", {})
    summary_points = summary.get("summary_points", []) if isinstance(summary, dict) else []
    core_text      = tabs.get("CORE", "") if isinstance(tabs.get("CORE"), str) else ""

    # 임베딩 텍스트 구성 (AI 서버 upsert_card와 동일한 방식)
    embed_str = (
        f"[{card_type}] {title} "
        + " ".join(summary_points)
        + f" {core_text[:300]}"
    ).strip()

    if not embed_str:
        raise ValueError(f"card_id={card_id} 임베딩할 내용 없음")

    vector = _embed(embed_str)

    # point_id: RDS card_id 기반 UUID (재실행 시 동일 ID → upsert로 중복 방지)
    point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"card_{card_id}"))

    client = QdrantClient(url=QDRANT_URL)
    _ensure_collection(client)

    client.upsert(
        collection_name=COLLECTION,
        points=[qmodels.PointStruct(
            id      = point_id,
            vector  = vector,
            payload = {
                "card_id":   card_id,
                "card_type": card_type,
                "title":     title,
                "content":   embed_str[:500],
                "doc_type":  "card",
            },
        )],
        wait=True,
    )
    logger.info("[Qdrant] 카드 업로드 완료: card_id=%d / point_id=%s / '%s'",
                card_id, point_id, title[:40])


# ══════════════════════════════════════════════════════════════════════════
# 카드 목록 전체 업로드
# ══════════════════════════════════════════════════════════════════════════

def upload_all_cards(cards: list[dict]) -> dict:
    """RDS에서 가져온 카드 목록 전체 업로드. 반환: {"성공": n, "실패": n}"""
    results = {"성공": 0, "실패": 0}
    for card in cards:
        try:
            upload_card_to_qdrant(card)
            results["성공"] += 1
        except Exception as e:
            logger.error("[Qdrant] card_id=%s 업로드 실패: %s", card.get("card_id"), e,
                         exc_info=True)
            results["실패"] += 1
    logger.info("[Qdrant] 전체 업로드 완료: %s", results)
    return results