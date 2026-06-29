"""
api/embed.py
텍스트 임베딩 엔드포인트 — jhgan/ko-sroberta-multitask (768d)

외부 서버에서 POST /embed 로 호출해 벡터를 받아 사용합니다.

청킹 옵션 (선택):
  chunk_size    — 청크당 최대 글자 수 (기본값: None → 청킹 없이 전체 텍스트 임베딩)
  chunk_overlap — 인접 청크 간 겹치는 글자 수 (기본값: 0)

청킹을 사용하면 /embed 가 청크별 임베딩 목록을 반환합니다.
"""
import asyncio
import logging
from typing import List, Optional  # Optional kept for EmbedResponse fields

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from db.retriever import _embed

logger = logging.getLogger(__name__)

router = APIRouter(tags=["embed"])


def _chunk_text(text: str, chunk_size: int, overlap: int) -> List[str]:
    """
    텍스트를 chunk_size 글자 단위로 나누되,
    인접 청크가 overlap 글자만큼 겹치도록 합니다.
    """
    chunks = []
    step = chunk_size - overlap          # 한 번에 전진하는 글자 수
    if step <= 0:
        raise ValueError("chunk_size must be greater than chunk_overlap")
    start = 0
    while start < len(text):
        chunks.append(text[start : start + chunk_size])
        start += step
    return chunks


class EmbedRequest(BaseModel):
    text: str = Field(..., min_length=1, description="임베딩할 텍스트")
    chunk_size: int = Field(
        500, gt=0, description="청크당 최대 글자 수 (기본값 500)"
    )
    chunk_overlap: int = Field(
        50, ge=0, description="인접 청크 간 겹치는 글자 수 (기본값 50)"
    )

    @model_validator(mode="after")
    def overlap_must_be_smaller_than_chunk(self) -> "EmbedRequest":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        return self


class ChunkEmbedding(BaseModel):
    chunk_index: int
    text: str
    embedding: list[float]
    dim: int


class EmbedResponse(BaseModel):
    # 청킹 없이 호출 시 단일 임베딩 (하위 호환)
    embedding: Optional[list[float]] = None
    dim: Optional[int] = None
    # 청킹 사용 시 청크별 임베딩 목록
    chunks: Optional[List[ChunkEmbedding]] = None


@router.post("/embed", response_model=EmbedResponse)
async def embed_text(req: EmbedRequest):
    """
    텍스트를 ko-sroberta-multitask 모델로 임베딩하여 반환합니다.

    - chunk_size 미지정: 전체 텍스트를 하나의 벡터로 반환 (기존 동작)
    - chunk_size 지정:   텍스트를 청킹 후 청크별 벡터를 chunks 배열로 반환
    """
    try:
        loop = asyncio.get_event_loop()

        texts = _chunk_text(req.text, req.chunk_size, req.chunk_overlap)
        chunk_results: List[ChunkEmbedding] = []
        for i, chunk in enumerate(texts):
            vector = await loop.run_in_executor(None, _embed, chunk)
            chunk_results.append(
                ChunkEmbedding(
                    chunk_index=i,
                    text=chunk,
                    embedding=vector,
                    dim=len(vector),
                )
            )
        return EmbedResponse(chunks=chunk_results)

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.error(f"임베딩 실패: {e}")
        raise HTTPException(status_code=500, detail=str(e))
