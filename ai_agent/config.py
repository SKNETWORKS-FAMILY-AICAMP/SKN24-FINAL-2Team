"""
config.py
Policity AI Agent 공통 설정

환경변수는 .env에서 로드 (python-dotenv)
"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── OpenAI ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

LLM_MODEL      = "gpt-4o"       # 카드 생성 / 토론 (고품질)
LLM_MODEL_FAST = "gpt-4o-mini"  # 필터링 / 분류 (빠르고 저렴)

# ── 임베딩 모델 ───────────────────────────────────────────────────────────────
# [현재] 로컬 SentenceTransformer (jhgan/ko-sroberta-multitask)
# [추후] RunPod 서버로 전환 시 retriever.py의 _embed() 함수만 교체하면 됨
#        .env에 RUNPOD_EMBED_URL, RUNPOD_API_KEY 추가 필요
EMBEDDING_MODEL = "jhgan/ko-sroberta-multitask"  # 768d dense
EMBEDDING_DIM   = 768

# chatbot.py 하위 호환용 (EMBEDDING_MODELS["ko-sroberta"] 키로 접근)
EMBEDDING_MODELS: dict[str, str] = {
    "ko-sroberta": EMBEDDING_MODEL,
}

# ── Qdrant ───────────────────────────────────────────────────────────────────
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")

# ── RDB (MySQL / SQLite fallback) ────────────────────────────────────────────
DB_HOST     = os.getenv("DB_HOST", "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_USER     = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME     = os.getenv("DB_NAME")

def mysql_url() -> str:
    return (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"
    )

# ── LangGraph checkpoint ─────────────────────────────────────────────────────
CHECKPOINT_PATH = os.getenv("CHECKPOINT_PATH", "./checkpoints/debate.db")

# ── 경로 ─────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ── 카드 생성 제한 ────────────────────────────────────────────────────────────
MAX_NEWS_CARDS_PER_DAY   = 10
MAX_POLICY_CARDS_PER_DAY = 5
MAX_ARTICLES_PER_CARD    = 25
MIN_ARTICLES_PER_CARD    = 2
MAX_CARD_CONTENT_CHARS   = 5000

# ── 챗봇 제한 ────────────────────────────────────────────────────────────────
CHAT_MAX_INPUT_CHARS    = 500
CHAT_MAX_RESPONSE_CHARS = 1500
CHAT_TOP_K              = 5
CHAT_RECOMMEND_COUNT    = 3
CHAT_HISTORY_WINDOW     = 6  # LLM에 넘길 최근 턴 수

# ── 토론 제한 ─────────────────────────────────────────────────────────────────
DEBATE_MAX_CHARS_PER_TURN = 500
DEBATE_TURNS_PER_ROUND    = 3
DEBATE_MAX_EXTRA_ROUNDS   = 2