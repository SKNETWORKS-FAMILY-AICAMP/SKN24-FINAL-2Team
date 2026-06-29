"""
db/qdrant_client.py
Qdrant 클라이언트 — 카드 임베딩·검색용 공통 모듈

card_generation 등 팀원들이 Qdrant 연결 시 이 파일을 사용.
서버 URL은 .env의 QDRANT_URL 참조.

사용법:
    from db.qdrant_client import get_client

    client = get_client()   # QDRANT_URL 기준으로 서버 연결
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from qdrant_client import QdrantClient

load_dotenv()

_QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")


def get_client(url: str | None = None) -> QdrantClient:
    """
    Qdrant 서버 클라이언트 반환.

    Parameters
    ----------
    url : Qdrant 서버 URL (None이면 .env의 QDRANT_URL 사용)
    """
    target = url or _QDRANT_URL
    return QdrantClient(url=target)
