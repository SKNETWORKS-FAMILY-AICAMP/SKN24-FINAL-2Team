"""
뉴스 수집 메인
실행: python main.py
"""
import sys
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_THIS_DIR = Path(__file__).parent
sys.path.insert(0, str(_THIS_DIR))

from collectors.naver import NaverCollector
from collectors.gov24 import Gov24Collector
from config import OUTPUT_DIR, LOG_DIR
from models import Article

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "collector.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")

ALL_COLLECTORS = {
    "naver": lambda: NaverCollector(),
    # "rss":   lambda: RSSCollector(),   # 추후 추가
}


def get_collectors(sources: Optional[List[str]] = None):
    if sources:
        unknown = [s for s in sources if s not in ALL_COLLECTORS]
        if unknown:
            logger.warning(f"알 수 없는 소스: {unknown}")
        return [(name, ALL_COLLECTORS[name]()) for name in sources if name in ALL_COLLECTORS]
    return [(name, factory()) for name, factory in ALL_COLLECTORS.items()]


def deduplicate(articles: List[Article]) -> List[Article]:
    seen, unique = set(), []
    for a in articles:
        if a.url not in seen:
            seen.add(a.url)
            unique.append(a)
    return unique


def _next_index(body_dir: Path, source: str) -> int:
    existing = [f for f in body_dir.iterdir() if f.name.startswith(f"{source}_") and f.suffix == ".txt"]
    return len(existing) + 1


def save_articles(articles: List[Article]) -> int:
    date_str   = datetime.now().strftime("%Y%m%d")
    body_dir   = OUTPUT_DIR / f"news_{date_str}"
    jsonl_path = OUTPUT_DIR / f"news_{date_str}.jsonl"

    body_dir.mkdir(exist_ok=True)
    counters = {}
    saved = 0

    with jsonl_path.open("a", encoding="utf-8") as jf:
        for article in articles:
            src = article.source
            if src not in counters:
                counters[src] = _next_index(body_dir, src)
            idx = counters[src]
            counters[src] += 1

            body_path = body_dir / f"{src}_{idx:04d}.txt"
            try:
                body_path.write_text(article.content, encoding="utf-8")
                article.file_path = str(body_path.relative_to(_THIS_DIR))
            except Exception as e:
                logger.warning(f"본문 저장 실패 [{article.url}]: {e}")
                article.file_path = None

            jf.write(json.dumps(article.to_dict(), ensure_ascii=False) + "\n")
            saved += 1

    logger.info(f"저장 완료 → {jsonl_path.name} / {body_dir.name} ({saved}건)")
    return saved


def run_collection(sources: Optional[List[str]] = None) -> int:
    start = datetime.now()
    label = ", ".join(sources) if sources else "전체"
    logger.info(f"=== 뉴스 수집 시작 [{label}] {start.isoformat()} ===")

    # gov24 → GPT 청년 필터링 → 카테고리 Top5 → 네이버 검색 쿼리 자동 생성
    logger.info("[Gov24] 정책 수집 + GPT 처리 시작...")
    gov24 = Gov24Collector()
    categorized, queries = gov24.collect_and_process(save=True)

    if not queries:
        logger.info("[Gov24] 신규 청년 정책 없음 — 오늘 네이버 수집 생략")
        return 0

    total_cards = sum(len(v) for v in categorized.values())
    logger.info(f"[Gov24] 카테고리 Top5: 총 {total_cards}개")
    for cat, items in categorized.items():
        logger.info(f"    {cat}: {len(items)}개")
    logger.info(f"[Gov24] 네이버 검색 쿼리 {len(queries)}개 준비 완료")

    total_saved = 0
    for name, collector in get_collectors(sources):
        logger.info(f"--- [{name.upper()}] 시작 ---")
        try:
            articles = collector.collect(queries=queries, skip_filter=True)
            unique   = deduplicate(articles)
            saved    = save_articles(unique)
            total_saved += saved
            logger.info(f"--- [{name.upper()}] {len(articles)}건 수집 / {saved}건 저장 ---")
        except Exception as e:
            logger.error(f"--- [{name.upper()}] 오류: {e} ---", exc_info=True)

    elapsed = (datetime.now() - start).total_seconds()
    logger.info(f"=== 수집 완료: 총 {total_saved}건 ({elapsed:.1f}초) ===")
    return total_saved


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="뉴스 수집기")
    parser.add_argument("--source", nargs="+", metavar="SOURCE")
    args = parser.parse_args()
    run_collection(sources=args.source)