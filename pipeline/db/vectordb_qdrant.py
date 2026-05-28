"""
embedding_hf/vectordb_qdrant.py
Qdrant 기반 Vector Store 관리

컬렉션 구조 (타입별 3개 분리):
  news      — 뉴스 기사 청크
  policies  — 청년 정책 청크
  bills     — 법안 청크

모델별 벡터 구성:
  bge-m3     : "dense"(1024d) + "sparse" → 하이브리드 검색 (RRF fusion)
  ko-sroberta: default vector(768d)       → Dense 검색

로컬 파일 모드 사용 (구글 드라이브 공유 가능):
    client = get_qdrant_client("./qdrant_storage")
    → qdrant_storage/ 폴더째 압축 → 드라이브 공유 → 팀원이 같은 경로로 오픈
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import List, Dict, Optional

from qdrant_client import QdrantClient
from qdrant_client import models as qmodels

from embed_hf import (
    get_embedder, is_hybrid_model, DENSE_DIM,
    SparseVector, EmbedOutput,
)
from chunkers import get_chunker

# ── 컬렉션 이름 ───────────────────────────────────────────────────────────────
COLLECTION_NEWS     = "news"
COLLECTION_POLICIES = "policies"
COLLECTION_BILLS    = "bills"

ALL_COLLECTIONS = [COLLECTION_NEWS, COLLECTION_POLICIES, COLLECTION_BILLS]


# ══════════════════════════════════════════════════════════════════════════════
# 클라이언트
# ══════════════════════════════════════════════════════════════════════════════

def get_qdrant_client(storage_path: str = "./qdrant_storage") -> QdrantClient:
    """
    로컬 파일 기반 Qdrant 클라이언트 반환
    storage_path 폴더가 없으면 자동 생성

    공유 방법:
        qdrant_storage/ 폴더 압축 → 구글 드라이브 업로드
        팀원: 압축 해제 후 같은 경로로 get_qdrant_client() 호출
    """
    Path(storage_path).mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=storage_path)


# ══════════════════════════════════════════════════════════════════════════════
# 컬렉션 초기화
# ══════════════════════════════════════════════════════════════════════════════

def _create_collection(
    client: QdrantClient,
    name: str,
    model_key: str,
    reset: bool = False,
) -> None:
    """
    단일 컬렉션 생성 (모델에 맞는 벡터 설정 자동 적용)

    bge-m3   → named vectors: "dense"(1024) + sparse: "sparse"
    ko-sroberta → default vector: 768-dim
    """
    exists = name in [c.name for c in client.get_collections().collections]

    if exists:
        if reset:
            client.delete_collection(name)
            print(f"  [Qdrant] 컬렉션 '{name}' 삭제 후 재생성")
        else:
            print(f"  [Qdrant] 컬렉션 '{name}' 이미 존재 — 건너뜀")
            return

    dim = DENSE_DIM[model_key]

    if is_hybrid_model(model_key):
        # bge-m3: Dense named vector + Sparse vector
        client.create_collection(
            collection_name=name,
            vectors_config={
                "dense": qmodels.VectorParams(
                    size=dim,
                    distance=qmodels.Distance.COSINE,
                    on_disk=False,
                )
            },
            sparse_vectors_config={
                "sparse": qmodels.SparseVectorParams(
                    index=qmodels.SparseIndexParams(on_disk=False)
                )
            },
        )
    else:
        # ko-sroberta: Dense only (default vector)
        client.create_collection(
            collection_name=name,
            vectors_config=qmodels.VectorParams(
                size=dim,
                distance=qmodels.Distance.COSINE,
            ),
        )

    print(f"  [Qdrant] 컬렉션 '{name}' 생성 완료 (model={model_key}, dim={dim})")


def init_collections(
    client: QdrantClient,
    model_key: str,
    reset: bool = False,
) -> None:
    """뉴스 / 정책 / 법안 3개 컬렉션 일괄 초기화"""
    for name in ALL_COLLECTIONS:
        _create_collection(client, name, model_key, reset=reset)


# ══════════════════════════════════════════════════════════════════════════════
# 청킹 (chunkers.py — chromadb 등 외부 의존성 없음)
# ══════════════════════════════════════════════════════════════════════════════

def _get_chunker(strategy: str = "sentence"):
    """strategy: 'fixed' | 'sentence'"""
    return get_chunker(strategy)


# ══════════════════════════════════════════════════════════════════════════════
# 업서트 (인덱싱)
# ══════════════════════════════════════════════════════════════════════════════

def _make_points(
    docs:      List[str],
    ids:       List[str],
    metas:     List[Dict],
    output:    EmbedOutput,
    model_key: str,
) -> List[qmodels.PointStruct]:
    """문서 배치 → Qdrant PointStruct 리스트 변환"""
    points = []
    for i, (doc, doc_id, meta) in enumerate(zip(docs, ids, metas)):
        if is_hybrid_model(model_key):
            sv = output.sparse[i]
            vector = {
                "dense":  output.dense[i],
                "sparse": qmodels.SparseVector(
                    indices=sv.indices,
                    values =sv.values,
                ),
            }
        else:
            vector = output.dense[i]

        points.append(
            qmodels.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id)),
                vector=vector,
                payload={**meta, "content": doc},
            )
        )
    return points


def upsert_documents(
    client:      QdrantClient,
    collection:  str,
    documents:   List[Dict],
    model_key:   str,
    strategy:    str = "sentence",
    batch_size:  int = 50,
    id_field:    str = "data_id",
) -> int:
    """
    문서 목록을 청킹 → 임베딩 → Qdrant 컬렉션에 upsert

    Parameters
    ----------
    client      : QdrantClient
    collection  : 컬렉션 이름 (COLLECTION_NEWS 등)
    documents   : [{"data_id", "data_title", "content", "source_url", ...}, ...]
    model_key   : "bge-m3" | "ko-sroberta"
    strategy    : 청킹 전략 "fixed" | "sentence"
    batch_size  : 임베딩 배치 크기 (GPU 메모리에 따라 조정)
    id_field    : 문서 고유 ID 필드명

    Returns
    -------
    int : 저장된 청크 수
    """
    embedder = get_embedder(model_key)
    chunker  = _get_chunker(strategy)

    all_docs:  List[str]  = []
    all_ids:   List[str]  = []
    all_metas: List[Dict] = []

    for doc in documents:
        content = doc.get("content", "").strip()
        if not content:
            continue
        data_id = str(doc.get(id_field, ""))
        chunks  = chunker(content)

        for j, chunk in enumerate(chunks):
            chunk_id = f"{collection}_{data_id}__{j}"
            all_docs.append(chunk)
            all_ids.append(chunk_id)
            all_metas.append({
                "data_id":   data_id,
                "title":     doc.get("data_title", doc.get("title", ""))[:200],
                "source_url":doc.get("source_url", doc.get("url", "")),
                "doc_type":  collection,   # "news" | "policies" | "bills"
                "chunk_idx": j,
                # 타입별 추가 메타
                **{k: str(v)[:200] for k, v in doc.items()
                   if k not in ("content", "data_id", "data_title",
                                "title", "source_url", "url")},
            })

    if not all_docs:
        print(f"  [{collection}] 저장할 청크 없음")
        return 0

    total = 0
    from tqdm import tqdm

    for i in tqdm(range(0, len(all_docs), batch_size),
                  desc=f"[{collection}] {model_key} 임베딩 & 저장"):
        batch_docs  = all_docs[i: i + batch_size]
        batch_ids   = all_ids[i: i + batch_size]
        batch_metas = all_metas[i: i + batch_size]

        output = embedder.encode(batch_docs)
        points = _make_points(batch_docs, batch_ids, batch_metas, output, model_key)
        client.upsert(collection_name=collection, points=points, wait=True)
        total += len(points)

    print(f"  ✓ [{collection}] {total}개 청크 저장 완료")
    return total


# ══════════════════════════════════════════════════════════════════════════════
# 검색 (Retrieve)
# ══════════════════════════════════════════════════════════════════════════════

def retrieve(
    query:      str,
    client:     QdrantClient,
    collection: str,
    model_key:  str,
    top_k:      int = 5,
    filters:    Optional[qmodels.Filter] = None,
) -> List[Dict]:
    """
    쿼리 → 임베딩 → Qdrant 검색

    bge-m3   : Dense + Sparse 하이브리드 (RRF fusion)
    ko-sroberta: Dense 검색

    Returns
    -------
    [{"content", "metadata", "score"}, ...]  score 내림차순
    """
    embedder = get_embedder(model_key)
    q_output = embedder.encode_query(query)

    if is_hybrid_model(model_key):
        results = _hybrid_search(client, collection, q_output, top_k, filters)
    else:
        results = _dense_search(client, collection, q_output, top_k, filters)

    return results


def _dense_search(
    client:     QdrantClient,
    collection: str,
    q_output:   EmbedOutput,
    top_k:      int,
    filters:    Optional[qmodels.Filter],
) -> List[Dict]:
    """Dense 검색 (ko-sroberta용) — query_points API (신버전 호환)"""
    try:
        # qdrant-client >= 1.7
        result = client.query_points(
            collection_name=collection,
            query=q_output.dense[0],
            limit=top_k,
            query_filter=filters,
            with_payload=True,
        )
        hits = result.points
    except AttributeError:
        # fallback: 구버전 search API
        hits = client.search(
            collection_name=collection,
            query_vector=q_output.dense[0],
            limit=top_k,
            query_filter=filters,
            with_payload=True,
        )
    return [
        {
            "content":  h.payload.get("content", ""),
            "metadata": {k: v for k, v in h.payload.items() if k != "content"},
            "score":    float(h.score),
        }
        for h in hits
    ]


def _hybrid_search(
    client:     QdrantClient,
    collection: str,
    q_output:   EmbedOutput,
    top_k:      int,
    filters:    Optional[qmodels.Filter],
) -> List[Dict]:
    """
    Dense + Sparse 하이브리드 검색 (bge-m3용)
    Qdrant의 Query API + RRF(Reciprocal Rank Fusion) 사용
    """
    sv = q_output.sparse[0]
    dense_vec  = q_output.dense[0]
    sparse_vec = qmodels.SparseVector(indices=sv.indices, values=sv.values)

    prefetch = [
        qmodels.Prefetch(
            query=dense_vec,
            using="dense",
            limit=top_k * 3,
            filter=filters,
        ),
        qmodels.Prefetch(
            query=sparse_vec,
            using="sparse",
            limit=top_k * 3,
            filter=filters,
        ),
    ]

    hits = client.query_points(
        collection_name=collection,
        prefetch=prefetch,
        query=qmodels.FusionQuery(fusion=qmodels.Fusion.RRF),
        limit=top_k,
        with_payload=True,
    ).points

    return [
        {
            "content":  h.payload.get("content", ""),
            "metadata": {k: v for k, v in h.payload.items() if k != "content"},
            "score":    float(h.score),
        }
        for h in hits
    ]


# ══════════════════════════════════════════════════════════════════════════════
# 통합 검색 (뉴스 + 정책 + 법안 동시)
# ══════════════════════════════════════════════════════════════════════════════

def retrieve_all(
    query:      str,
    client:     QdrantClient,
    model_key:  str,
    top_k:      int = 5,
    news_k:     int = 3,
    policy_k:   int = 3,
    bill_k:     int = 3,
) -> List[Dict]:
    """
    뉴스 / 정책 / 법안 3개 컬렉션 동시 검색 후 score 내림차순 병합

    Returns
    -------
    상위 top_k개, 각 항목에 doc_type 포함
    """
    results: List[Dict] = []

    for col, k in [
        (COLLECTION_NEWS,     news_k),
        (COLLECTION_POLICIES, policy_k),
        (COLLECTION_BILLS,    bill_k),
    ]:
        try:
            hits = retrieve(query, client, col, model_key, top_k=k)
            results.extend(hits)
        except Exception as e:
            print(f"  ⚠ [{col}] 검색 실패: {e}")

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]

# ══════════════════════════════════════════════════════════════════════════════
# 카드 컬렉션 — SQLite 카드 → Qdrant upsert + 유사도 검색
# ══════════════════════════════════════════════════════════════════════════════

COLLECTION_CARDS = "cards"


def _ensure_cards_collection(client: QdrantClient, dim: int = 768) -> None:
    """cards 컬렉션 없으면 생성"""
    existing = [c.name for c in client.get_collections().collections]
    if COLLECTION_CARDS not in existing:
        client.create_collection(
            collection_name=COLLECTION_CARDS,
            vectors_config=qmodels.VectorParams(
                size=dim, distance=qmodels.Distance.COSINE
            ),
        )
        print(f"✅ '{COLLECTION_CARDS}' 컬렉션 생성 완료")


def upsert_card_to_qdrant(
    client:    QdrantClient,
    card_id:   int,
    card_type: str,
    title:     str,
    summary_points: List[str],
    core_text: str = "",
    model_key: str = "ko-sroberta",
) -> None:
    """
    카드 1개를 Qdrant cards 컬렉션에 upsert.
    임베딩 텍스트 = title + summary_points + core 앞부분
    """
    _ensure_cards_collection(client)

    embed_text = f"{title} " + " ".join(summary_points) + f" {core_text[:300]}"
    embed_text  = embed_text.strip()
    if not embed_text:
        return

    embedder = get_embedder(model_key)
    output   = embedder.encode([embed_text])
    vector   = output.dense[0]

    point_id = str(uuid.uuid4())
    client.upsert(
        collection_name=COLLECTION_CARDS,
        points=[qmodels.PointStruct(
            id      = point_id,
            vector  = vector,
            payload = {
                "card_id":   str(card_id),
                "card_type": card_type,
                "title":     title,
                "content":   embed_text[:500],
                "doc_type":  "card",
            },
        )],
        wait=True,
    )


def upload_cards_from_sqlite(
    client:    QdrantClient,
    db_path:   str,
    model_key: str = "ko-sroberta",
) -> int:
    """
    SQLite politalk_dev.db의 카드 전체를 Qdrant cards 컬렉션에 업로드.
    이미 업로드된 카드는 title 기반으로 스킵하지 않고 덮어씀 (멱등).
    Returns: 업로드된 카드 수
    """
    import sqlite3, json as _json

    _ensure_cards_collection(client)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur  = conn.cursor()

    cur.execute("SELECT id, card_type FROM cards ORDER BY id")
    cards = cur.fetchall()
    count = 0

    for card in cards:
        card_id   = card["id"]
        card_type = card["card_type"]

        # SUMMARY 탭
        cur.execute(
            "SELECT content FROM card_tabs WHERE card_id=? AND tab_type='SUMMARY'",
            (card_id,)
        )
        row = cur.fetchone()
        if not row:
            continue
        try:
            summ = _json.loads(row["content"])
        except Exception:
            continue

        title  = summ.get("title", "")
        pts    = summ.get("summary_points", [])

        # CORE 탭
        cur.execute(
            "SELECT content FROM card_tabs WHERE card_id=? AND tab_type='CORE'",
            (card_id,)
        )
        core_row  = cur.fetchone()
        core_text = (core_row["content"] if core_row else "")[:300]

        try:
            upsert_card_to_qdrant(
                client, card_id, card_type, title, pts, core_text, model_key
            )
            print(f"  ✅ card #{card_id} [{card_type}] '{title[:30]}' 업로드")
            count += 1
        except Exception as e:
            print(f"  ❌ card #{card_id} 업로드 실패: {e}")

    conn.close()
    return count


def retrieve_from_cards(
    query:     str,
    client:    QdrantClient,
    model_key: str = "ko-sroberta",
    top_k:     int = 5,
    card_type: str | None = None,
) -> List[Dict]:
    """
    cards 컬렉션에서 유사도 검색.
    Returns: [{"content", "metadata": {"card_id", "card_type", "title"}, "score"}, ...]
    """
    _ensure_cards_collection(client)

    flt = None
    if card_type:
        flt = qmodels.Filter(
            must=[qmodels.FieldCondition(
                key="card_type",
                match=qmodels.MatchValue(value=card_type),
            )]
        )

    return retrieve(query, client, COLLECTION_CARDS, model_key, top_k=top_k, filters=flt)