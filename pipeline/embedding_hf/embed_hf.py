"""
embedding_hf/embed_hf.py
통합 임베딩 모듈

지원 모델:
  - BAAI/bge-m3         : Dense(1024d) + Sparse  →  하이브리드 검색 (FlagEmbedding)
  - jhgan/ko-sroberta-multitask : Dense(768d) only  →  Dense 검색 (sentence-transformers)

사용 예시:
    from embed_hf import get_embedder

    emb = get_embedder("bge-m3")
    result = emb.encode(["청년 주거 지원", "취업 정책"])
    # result.dense   → List[List[float]]  (N × 1024)
    # result.sparse  → List[SparseVector] (bge-m3만, 나머지는 None)

    emb2 = get_embedder("ko-sroberta")
    result2 = emb2.encode(["청년 주거 지원"])
    # result2.dense  → List[List[float]]  (N × 768)
    # result2.sparse → None
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

# ── Qdrant sparse 포맷 ────────────────────────────────────────────────────────
@dataclass
class SparseVector:
    """Qdrant SparseVector 포맷 (indices/values 쌍)"""
    indices: List[int]
    values:  List[float]


@dataclass
class EmbedOutput:
    """임베딩 결과 컨테이너"""
    dense:  List[List[float]]            # 항상 존재
    sparse: Optional[List[SparseVector]] # bge-m3만, 나머지는 None


# ══════════════════════════════════════════════════════════════════════════════
# BGE-M3 Embedder  (FlagEmbedding 기반 — Dense + Sparse)
# ══════════════════════════════════════════════════════════════════════════════

class BGEM3Embedder:
    """
    BAAI/bge-m3 임베딩
    - Dense : 1024-dim, L2 정규화
    - Sparse: lexical weight 기반 (BM25 대체)

    의존 패키지:
        pip install FlagEmbedding --break-system-packages
        (내부적으로 transformers, torch 사용)
    """

    MODEL_ID = "BAAI/bge-m3"
    DENSE_DIM = 1024

    def __init__(self, use_fp16: bool = True, batch_size: int = 12):
        """
        Parameters
        ----------
        use_fp16 : bool
            GPU 메모리 절약. CPU에서는 자동 무시됨.
        batch_size : int
            GPU 4GB → 12, 8GB → 24, CPU → 8 권장
        """
        self._use_fp16   = use_fp16
        self._batch_size = batch_size
        self._model      = None  # lazy load

    def _load(self):
        if self._model is not None:
            return
        try:
            from FlagEmbedding import BGEM3FlagModel
        except ImportError:
            raise ImportError(
                "FlagEmbedding 패키지가 필요합니다.\n"
                "  pip install FlagEmbedding --break-system-packages"
            )
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"  [bge-m3] 모델 로드 중 (device={device}, fp16={self._use_fp16 and device=='cuda'}) ...")
        self._model = BGEM3FlagModel(
            self.MODEL_ID,
            use_fp16=(self._use_fp16 and device == "cuda"),
        )
        print(f"  [bge-m3] 로드 완료")

    def encode(
        self,
        texts: List[str],
        max_length: int = 8192,
    ) -> EmbedOutput:
        """
        텍스트 목록을 Dense + Sparse 벡터로 인코딩

        Parameters
        ----------
        texts      : 인코딩할 텍스트 리스트
        max_length : 최대 토큰 길이 (bge-m3 최대 8192)

        Returns
        -------
        EmbedOutput
            .dense  : List[List[float]]   (N × 1024)
            .sparse : List[SparseVector]  (N개)
        """
        self._load()
        safe = [t if t.strip() else " " for t in texts]

        output = self._model.encode(
            safe,
            batch_size=self._batch_size,
            max_length=max_length,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )

        dense_vecs = output["dense_vecs"].tolist()          # (N, 1024)
        lex_weights: List[dict] = output["lexical_weights"] # List[{token_id: weight}]

        sparse_vecs = [
            SparseVector(
                indices=[int(k)   for k in lw.keys()],
                values =[float(v) for v in lw.values()],
            )
            for lw in lex_weights
        ]

        return EmbedOutput(dense=dense_vecs, sparse=sparse_vecs)

    def encode_query(self, query: str) -> EmbedOutput:
        """단일 쿼리 인코딩 (편의 메서드)"""
        return self.encode([query])


# ══════════════════════════════════════════════════════════════════════════════
# Ko-SRoBERTa Embedder  (sentence-transformers 기반 — Dense only)
# ══════════════════════════════════════════════════════════════════════════════

class KoSRobertaEmbedder:
    """
    jhgan/ko-sroberta-multitask 임베딩
    - Dense : 768-dim, L2 정규화
    - Sparse: 없음 (Dense 검색만 사용)

    의존 패키지:
        pip install sentence-transformers --break-system-packages
    """

    MODEL_ID  = "jhgan/ko-sroberta-multitask"
    DENSE_DIM = 768

    def __init__(self, batch_size: int = 64):
        self._batch_size = batch_size
        self._model      = None

    def _load(self):
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            import torch
        except ImportError:
            raise ImportError(
                "sentence-transformers 패키지가 필요합니다.\n"
                "  pip install sentence-transformers --break-system-packages"
            )
        device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"  [ko-sroberta] 모델 로드 중 (device={device}) ...")
        self._model = SentenceTransformer(self.MODEL_ID, device=device)
        print(f"  [ko-sroberta] 로드 완료")

    def encode(self, texts: List[str]) -> EmbedOutput:
        """
        텍스트 목록을 Dense 벡터로 인코딩

        Returns
        -------
        EmbedOutput
            .dense  : List[List[float]]  (N × 768)
            .sparse : None
        """
        self._load()
        safe = [t if t.strip() else " " for t in texts]

        vecs = self._model.encode(
            safe,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=len(safe) > 100,
            convert_to_numpy=True,
        )
        return EmbedOutput(dense=vecs.tolist(), sparse=None)

    def encode_query(self, query: str) -> EmbedOutput:
        return self.encode([query])


# ══════════════════════════════════════════════════════════════════════════════
# 팩토리 함수
# ══════════════════════════════════════════════════════════════════════════════

_CACHE: dict = {}

def get_embedder(
    model_key: str,
    **kwargs,
) -> "BGEM3Embedder | KoSRobertaEmbedder":
    """
    model_key로 임베더 인스턴스 반환 (싱글턴 캐시)

    Parameters
    ----------
    model_key : "bge-m3" | "ko-sroberta"
    **kwargs  : 각 Embedder 생성자 인수 (batch_size, use_fp16 등)

    Examples
    --------
    emb = get_embedder("bge-m3")
    emb = get_embedder("ko-sroberta", batch_size=32)
    """
    if model_key not in _CACHE:
        if model_key == "bge-m3":
            _CACHE[model_key] = BGEM3Embedder(**kwargs)
        elif model_key == "ko-sroberta":
            _CACHE[model_key] = KoSRobertaEmbedder(**kwargs)
        else:
            raise ValueError(
                f"알 수 없는 model_key: '{model_key}'\n"
                f"사용 가능: 'bge-m3', 'ko-sroberta'"
            )
    return _CACHE[model_key]


def is_hybrid_model(model_key: str) -> bool:
    """해당 모델이 하이브리드(Dense+Sparse) 검색을 지원하는지"""
    return model_key == "bge-m3"


DENSE_DIM = {
    "bge-m3":      1024,
    "ko-sroberta": 768,
}
