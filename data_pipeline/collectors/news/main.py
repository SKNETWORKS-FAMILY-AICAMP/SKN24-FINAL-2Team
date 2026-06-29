"""
collectors/news/main.py
Fargate 진입점

실행:
  python main.py                   # 전체 파이프라인 (수집 → RDS → Qdrant)
  python main.py --step collect    # STEP 1~7-1: 수집·전처리만, JSON 저장
  python main.py --step rds        # 저장된 JSON → RDS 적재
  python main.py --step qdrant     # JSON → Qdrant 적재 (rds 선행 필요)
  python main.py --today-only      # 당일 기사만 수집
  python main.py --max-articles 500 # 키워드당 수집 기사 수 (기본 1000)

권장 순서:
  1. python main.py --step collect
  2. data/news/preprocessed/*.json 열어서 데이터 확인
  3. python main.py --step rds
  4. python main.py --step qdrant
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# ── import 경로 ───────────────────────────────────────────────────────────
BASE_DIR    = Path(__file__).resolve().parent   # collectors/news
PROJECT_DIR = BASE_DIR.parent.parent            # 프로젝트 루트

for p in (PROJECT_DIR, BASE_DIR):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BASE_DIR / ".env")
load_dotenv()

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ── 경로 ──────────────────────────────────────────────────────────────────
# 출력: 프로젝트루트/data/news/ 하위
# .env에 PIPELINE_OUTPUT_DIR 지정 시 override
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "data" / "news"
OUTPUT_DIR   = Path(os.getenv("PIPELINE_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))).expanduser().resolve()
RAW_DIR      = OUTPUT_DIR / "raw"
CLEAN_DIR    = OUTPUT_DIR / "preprocessed"
METADATA_DIR = OUTPUT_DIR / "metadata"

for directory in (RAW_DIR, CLEAN_DIR, METADATA_DIR):
    directory.mkdir(parents=True, exist_ok=True)

logger.info(f"[PATH] OUTPUT_DIR = {OUTPUT_DIR}")

# ── 환경 변수 ──────────────────────────────────────────────────────────────
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
DB_HOST             = os.getenv("DB_HOST", "")
DB_PORT             = int(os.getenv("DB_PORT", "3306"))
DB_NAME             = os.getenv("DB_NAME", "")
DB_USER             = os.getenv("DB_USER", "")
DB_PASSWORD         = os.getenv("DB_PASSWORD", "")


# ── import 헬퍼 ───────────────────────────────────────────────────────────

def import_news_collector():
    try:
        import news_collector
        return news_collector
    except ModuleNotFoundError:
        from collectors.news import news_collector
        return news_collector


def import_rds_module():
    try:
        from storage import rds
        return rds
    except ModuleNotFoundError:
        import rds
        return rds


def get_qdrant_handler():
    """QdrantHandler 초기화 (주소 하드코딩)."""
    try:
        from storage.news_qdrant_handler import QdrantHandler
        handler = QdrantHandler()
        logger.info("[Qdrant] 연결 완료")
        return handler
    except Exception as e:
        logger.warning(f"[Qdrant] 초기화 실패 — Qdrant 적재 스킵: {e}")
        return None


def validate_required_env(need_db: bool = True) -> None:
    required = {
        "NAVER_CLIENT_ID":     NAVER_CLIENT_ID,
        "NAVER_CLIENT_SECRET": NAVER_CLIENT_SECRET,
        "OPENAI_API_KEY":      OPENAI_API_KEY,
    }
    if need_db:
        required.update({
            "DB_HOST":     DB_HOST,
            "DB_NAME":     DB_NAME,
            "DB_USER":     DB_USER,
            "DB_PASSWORD": DB_PASSWORD,
        })
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"필수 환경 변수가 비어 있습니다: {', '.join(missing)}")


# ══════════════════════════════════════════════════════════════════════════
# STEP A: 수집·전처리만 (JSON 저장, DB 미적재)
# ══════════════════════════════════════════════════════════════════════════

async def step_collect(max_articles: int, today_only: bool) -> None:
    """
    STEP 1~7-1: 기사 수집 + 찬반 분류 + 전처리 + stance 필터까지만 실행.
    결과는 data/news/preprocessed/*.json 에 저장됨.
    DB/Qdrant 적재 없음.

    실행 후 JSON 파일을 직접 열어 데이터 품질 확인 후
    이상 없으면 --step rds 로 적재하세요.
    """
    validate_required_env(need_db=False)
    news_collector = import_news_collector()

    logger.info("=== [STEP collect] 수집·전처리 시작 (DB 적재 없음) ===")
    await news_collector.run(
        raw_dir             = RAW_DIR,
        clean_dir           = CLEAN_DIR,
        metadata_dir        = METADATA_DIR,
        naver_client_id     = NAVER_CLIENT_ID,
        naver_client_secret = NAVER_CLIENT_SECRET,
        openai_api_key      = OPENAI_API_KEY,
        db_conn             = None,
        qdrant_handler      = None,
        max_articles        = max_articles,
        today_only          = today_only,
    )

    # 수집 결과 요약 출력
    clean_files = sorted(CLEAN_DIR.glob("*_clean.json"))
    logger.info(f"\n{'='*60}")
    logger.info(f"[collect 완료] 전처리 파일 {len(clean_files)}개:")
    for f in clean_files:
        arts    = json.loads(f.read_text(encoding="utf-8"))
        pro     = sum(1 for a in arts if a.get("stance") == "pro")
        con     = sum(1 for a in arts if a.get("stance") == "con")
        neutral = sum(1 for a in arts if a.get("stance") == "neutral")
        logger.info(f"  {f.name}  총 {len(arts)}건  찬성 {pro} / 반대 {con} / 중립 {neutral}")
    logger.info(f"{'='*60}")
    logger.info(f"파일 위치: {CLEAN_DIR}")
    logger.info("내용 확인 후 이상 없으면 --step rds 로 적재하세요.")


# ══════════════════════════════════════════════════════════════════════════
# STEP B: 저장된 JSON → RDS 적재
# ══════════════════════════════════════════════════════════════════════════

def step_rds() -> None:
    """
    data/news/preprocessed/*.json 을 읽어 RDS에 적재.
    적재 후 --step qdrant 로 Qdrant 적재 진행.
    """
    validate_required_env(need_db=True)
    rds = import_rds_module()

    clean_files = sorted(CLEAN_DIR.glob("*_clean.json"))
    if not clean_files:
        logger.warning(f"[rds] 전처리 파일 없음 ({CLEAN_DIR}) — 먼저 --step collect 를 실행하세요.")
        return

    db_conn = rds.get_connection(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD)
    total_inserted = 0

    try:
        logger.info(f"=== [STEP rds] RDS 적재 시작: {len(clean_files)}개 파일 ===")
        for f in clean_files:
            arts     = json.loads(f.read_text(encoding="utf-8"))
            logger.info(f"  {f.name}: {len(arts)}건 처리 중...")
            inserted = rds.insert_articles(db_conn, arts)
            rds.save_metadata_csv(inserted, METADATA_DIR)

            # data_id를 JSON에 다시 저장 (qdrant 단계에서 사용)
            id_map = {
                (a.get("source_url") or a.get("url", "")): a["data_id"]
                for a in inserted if a.get("data_id")
            }
            for art in arts:
                art_url = art.get("source_url") or art.get("url", "")
                if art_url in id_map:
                    art["data_id"] = id_map[art_url]
            f.write_text(json.dumps(arts, ensure_ascii=False, indent=2), encoding="utf-8")

            total_inserted += len(inserted)
            logger.info(f"  → {len(inserted)}건 신규 적재")
    finally:
        db_conn.close()

    logger.info(f"[rds 완료] 총 {total_inserted}건 적재")
    logger.info("이상 없으면 --step qdrant 로 벡터 적재하세요.")


# ══════════════════════════════════════════════════════════════════════════
# STEP C: Qdrant 적재
# ══════════════════════════════════════════════════════════════════════════

def step_qdrant() -> None:
    """
    data/news/preprocessed/*.json 을 읽어 Qdrant에 적재.
    data_id 없는 기사는 스킵 (--step rds 선행 필요).
    """
    qdrant = get_qdrant_handler()
    if qdrant is None:
        return

    clean_files = sorted(CLEAN_DIR.glob("*_clean.json"))
    if not clean_files:
        logger.warning(f"[qdrant] 전처리 파일 없음 — 먼저 --step collect, --step rds 를 실행하세요.")
        return

    all_arts = []
    for f in clean_files:
        all_arts.extend(json.loads(f.read_text(encoding="utf-8")))

    ready     = [a for a in all_arts if a.get("data_id")]
    not_ready = len(all_arts) - len(ready)
    if not_ready:
        logger.warning(f"[qdrant] data_id 없는 기사 {not_ready}건 스킵 — --step rds 먼저 실행했는지 확인하세요.")

    logger.info(f"=== [STEP qdrant] Qdrant 적재 시작: {len(ready)}건 ===")
    upserted = qdrant.upsert_articles(ready)
    logger.info(f"[qdrant 완료] {upserted}건 적재")


# ══════════════════════════════════════════════════════════════════════════
# 전체 파이프라인 (배포용)
# ══════════════════════════════════════════════════════════════════════════

async def main(max_articles: int = 1000, today_only: bool = False) -> None:
    validate_required_env(need_db=True)

    news_collector = import_news_collector()
    rds            = import_rds_module()
    qdrant         = get_qdrant_handler()
    db_conn        = rds.get_connection(DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD)

    try:
        trigger_category_ids = await news_collector.run(
            raw_dir             = RAW_DIR,
            clean_dir           = CLEAN_DIR,
            metadata_dir        = METADATA_DIR,
            naver_client_id     = NAVER_CLIENT_ID,
            naver_client_secret = NAVER_CLIENT_SECRET,
            openai_api_key      = OPENAI_API_KEY,
            db_conn             = db_conn,
            qdrant_handler      = qdrant,
            max_articles        = max_articles,
            today_only          = today_only,
        )

        # ── 정책 수집 파이프라인 (추후 추가) ─────────────────────────────
        # await policy_collector.run(...)

        # ── 법안 수집 파이프라인 (추후 추가) ─────────────────────────────
        # await bill_collector.run(...)

        if trigger_category_ids:
            logger.info(f"카드 생성 트리거: {len(trigger_category_ids)}개 카테고리")
            for category_id in trigger_category_ids:
                logger.info(f"  category_id={category_id} → NewsCardGenerator 호출 필요")
                # TODO: NewsCardGenerator 연동
                # from agents.news_card.graph import NewsCardGenerator
                # articles = rds.get_articles_by_category(db_conn, category_id)
                # generator = NewsCardGenerator(qdrant_client, openai_client)
                # generator.run(articles, save=True)

    finally:
        db_conn.close()

    logger.info("전체 파이프라인 완료")


# ══════════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Policity 뉴스 수집 파이프라인")
    parser.add_argument(
        "--step",
        choices=["collect", "rds", "qdrant"],
        default=None,
        help=(
            "단계별 실행:\n"
            "  collect : 수집·전처리만 (JSON 저장, DB 없음)\n"
            "  rds     : JSON → RDS 적재\n"
            "  qdrant  : JSON → Qdrant 적재 (rds 선행 필요)\n"
            "  생략 시 전체 파이프라인 한 번에 실행"
        ),
    )
    parser.add_argument("--max-articles", type=int, default=1000, help="키워드당 수집 기사 수 (기본 1000, API 최대 1000)")
    parser.add_argument("--today-only",   action="store_true",   help="당일 기사만 수집")
    args = parser.parse_args()

    if args.step == "collect":
        asyncio.run(step_collect(args.max_articles, args.today_only))
    elif args.step == "rds":
        step_rds()
    elif args.step == "qdrant":
        step_qdrant()
    else:
        asyncio.run(main(args.max_articles, args.today_only))