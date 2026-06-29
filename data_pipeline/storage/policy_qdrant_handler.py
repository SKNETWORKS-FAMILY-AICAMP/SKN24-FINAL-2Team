"""
Qdrant 벡터 DB 핸들러
MOCK_MODE=true 시 실제 Qdrant 연결 없이 로그만 출력
"""
import logging
from typing import List, Optional
from uuid import uuid4

from config import MOCK_MODE, QDRANT_COLLECTION_NAME, QDRANT_HOST, QDRANT_PORT, QDRANT_VECTOR_SIZE

logger = logging.getLogger(__name__)


class QdrantHandler:

    def __init__(self):
        if MOCK_MODE:
            logger.info("[Qdrant] 목업 모드 — 실제 Qdrant 연결 안 함")
            self.client = None
            self.collection = QDRANT_COLLECTION_NAME
            return

        from qdrant_client import QdrantClient
        from qdrant_client.http import models
        from qdrant_client.http.exceptions import UnexpectedResponse

        self.client = QdrantClient(url=f"{QDRANT_HOST}:{QDRANT_PORT}")
        self.collection = QDRANT_COLLECTION_NAME
        self._models = models

        try:
            self.client.get_collection(self.collection)
        except UnexpectedResponse:
            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=QDRANT_VECTOR_SIZE,
                    distance=models.Distance.COSINE,
                ),
            )
            logger.info(f"[Qdrant] 컬렉션 생성: {self.collection}")

    def upsert(self, data_id: int, doc_type: str, chunks: List[str],
               vectors: List[List[float]], metadata: dict) -> None:
        if MOCK_MODE:
            logger.info(f"[Qdrant][MOCK] upsert 스킵: data_id={data_id}, {len(chunks)}개 청크")
            return

        models = self._models
        self.delete_by_data_id(data_id)

        points = []
        for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
            points.append(
                models.PointStruct(
                    id=str(uuid4()),
                    vector=vector,
                    payload={"data_id": data_id, "doc_type": doc_type,
                             "chunk_idx": idx, "text": chunk, **metadata},
                )
            )
        self.client.upsert(collection_name=self.collection, points=points)
        logger.info(f"[Qdrant] 적재 완료: data_id={data_id}, {len(points)}개 청크")

    def delete_by_data_id(self, data_id: int) -> None:
        if MOCK_MODE:
            return
        models = self._models
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(
                        key="data_id",
                        match=models.MatchValue(value=data_id)
                    )]
                )
            ),
        )

    def search(self, query_vector: List[float], top_k: int = 5,
               doc_type: Optional[str] = None, category_id: Optional[int] = None,
               is_youth_related: Optional[bool] = None) -> List[dict]:
        if MOCK_MODE:
            logger.info(f"[Qdrant][MOCK] 검색 스킵: top_k={top_k}")
            return []

        models = self._models
        filters = []
        if doc_type:
            filters.append(models.FieldCondition(key="doc_type", match=models.MatchValue(value=doc_type)))
        if category_id is not None:
            filters.append(models.FieldCondition(key="category_id", match=models.MatchValue(value=category_id)))
        if is_youth_related is not None:
            filters.append(models.FieldCondition(key="is_youth_related", match=models.MatchValue(value=is_youth_related)))

        results = self.client.search(
            collection_name=self.collection,
            query_vector=query_vector,
            limit=top_k,
            query_filter=models.Filter(must=filters) if filters else None,
            with_payload=True,
        )
        return [{"data_id": h.payload.get("data_id"), "doc_type": h.payload.get("doc_type"),
                 "text": h.payload.get("text"), "score": h.score, "payload": h.payload}
                for h in results]