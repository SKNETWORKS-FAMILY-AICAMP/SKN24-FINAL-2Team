"""
뉴스 + 법안 + gov24 + 법령 통합 자동 수집 스케줄러
매일 새벽 2시 실행 순서:
  1. 뉴스 수집 (gov24 → GPT 청년 필터 → 네이버 기사 크롤링)
  2. 법안 수집 (열린국회정보 API)
  3. 법령 수집 (법제처 API → gov24 top5 법령 조문)

실행:
  python scheduler.py          # 매일 02:00 자동 실행
  python scheduler.py --now    # 즉시 1회 실행 (테스트용)
"""
import sys
import logging
import argparse
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))

from config import LOG_DIR
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "scheduler.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler")


def job_news():
    """
    gov24 전체 수집 → GPT 청년 필터링 → 청년 정책명으로 네이버 기사 크롤링
    """
    logger.info("━━━ [1/2] 뉴스 수집 시작 ━━━")
    logger.info("    흐름: gov24 수집 → GPT 청년 필터 → 네이버 기사 크롤링")
    try:
        from main import run_collection
        run_collection()  # gov24 → GPT 필터 → 네이버 검색
        logger.info("━━━ [1/2] 뉴스 수집 완료 ━━━")
    except Exception as e:
        logger.error("━━━ [1/2] 뉴스 수집 실패: %s ━━━", e, exc_info=True)


def job_bills():
    """
    열린국회정보 API → 누락 법안 수집 (메타데이터 + 제안이유 + PDF)
    """
    logger.info("━━━ [2/3] 법안 수집 시작 ━━━")
    try:
        import bill_collector
        bill_collector.run()
        logger.info("━━━ [2/3] 법안 수집 완료 ━━━")
    except Exception as e:
        logger.error("━━━ [2/3] 법안 수집 실패: %s ━━━", e, exc_info=True)


def job_laws():
    """
    법제처 API → gov24 top5 법령 조문 수집 → data/laws/ 저장
    반드시 job_news() 이후 실행 (top5 CSV가 있어야 함)
    """
    logger.info("━━━ [3/3] 법령 수집 시작 ━━━")
    try:
        from config import DATA_DIR, LAW_API_OC
        from law_collector import LawCollector
        from pathlib import Path

        if not LAW_API_OC:
            logger.error("━━━ [3/3] LAW_API_OC 환경변수 없음 — 법령 수집 생략 ━━━")
            return

        meta_dir   = DATA_DIR / "metadata"
        top5_files = sorted(Path(meta_dir).glob("gov24_top5_*.csv"), reverse=True)
        if not top5_files:
            logger.error("━━━ [3/3] top5 CSV 없음 — gov24 수집 먼저 실행 필요 ━━━")
            return

        top5_csv = top5_files[0]
        logger.info(f"[Law] top5 파일: {top5_csv.name}")

        collector = LawCollector(oc=LAW_API_OC)
        saved = collector.collect_from_top5(top5_csv)
        logger.info("━━━ [3/3] 법령 수집 완료 (%d개) ━━━", saved)
    except Exception as e:
        logger.error("━━━ [3/3] 법령 수집 실패: %s ━━━", e, exc_info=True)


def run_all():
    start = datetime.now()
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  통합 수집 시작: %s  ║", start.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("╚══════════════════════════════════════╝")

    job_news()   # gov24 → GPT → 네이버 뉴스
    job_bills()  # 국회 법안
    job_laws()   # 법제처 법령 조문

    elapsed = (datetime.now() - start).total_seconds()
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║  통합 수집 완료 (%.1f초)              ║", elapsed)
    logger.info("╚══════════════════════════════════════╝")


def start_scheduler():
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("apscheduler 미설치: pip install apscheduler")
        sys.exit(1)

    scheduler = BlockingScheduler(timezone="Asia/Seoul")
    scheduler.add_job(
        run_all,
        trigger=CronTrigger(hour=2, minute=0),
        id="daily_collection",
        name="뉴스 + 법안 + 법령 일간 수집",
        misfire_grace_time=600,
    )
    next_run = scheduler.get_jobs()[0].next_run_time
    logger.info("스케줄러 시작 — 매일 02:00 KST")
    logger.info("다음 실행: %s", next_run.strftime("%Y-%m-%d %H:%M:%S %Z") if next_run else "미정")
    logger.info("종료: Ctrl+C")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="뉴스 + 법안 + 법령 통합 스케줄러")
    parser.add_argument("--now", action="store_true", help="즉시 1회 실행 (테스트)")
    args = parser.parse_args()

    if args.now:
        run_all()
    else:
        start_scheduler()
