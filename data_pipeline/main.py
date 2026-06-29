"""
POLICITY 전체 데이터 파이프라인

Fargate 실행 진입점:
  - 정책 수집/전처리
  - RDS 적재
  - 스케줄러 (매일 새벽 2시)

실행:
  python main.py                  # 전체 실행
  python main.py --only upload    # 적재만 실행
  python main.py --skip-upload    # 수집/전처리만 실행
  python main.py --schedule       # 스케줄러 시작 (매일 02:00)
  python main.py --now            # 즉시 1회 실행
"""

import sys
import csv
import json
import logging
import argparse
from datetime import datetime
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).parent
sys.path.insert(0, str(_ROOT))

from config import (
    LOG_DIR,
    NEWS_RAW_DIR, NEWS_BODY_DIR, NEWS_META_DIR, NEWS_PROCESSED_DIR,
    POLICY_RAW_DIR, POLICY_PROCESSED_DIR, POLICY_META_DIR,
    LAWS_PROCESSED_DIR, LAW_API_OC,
)

for d in [NEWS_RAW_DIR, NEWS_BODY_DIR, NEWS_META_DIR, NEWS_PROCESSED_DIR,
          POLICY_RAW_DIR, POLICY_PROCESSED_DIR, POLICY_META_DIR,
          LAWS_PROCESSED_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "main.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ══════════════════════════════════════════════════════════════════════════
# 유틸
# ══════════════════════════════════════════════════════════════════════════

def _deduplicate(articles) -> list:
    seen, unique = set(), []
    for a in articles:
        if a.url not in seen:
            seen.add(a.url)
            unique.append(a)
    return unique


def _next_index(body_dir: Path, source: str) -> int:
    existing = [
        f for f in body_dir.iterdir()
        if f.name.startswith(f"{source}_") and f.suffix == ".txt"
    ]
    return len(existing) + 1


def _save_articles(articles) -> int:
    date_str   = datetime.now().strftime("%Y%m%d")
    body_dir   = NEWS_BODY_DIR / f"news_{date_str}"
    jsonl_path = NEWS_RAW_DIR  / f"news_{date_str}.jsonl"
    csv_path   = NEWS_META_DIR / f"news_{date_str}.csv"

    body_dir.mkdir(exist_ok=True)
    counters, saved, csv_rows = {}, 0, []

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
                article.file_path = str(body_path.relative_to(_ROOT))
            except Exception as e:
                logger.warning(f"본문 저장 실패 [{article.url}]: {e}")
                article.file_path = None

            jf.write(json.dumps(article.to_dict(), ensure_ascii=False) + "\n")
            csv_rows.append(article.to_dict())
            saved += 1

    write_header = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8-sig", newline="") as cf:
        fields = ["keyword_matched", "category", "publisher", "title",
                  "published_at", "url", "source", "file_path", "content"]
        writer = csv.DictWriter(cf, fieldnames=fields)
        if write_header:
            writer.writeheader()
        writer.writerows(csv_rows)

    logger.info(f"뉴스 저장 완료 → {jsonl_path.name} ({saved}건)")
    return saved


# ══════════════════════════════════════════════════════════════════════════
# 1. 수집/전처리 파이프라인
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
# 1-0. 뉴스 카드용 수집/전처리 파이프라인
# ══════════════════════════════════════════════════════════════════════════

def run_news() -> bool:
    """
    collectors/news/main.py 전체 파이프라인 호출.
    수집 → 전처리 → RDS 적재 → Qdrant 적재
    결과 파일: data/news/preprocessed/naver_*_clean.json
    """
    logger.info("━━━ [News] 뉴스 수집/전처리 시작 ━━━")
    start = datetime.now()
    try:
        import asyncio
        from collectors.news.main import main as news_main
        asyncio.run(news_main())
        elapsed = (datetime.now() - start).total_seconds()
        logger.info("━━━ [News] 뉴스 수집/전처리 완료 (%.1f초) ━━━", elapsed)
        return True
    except Exception as e:
        logger.error("━━━ [News] 뉴스 수집/전처리 실패: %s ━━━", e, exc_info=True)
        return False


def run_policy() -> bool:
    logger.info("━━━ [Policy] 정책 수집/전처리 시작 ━━━")
    start = datetime.now()

    try:
        from collectors.policy.gov24 import Gov24Collector
        from collectors.policy.naver import NaverCollector
        from collectors.policy.law_collector import LawCollector

        # Step 1. gov24 수집
        logger.info("━━ [1/6] gov24 수집 시작 ━━")
        gov24 = Gov24Collector()
        # categorized, queries, query_to_category = gov24.collect_and_process(save=True)
        from storage import rds as _rds
        from config import RDS_HOST, RDS_PORT, RDS_DATABASE, RDS_USER, RDS_PASSWORD
        db_conn = None
        try:
            db_conn = _rds.get_connection(RDS_HOST, RDS_PORT, RDS_DATABASE, RDS_USER, RDS_PASSWORD)
            categorized, queries, query_to_category = gov24.collect_and_process(save=True, db_conn=db_conn)
        except Exception as e:
            logger.warning(f"[Gov24] RDS 연결 실패 — db_conn 없이 진행: {e}")
            db_conn = None
            categorized, queries, query_to_category = gov24.collect_and_process(save=True, db_conn=None)
        finally:
            if db_conn:
                db_conn.close()

        if not queries:
            logger.info("[Gov24] 신규 청년 정책 없음 — 법령/뉴스 수집 생략")
            return False

        total_cards = sum(len(v) for v in categorized.values())
        logger.info(f"[Gov24] Top5: {total_cards}개 정책 / {len(queries)}개 쿼리")

        # Step 2. 법령 수집
        logger.info("━━ [2/6] 법령 수집 시작 ━━")
        try:
            if not LAW_API_OC:
                logger.error("[Law] LAW_API_OC 없음 — 건너뜀")
            else:
                top5_files = sorted(POLICY_META_DIR.glob("gov24_top5_*.csv"), reverse=True)
                if top5_files:
                    LawCollector(oc=LAW_API_OC).collect_from_top5(top5_files[0])
                    logger.info("[Law] 법령 수집 완료")
        except Exception as e:
            logger.error(f"[Law] 오류: {e}", exc_info=True)

        # Step 3. 정책 전처리
        logger.info("━━ [3/6] 정책 전처리 시작 ━━")
        try:
            from parsers.policy_parser import parse_policies_from_json
            top5_json_files = sorted(POLICY_RAW_DIR.glob("gov24_top5_[0-9]*.json"), reverse=True)
            if top5_json_files:
                policies = parse_policies_from_json(top5_json_files[0])
                today = datetime.now().strftime("%Y-%m-%d")
                clean_path = POLICY_PROCESSED_DIR / f"gov24_top5_clean_{today}.json"
                clean_path.write_text(
                    json.dumps(policies, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                logger.info(f"[정책전처리] {len(policies)}개 → {clean_path.name}")
        except Exception as e:
            logger.error(f"[정책전처리] 오류: {e}", exc_info=True)

        # Step 4. 법령 전처리
        logger.info("━━ [4/6] 법령 전처리 시작 ━━")
        try:
            from parsers.law_parser import preprocess as preprocess_law
            grouped_path = LAWS_PROCESSED_DIR / "law_grouped.json"
            today = datetime.now().strftime("%Y-%m-%d")
            clean_path = LAWS_PROCESSED_DIR / f"law_grouped_clean_{today}.json"
            if grouped_path.exists():
                result = preprocess_law(input_path=grouped_path, output_path=clean_path)
                logger.info(f"[법령전처리] {result['laws']}개 법령 / {result['articles']}개 조문")
            else:
                logger.warning("[법령전처리] law_grouped.json 없음 — 건너뜀")
        except Exception as e:
            logger.error(f"[법령전처리] 오류: {e}", exc_info=True)

        # Step 5. 뉴스 수집
        logger.info("━━ [5/6] 뉴스 수집 시작 ━━")
        try:
            collector = NaverCollector(use_gpt_filter=True)
            articles  = collector.collect(queries=queries, skip_filter=False)
            for article in articles:
                article.category = query_to_category.get(article.keyword_matched, "기타")
            unique = _deduplicate(articles)
            saved  = _save_articles(unique)
            logger.info(f"[Naver] {len(articles)}건 수집 / {len(unique)}건 저장")
        except Exception as e:
            logger.error(f"[Naver] 오류: {e}", exc_info=True)

        # Step 6. 뉴스 전처리
        logger.info("━━ [6/6] 뉴스 전처리 시작 ━━")
        try:
            from parsers.news_parser import preprocess
            date_str   = datetime.now().strftime("%Y%m%d")
            output_dir = NEWS_PROCESSED_DIR / date_str
            result = preprocess(
                input_dir=NEWS_RAW_DIR,
                pattern=f"news_{date_str}.jsonl",
                output_dir=output_dir,
                min_chars=120,
            )
            logger.info(f"[전처리] keep:{result['kept']} / review:{result['review']} / drop:{result['dropped']}")
        except Exception as e:
            logger.error(f"[전처리] 오류: {e}", exc_info=True)

        elapsed = (datetime.now() - start).total_seconds()
        logger.info(f"━━━ [Policy] 정책 수집/전처리 완료 ({elapsed:.1f}초) ━━━")
        return True

    except Exception as e:
        logger.error("━━━ [Policy] 정책 수집/전처리 실패: %s ━━━", e, exc_info=True)
        return False


# ══════════════════════════════════════════════════════════════════════════
# 2. RDS 적재
# ══════════════════════════════════════════════════════════════════════════

def _safe_upload(module, func_name: str) -> bool:
    func = getattr(module, func_name, None)
    if func is None:
        logger.warning("[Upload] vector_uploader.%s() 없음 → 건너뜀", func_name)
        return False
    try:
        logger.info("[Upload] %s 시작", func_name)
        func()
        logger.info("[Upload] %s 완료", func_name)
        return True
    except Exception as e:
        logger.error("[Upload] %s 실패: %s", func_name, e, exc_info=True)
        return False


def run_upload(targets: list = None) -> dict:
    logger.info("━━━ [Upload] RDS + Qdrant 적재 시작 ━━━")

    try:
        from storage.policy_rds_uploader import upload_policies, upload_news
        import types
        rds_uploader = types.SimpleNamespace(
            upload_policies=upload_policies,
            upload_news=upload_news,
        )
    except Exception as e:
        logger.error("[Upload] policy_rds_uploader import 실패: %s", e, exc_info=True)
        return {}

    targets  = targets or ["policy", "news"]
    func_map = {"policy": "upload_policies", "news": "upload_news"}
    results  = {}

    # ── RDS 적재 ──────────────────────────────────────────────────────────
    for target in targets:
        func_name = func_map.get(target)
        if not func_name:
            results[target] = False
            continue
        results[target] = _safe_upload(rds_uploader, func_name)

    # ── Qdrant 적재 ───────────────────────────────────────────────────────
    try:
        from storage.policy_qdrant_uploader import (
            upload_policies as q_upload_policies,
            upload_laws,
            upload_news as q_upload_news,
        )
        logger.info("[Upload] Qdrant 적재 시작")
        if "policy" in targets:
            q_upload_policies()
            upload_laws()
        if "news" in targets:
            q_upload_news()
        logger.info("[Upload] Qdrant 적재 완료")
    except Exception as e:
        logger.error("[Upload] Qdrant 적재 실패: %s", e, exc_info=True)

    logger.info("━━━ [Upload] RDS + Qdrant 적재 완료: %s ━━━", results)
    return results


# ══════════════════════════════════════════════════════════════════════════
# 3. 전체 실행
# ══════════════════════════════════════════════════════════════════════════

def run_all(skip_upload: bool = False) -> None:
    start = datetime.now()

    logger.info("╔══════════════════════════════════════╗")
    logger.info("║      POLICITY 전체 파이프라인 시작      ║")
    logger.info("║      시작 시각: %s      ║", start.strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("╚══════════════════════════════════════╝")

    news_ok   = run_news()
    policy_ok = run_policy()

    if skip_upload:
        logger.info("[AllFlow] --skip-upload → 적재 생략")
    else:
        targets = []
        if news_ok:
            targets.append("news")
        if policy_ok:
            targets.append("policy")
        if targets:
            run_upload(targets)
        else:
            logger.warning("[AllFlow] 수집 전부 실패 → 적재 생략")

        # ── 카드 생성 ───────────────────────────────────────────────────
        try:
            from cards.card_generation import run_card_generation
            logger.info("━━━ [Card] 카드 생성 시작 ━━━")
            card_results = run_card_generation(["news", "policy"])
            logger.info("━━━ [Card] 카드 생성 완료: %s ━━━", card_results)
        except Exception as e:
            logger.error("[Card] 카드 생성 실패: %s", e, exc_info=True)

    elapsed = (datetime.now() - start).total_seconds()
    logger.info("╔══════════════════════════════════════╗")
    logger.info("║      POLICITY 전체 파이프라인 완료      ║")
    logger.info("║      소요 시간: %.1f초      ║", elapsed)
    logger.info("╚══════════════════════════════════════╝")

# ══════════════════════════════════════════════════════════════════════════
# 5. CLI
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="POLICITY 전체 데이터 파이프라인")

    parser.add_argument("--only", choices=["policy", "upload", "card", "card-test"], 
                        help="특정 단계만 실행")
    parser.add_argument("--skip-upload", action="store_true", help="수집/전처리만 실행")
    parser.add_argument("--now", action="store_true", help="즉시 1회 실행")
    parser.add_argument("--type", choices=["news", "policy", "all"], default="all",
                        help="카드 생성 대상 (--only card / card-test 와 함께 사용)")

    args = parser.parse_args()

    if args.now:
        run_all()

    elif args.only == "card":
        # 실제 카드 생성 + RDS 저장 + Qdrant 업로드
        from cards.card_generation import run_card_generation
        targets = ["news", "policy"] if args.type == "all" else [args.type]
        logger.info("━━━ [Card] 카드 생성 시작 ━━━")
        result = run_card_generation(targets)
        logger.info("━━━ [Card] 카드 생성 완료: %s ━━━", result)

    elif args.only == "card-test":
        # 로컬 JSON 저장 테스트용
        from cards.test_card_save import test_news_cards, test_policy_cards
        if args.type in ("news", "all"):
            test_news_cards()
        if args.type in ("policy", "all"):
            test_policy_cards()

    elif args.only == "policy":
        ok = run_policy()
        if ok and not args.skip_upload:
            run_upload(["policy", "news"])

    elif args.only == "upload":
        run_upload(["policy", "news"])

    else:
        run_all(skip_upload=args.skip_upload)