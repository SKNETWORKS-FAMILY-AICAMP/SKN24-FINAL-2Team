"""
pipeline/config.py
PoliTalk 파이프라인 공통 설정
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── OpenAI ─────────────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# ── RDB (MySQL / SQLite fallback) ───────────────────────────────────────────
# MySQL 사용 시: "mysql+pymysql://user:password@host:3306/politalk"
# 로컬 테스트 시: "sqlite:///./politalk_dev.db" (자동 생성)
DB_URL = os.getenv(
    "POLITALK_DB_URL",
    f"sqlite:///{Path(__file__).parent / 'politalk_dev.db'}"
)

DB_HOST     = os.getenv("DB_HOST",     "localhost")
DB_PORT     = int(os.getenv("DB_PORT", "3306"))
DB_USER     = os.getenv("DB_USER",     "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME     = os.getenv("DB_NAME",     "politalk")

# MySQL 연결 문자열 (환경변수 개별 설정 시 사용)
def mysql_url() -> str:
    return f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

LLM_MODEL       = "gpt-4o"        # 카드 생성 / 토론 (고품질)
LLM_MODEL_FAST  = "gpt-4o-mini"   # 필터링 / 분류 (빠르고 저렴)

# ── 임베딩 모델 비교 실험 ────────────────────────────────────────────────────
EMBEDDING_MODELS: dict[str, str] = {
    "small": "text-embedding-3-small",   # 1536-dim, 저비용
    "large": "text-embedding-3-large",   # 3072-dim, 고성능
}

# ── 청킹 전략 비교 실험 ──────────────────────────────────────────────────────
CHUNKING_STRATEGIES = ["fixed", "sentence", "semantic"]

FIXED_CHUNK_SIZE    = 500   # 글자 수 기준 (한국어 약 250~350 토큰)
FIXED_CHUNK_OVERLAP = 50

SENTENCE_MAX_CHARS  = 600   # 문장 단위 청킹 최대 글자 수
SEMANTIC_THRESHOLD  = 0.75  # 코사인 유사도 임계값 (이하에서 분할)
SEMANTIC_MIN_CHARS  = 100   # 최소 청크 길이

# ── 6개 실험 조합 이름 ─────────────────────────────────────────────────────
# fixed_small / fixed_large / sentence_small / sentence_large /
# semantic_small / semantic_large
def collection_name(strategy: str, model_key: str) -> str:
    return f"{strategy}_{model_key}"

EXPERIMENT_COMBOS = [
    (strategy, model_key)
    for strategy in CHUNKING_STRATEGIES
    for model_key in EMBEDDING_MODELS
]

# ── 경로 ────────────────────────────────────────────────────────────────────
BASE_DIR      = Path(__file__).parent
DATA_DIR      = BASE_DIR.parent / "news_crawler" / "output"
CHROMA_DIR    = BASE_DIR / "chroma_db"
OUTPUT_DIR    = BASE_DIR / "output"

CHROMA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ── 카드 생성 제한 (REQ-CARD-004, 005) ─────────────────────────────────────
MAX_NEWS_CARDS_PER_DAY    = 10
MAX_POLICY_CARDS_PER_DAY  = 5
MAX_ARTICLES_PER_CARD     = 10
MIN_ARTICLES_PER_CARD     = 2
MAX_CARD_CONTENT_CHARS    = 5000

# ── 챗봇 제한 (REQ-CHAT-001) ────────────────────────────────────────────────
CHAT_MAX_INPUT_CHARS    = 500
CHAT_MAX_RESPONSE_CHARS = 1500
CHAT_TOP_K              = 5
CHAT_RECOMMEND_COUNT    = 3
CHAT_HISTORY_WINDOW     = 6   # LLM에 넘길 최근 턴 수

# ── 토론 제한 (REQ-DEBATE-001~006) ──────────────────────────────────────────
DEBATE_MAX_CHARS_PER_TURN = 500
DEBATE_TURNS_PER_ROUND    = 3
DEBATE_MAX_EXTRA_ROUNDS   = 2

# ── 검색 평가용 샘플 쿼리 ───────────────────────────────────────────────────
# ── ChromaDB 컬렉션 이름 규칙 ───────────────────────────────────────────────
# 기사 RAG 컬렉션: articles_{strategy}_{model_key}
# 카드 RAG 컬렉션: cards_{model_key}
def article_collection_name(strategy: str, model_key: str) -> str:
    return f"articles_{strategy}_{model_key}"

def card_collection_name(model_key: str) -> str:
    return f"cards_{model_key}"

EVAL_QUERIES = [
    "청년 주거 지원 정책",
    "청년 일자리 취업 현황",
    "복지 법안 발의 현황",
    "대학생 장학금 지원",
    "청년 창업 지원 프로그램",
    "청년 대출 금융 지원",
    "교육비 부담 경감 정책",
    "청년 월세 지원",
]

# ── 토론 RAG 컬렉션 이름 규칙 ────────────────────────────────────────────────
# 정책 RAG: policies_{model_key}
# 법안 RAG: bills_{model_key}
# 토론 시 기사+정책+법안 3개 컬렉션 통합 검색
def policy_collection_name(model_key: str) -> str:
    return f"policies_{model_key}"

def bill_collection_name(model_key: str) -> str:
    return f"bills_{model_key}"

# 1차 전처리 데이터 경로
PREPROCESSED_DIR = BASE_DIR / "1차 전처리"
PREPROCESSED_NEWS_DIR    = PREPROCESSED_DIR / "뉴스"
PREPROCESSED_POLICY_FILE = PREPROCESSED_DIR / "정책" / "youth_policies_document.md"
PREPROCESSED_POLICY_META = PREPROCESSED_DIR / "정책" / "youth_policies_metadata.csv"
PREPROCESSED_BILL_DIR    = PREPROCESSED_DIR / "법안"
PREPROCESSED_BILL_META   = PREPROCESSED_DIR / "법안" / "metadata_youth_only.csv"
