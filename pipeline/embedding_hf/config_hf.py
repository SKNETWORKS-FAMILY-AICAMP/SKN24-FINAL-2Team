"""
embedding_hf/config_hf.py
HuggingFace 임베딩 실험용 설정
- 기존 config.py(OpenAI 모델)를 건드리지 않고 확장
- BAAI/bge-m3, jhgan/ko-sroberta-multitask 추가
"""
import sys
from pathlib import Path

# 기존 pipeline/ 경로를 import 경로에 추가 (공통 설정 재사용)
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    OPENAI_API_KEY,
    DB_URL,
    LLM_MODEL, LLM_MODEL_FAST,
    FIXED_CHUNK_SIZE, FIXED_CHUNK_OVERLAP,
    SENTENCE_MAX_CHARS, SEMANTIC_THRESHOLD, SEMANTIC_MIN_CHARS,
    CHUNKING_STRATEGIES,
    BASE_DIR, DATA_DIR, CHROMA_DIR, OUTPUT_DIR,
    PREPROCESSED_DIR, PREPROCESSED_NEWS_DIR,
    PREPROCESSED_POLICY_FILE, PREPROCESSED_POLICY_META,
    PREPROCESSED_BILL_DIR, PREPROCESSED_BILL_META,
    EVAL_QUERIES,
    MAX_NEWS_CARDS_PER_DAY, MAX_POLICY_CARDS_PER_DAY,
    CHAT_TOP_K,
)

# ── 임베딩 모델 정의 (확장) ──────────────────────────────────────────────────
#   model_key → 실제 모델 식별자
#   "openai:" prefix  → OpenAI API 사용
#   "hf:" prefix      → HuggingFace sentence-transformers 사용

EMBEDDING_MODELS_ALL: dict[str, str] = {
    # 기존 OpenAI 모델
    "small":       "openai:text-embedding-3-small",   # 1536-dim
    "large":       "openai:text-embedding-3-large",   # 3072-dim
    # 새 HuggingFace 모델
    "bge-m3":      "hf:BAAI/bge-m3",                  # 1024-dim, 다국어
    "ko-sroberta": "hf:jhgan/ko-sroberta-multitask",  # 768-dim, 한국어 특화
}

# 실험에 포함할 모델 키 목록 (필요에 따라 조정)
EXPERIMENT_MODEL_KEYS = ["small", "large", "bge-m3", "ko-sroberta"]

# 실험 조합: 청킹 전략 × 임베딩 모델
EXPERIMENT_COMBOS_ALL = [
    (strategy, model_key)
    for strategy in CHUNKING_STRATEGIES          # fixed / sentence / semantic
    for model_key in EXPERIMENT_MODEL_KEYS
]

# ── ChromaDB 저장 경로 (HF 실험 전용 분리) ────────────────────────────────────
HF_CHROMA_DIR = Path(__file__).parent / "chroma_db_hf"
HF_CHROMA_DIR.mkdir(exist_ok=True)

# ── 컬렉션 이름 규칙 ──────────────────────────────────────────────────────────
def hf_article_collection(strategy: str, model_key: str) -> str:
    return f"articles_{strategy}_{model_key}"

def hf_policy_collection(model_key: str) -> str:
    return f"policies_{model_key}"

def hf_bill_collection(model_key: str) -> str:
    return f"bills_{model_key}"

def hf_card_collection(model_key: str) -> str:
    return f"cards_{model_key}"

# ── 모델 백엔드 판별 헬퍼 ─────────────────────────────────────────────────────
def is_openai_model(model_key: str) -> bool:
    """model_key가 OpenAI 모델인지 여부"""
    spec = EMBEDDING_MODELS_ALL.get(model_key, "")
    return spec.startswith("openai:")

def is_hf_model(model_key: str) -> bool:
    """model_key가 HuggingFace 모델인지 여부"""
    spec = EMBEDDING_MODELS_ALL.get(model_key, "")
    return spec.startswith("hf:")

def get_model_id(model_key: str) -> str:
    """model_key → 실제 모델 ID 반환 (prefix 제거)"""
    spec = EMBEDDING_MODELS_ALL.get(model_key, "")
    if ":" in spec:
        return spec.split(":", 1)[1]
    return spec
