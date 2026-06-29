"""
api/cards.py
카드 생성 엔드포인트

POST /cards/generate/news    — 뉴스 카드 생성 + Qdrant 저장
POST /cards/generate/policy  — 정책 카드 생성 + Qdrant 저장
POST /cards/generate/bill    — 법안 카드 생성 + Qdrant 저장

응답받은 카드 데이터의 RDS 저장은 호출자(스케줄러)가 담당.
"""
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from openai import OpenAI
from qdrant_client import QdrantClient

from agents.card_generation.news.graph import NewsCardGenerator
from agents.card_generation.policy.graph import PolicyCardGenerator
from db.qdrant_client import get_client
from config import OPENAI_API_KEY

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cards", tags=["cards"])

# ── 요청 스키마 ───────────────────────────────────────────────────────────────

class Article(BaseModel):
    title:     str
    content:   str
    url:       str = ""
    publisher: str = ""
    press:     str = ""

class NewsCardRequest(BaseModel):
    articles: List[Article]

class PolicySource(BaseModel):
    id:      str = ""
    name:    str
    content: str
    target:  str = ""
    method:  str = ""
    period:  str = ""
    contact: str = ""
    org:     str = ""
    url:     str = ""

class PolicyCardRequest(BaseModel):
    source:           PolicySource
    related_articles: List[Article] = []
    related_laws: List[dict] = []

class BillSource(BaseModel):
    id:      str = ""
    name:    str
    content: str
    url:     str = ""

class BillCardRequest(BaseModel):
    source:           BillSource
    related_articles: List[Article] = []


# ── 응답 스키마 ───────────────────────────────────────────────────────────────

class CardResponse(BaseModel):
    card_type:    str
    qdrant_id:    Optional[str]
    title:        str
    intro:        str = ""
    debate_topic: str = ""
    tabs:         Dict


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@router.post("/generate/news", response_model=CardResponse)
async def generate_news_card(
    req:           NewsCardRequest,
    openai_client: OpenAI       = Depends(lambda: OpenAI(api_key=OPENAI_API_KEY)),
    qdrant_client: QdrantClient = Depends(get_client),
):
    gen    = NewsCardGenerator(qdrant_client=qdrant_client, openai_client=openai_client)
    result = gen.run(
        articles = [a.model_dump() for a in req.articles],
        save     = True,
    )

    if not result:
        raise HTTPException(status_code=422, detail="카드 생성 실패 (편향 감지 또는 데이터 부족)")

    return CardResponse(
        card_type    = "NEWS",
        qdrant_id    = str(result.get("card_id")),
        title        = result.get("title", ""),
        intro        = result.get("intro", ""),
        debate_topic = result.get("debate_topic", ""),
        tabs         = result.get("tabs", {}),
    )


@router.post("/generate/policy", response_model=CardResponse)
async def generate_policy_card(
    req:           PolicyCardRequest,
    openai_client: OpenAI       = Depends(lambda: OpenAI(api_key=OPENAI_API_KEY)),
    qdrant_client: QdrantClient = Depends(get_client),
):
    gen    = PolicyCardGenerator(qdrant_client=qdrant_client, openai_client=openai_client)
    result = gen.run(
        source           = req.source.model_dump(),
        related_articles = [a.model_dump() for a in req.related_articles],
        related_laws     = req.related_laws,
        card_type        = "POLICY",
        save             = False,
    )

    if not result:
        raise HTTPException(status_code=422, detail="카드 생성 실패 (편향 감지 또는 데이터 부족)")

    return CardResponse(
        card_type    = "POLICY",
        qdrant_id    = str(result.get("card_id")) if result.get("card_id") else None,
        title        = result.get("title", ""),
        intro        = result.get("intro", ""),
        debate_topic = result.get("debate_topic", ""),
        tabs         = result.get("tabs", {}),
    )