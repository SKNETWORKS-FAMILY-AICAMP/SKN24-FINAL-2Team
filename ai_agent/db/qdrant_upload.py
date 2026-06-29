"""
db/qdrant_upload.py
Qdrant 데이터 적재 전용 모듈
"""
import hashlib
import uuid
from typing import Dict
from qdrant_client import QdrantClient
from qdrant_client import models as qmodels
from db.retriever import _embed

COLLECTION_CARDS = "policity_cards"   # 카드 전용 컬렉션

def _ensure_cards_collection(client: QdrantClient, dim: int = 768) -> None:
    """cards 컬렉션이 없으면 생성"""
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_CARDS not in existing:
        client.create_collection(
            collection_name=COLLECTION_CARDS,
            vectors_config=qmodels.VectorParams(
                size=dim, distance=qmodels.Distance.COSINE
            ),
        )

def upsert_card(client: QdrantClient, card_data: Dict, card_type: str = "POLICY") -> int:
    """
    생성된 카드 데이터를 임베딩하여 Qdrant 'policity_cards' 컬렉션에 업로드합니다.
    """
    _ensure_cards_collection(client)

    summary_tab    = card_data.get("SUMMARY", {})
    title          = summary_tab.get("title", "")
    summary_points = summary_tab.get("summary_points", [])
    core_text      = card_data.get("CORE", "")

    embed_text = f"[{card_type}] {title} " + " ".join(summary_points) + f" {core_text[:300]}"
    embed_text = embed_text.strip()

    if not embed_text:
        raise ValueError("임베딩할 카드 내용이 비어있습니다.")

    vector         = _embed(embed_text)
    point_id       = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"card_{title}"))
    hash_object    = hashlib.md5(title.encode("utf-8"))
    card_numeric_id = int(hash_object.hexdigest(), 16) % (10**8)

    client.upsert(
        collection_name=COLLECTION_CARDS,
        points=[qmodels.PointStruct(
            id      = point_id,
            vector  = vector,
            payload = {
                "card_id":   card_numeric_id,
                "card_type": card_type,
                "title":     title,
                "content":   embed_text[:500],
                "doc_type":  "card",
            }
        )],
        wait=True,
    )
    return card_numeric_id