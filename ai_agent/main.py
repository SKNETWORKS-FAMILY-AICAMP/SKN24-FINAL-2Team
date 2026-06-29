"""
ai_agent/main.py
FastAPI 서버 진입점

lifespan:
  - ko-sroberta 임베딩 모델 로드 (첫 요청 지연 방지)
  - Qdrant 클라이언트 초기화
  - OpenAI 클라이언트 초기화
  - LangGraph debate 그래프 빌드 (SQLite checkpoint)

실행:
  uvicorn main:app --host 0.0.0.0 --port 8001
"""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from openai import OpenAI
from qdrant_client import QdrantClient

from agents.debate import build_debate_graph
from db.retriever import _get_embedder

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# 전역 상태 (lifespan에서 초기화 → 라우터가 참조)
_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    # 1. ko-sroberta 임베딩 모델 로드 (~400MB, 첫 요청 지연 방지)
    logger.info("ko-sroberta 임베딩 모델 로드 중...")
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _get_embedder)
    logger.info("임베딩 모델 로드 완료")

    # 2. Qdrant 클라이언트
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    try:
        qdrant_client = QdrantClient(url=qdrant_url)
        logger.info(f"Qdrant 연결: {qdrant_url}")
    except Exception as e:
        logger.warning(f"Qdrant 연결 실패 (RAG 비활성화): {e}")
        qdrant_client = None

    # 3. OpenAI 클라이언트
    openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    # 4. LangGraph debate 그래프 (AsyncSqliteSaver)
    # WAL 모드 비활성화: 강제 종료 후 DB 잠금 없이 재시작 가능
    checkpoint_path = os.getenv("CHECKPOINT_PATH", "./checkpoints/debate.db")
    Path(checkpoint_path).parent.mkdir(parents=True, exist_ok=True)
    aio_conn = await aiosqlite.connect(checkpoint_path)
    await aio_conn.execute("PRAGMA journal_mode=DELETE")
    await aio_conn.commit()
    async_saver = AsyncSqliteSaver(aio_conn)

    graph, _ = build_debate_graph(
        vector_client=qdrant_client,
        openai_client=openai_client,
        checkpointer=async_saver,
    )
    logger.info(f"LangGraph 그래프 빌드 완료 (checkpoint: {checkpoint_path})")

    _state["graph"] = graph
    _state["qdrant_client"] = qdrant_client
    _state["openai_client"] = openai_client

    yield

    await aio_conn.close()
    logger.info("서버 종료")


app = FastAPI(
    title="Policity AI Agent",
    description="대한민국 청년을 위한 정책·뉴스 해설 AI 서비스",
    version="1.0.0",
    lifespan=lifespan,
)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    try:
        body = await request.json()
    except Exception:
        body = "<could not parse body>"
    logger.error(
        "422 Validation error on %s %s\nBody: %s\nErrors: %s",
        request.method,
        request.url.path,
        body,
        exc.errors(),
    )
    return JSONResponse(status_code=422, content={"detail": exc.errors(), "body": str(body)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
async def health():
    return {"status": "ok"}


# 라우터 등록 — 순환 임포트 방지를 위해 app 정의 이후에 임포트
from api import debate_router, chatbot_router, cards_router, embed_router  # noqa: E402

app.include_router(debate_router, prefix="/debate")
app.include_router(chatbot_router)
app.include_router(cards_router)
app.include_router(embed_router)
