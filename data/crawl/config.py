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
LAW_API_OC          = os.getenv("LAW_API_OC", "")          # 법제처 open.law.go.kr 가입 이메일 @ 앞부분

# ── 수집 공통 설정 ─────────────────────────────────────────────────────────
REQUEST_TIMEOUT  = 10
MAX_RETRY        = 3
RETRY_DELAY      = 2
CRAWL_DELAY_MIN  = 0.5
CRAWL_DELAY_MAX  = 1.5

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ── 저장 경로 ──────────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).parent
OUTPUT_DIR  = BASE_DIR / "output"
LOG_DIR     = BASE_DIR / "logs"
DATA_DIR    = BASE_DIR / "data"