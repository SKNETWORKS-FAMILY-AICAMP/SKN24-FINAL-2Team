"""
전체 수집기 설정값
환경변수는 .env 파일에서 로드
"""
import os
from pathlib import Path
from dotenv import load_dotenv

_ENV_PATH = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH)

# ── API 키 ─────────────────────────────────────────────────────────────────
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
ASSEMBLY_API_KEY    = os.getenv("ASSEMBLY_API_KEY", "")
GOV24_API_KEY       = os.getenv("GOV24_API_KEY", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
LAW_API_OC          = os.getenv("LAW_API_OC", "")

# ── RDS MySQL ──────────────────────────────────────────────────────────────
RDS_HOST     = os.getenv("RDS_HOST") or os.getenv("DB_HOST", "")
RDS_PORT     = int(os.getenv("RDS_PORT") or os.getenv("DB_PORT", "3306"))
RDS_USER     = os.getenv("RDS_USER") or os.getenv("DB_USER", "")
RDS_PASSWORD = os.getenv("RDS_PASSWORD") or os.getenv("DB_PASSWORD", "")
RDS_DATABASE = os.getenv("RDS_DATABASE") or os.getenv("DB_NAME", "")

# ── S3 ─────────────────────────────────────────────────────────────────────
S3_BUCKET_NAME        = os.getenv("S3_BUCKET_NAME", "")
AWS_ACCESS_KEY_ID     = os.getenv("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "")
AWS_REGION            = os.getenv("AWS_REGION", "ap-northeast-2")

S3_PREFIX_BILL   = "bills"
S3_PREFIX_NEWS   = "news"
S3_PREFIX_POLICY = "policies"
S3_PREFIX_LAW    = "laws"

# ── Qdrant ─────────────────────────────────────────────────────────────────
QDRANT_HOST            = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT            = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION_NAME = os.getenv("QDRANT_COLLECTION_NAME", "policity_docs")
QDRANT_VECTOR_SIZE     = 768  # ko-sroberta-multitask

# ── 임베딩 모델 (로컬 임베딩 사용 시 활성화) ───────────────────────────────
# EMBEDDING_MODEL      = "jhgan/ko-sroberta-multitask"
# EMBEDDING_BATCH_SIZE = 32

# ── EC2 임베딩 서버 ─────────────────────────────────────────────────────────
RUNPOD_ENDPOINT_URL = os.getenv("RUNPOD_ENDPOINT_URL", "")  # http://3.36.216.80:8001
RUNPOD_API_KEY      = os.getenv("RUNPOD_API_KEY", "")

# ── 수집 공통 설정 ─────────────────────────────────────────────────────────
REQUEST_TIMEOUT  = 30
MAX_RETRY        = 3
RETRY_DELAY      = 2
CRAWL_DELAY_MIN  = 0.5
CRAWL_DELAY_MAX  = 1.5

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── 목업 모드 ──────────────────────────────────────────────────────────────
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"

# ── 저장 경로 ──────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
LOG_DIR  = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"

# 정책 (data/policy/policies/)
POLICY_RAW_DIR       = DATA_DIR / "policy" / "policies" / "raw"
POLICY_PROCESSED_DIR = DATA_DIR / "policy" / "policies" / "processed"
POLICY_META_DIR      = DATA_DIR / "policy" / "policies" / "metadata"

# 법령 (data/policy/related_laws/)
LAWS_RAW_DIR       = DATA_DIR / "policy" / "related_laws" / "raw"
LAWS_PROCESSED_DIR = DATA_DIR / "policy" / "related_laws" / "processed"
LAWS_META_DIR      = DATA_DIR / "policy" / "related_laws" / "metadata"

# 뉴스 (data/policy/related_news/)
NEWS_RAW_DIR       = DATA_DIR / "policy" / "related_news" / "raw"
NEWS_BODY_DIR      = DATA_DIR / "policy" / "related_news" / "bodies"
NEWS_PROCESSED_DIR = DATA_DIR / "policy" / "related_news" / "processed"
NEWS_META_DIR      = DATA_DIR / "policy" / "related_news" / "metadata"
