"""
card_generator.py
AI 서버 카드 생성 API 호출 + RDS 저장

흐름:
  뉴스 카드:
    data/news/naver_*_clean.json  (파일 1개 = 키워드 1개)
      → 파일 단위로 바로 처리
      → POST /cards/generate/news  (save=True → Qdrant 자동 저장)
      → 응답 → RDS INFO_CARDS + Qdrant card_id 업데이트

  정책 카드:
    data/policy/policies/processed/gov24_top5_clean_*.json  (정책)
    + data/policy/related_laws/processed/law_grouped_clean_*.json  (법령, service_id 매핑)
    + data/policy/related_news/processed/{date}/cleaned_news.jsonl  (관련 뉴스)
      → POST /cards/generate/policy  (save=True → Qdrant 자동 저장)
      → 응답 → RDS INFO_CARDS + Qdrant card_id 업데이트

실행:
  python card_generator.py --type news
  python card_generator.py --type policy
  python card_generator.py --type all
  python card_generator.py --type all --force
"""

import json
import logging
import argparse
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# ── 경로 설정 ──────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from config import (
    LOG_DIR,
    DATA_DIR,
    POLICY_PROCESSED_DIR,
    LAWS_PROCESSED_DIR,
    MOCK_MODE,
    RDS_HOST, RDS_PORT, RDS_USER, RDS_PASSWORD, RDS_DATABASE,
    QDRANT_HOST, QDRANT_PORT,
)

# naver_*_clean.json 위치
NEWS_CLEAN_DIR            = DATA_DIR / "news" / "preprocessed"
# 정책 관련 뉴스 위치
POLICY_NEWS_PROCESSED_DIR = DATA_DIR / "policy" / "related_news" / "processed"

AI_AGENT_URL     = os.getenv("AI_AGENT_URL", "http://localhost:8000")
CARD_GEN_TIMEOUT = int(os.getenv("CARD_GEN_TIMEOUT", "600"))

NEWS_MIN_ARTICLES   = 3
POLICY_MAX_ARTICLES = 5

# Qdrant 카드 컬렉션명 (AI 서버와 동일하게 맞춤)
COLLECTION_CARDS = "policity_cards"

# CATEGORIES 테이블 매핑
CATEGORY_MAP = {
    "일자리":  1,
    "교육":    2,
    "주거":    3,
    "금융":    4,
    "생활복지": 5,
    "복지":    5,
    "문화":    6,
}

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "card_generator.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("card_generator")


# ══════════════════════════════════════════════════════════════════════════
# 공통 유틸
# ══════════════════════════════════════════════════════════════════════════

def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _latest_file(directory: Path, pattern: str) -> Optional[Path]:
    files = sorted(directory.glob(pattern), reverse=True)
    return files[0] if files else None


def _post(endpoint: str, payload: dict) -> Optional[dict]:
    """AI Agent API 호출. 실패 시 None 반환."""
    url = f"{AI_AGENT_URL}{endpoint}"
    try:
        resp = requests.post(url, json=payload, timeout=CARD_GEN_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("[API] %s → HTTP %d: %s", endpoint, resp.status_code, resp.text[:200])
        return None
    except requests.RequestException as e:
        logger.error("[API] %s 호출 실패: %s", endpoint, e)
        return None


# ══════════════════════════════════════════════════════════════════════════
# Qdrant card_id 업데이트
# ══════════════════════════════════════════════════════════════════════════

def _update_qdrant_card_id(qdrant_id: str, card_id: int) -> None:
    """
    AI 서버가 임시 ID로 저장한 Qdrant 포인트의 payload.card_id를
    RDS AUTO INCREMENT 실제 card_id로 덮어씀.
    """
    if not qdrant_id:
        logger.warning("[Qdrant] qdrant_id 없음 — card_id 업데이트 생략")
        return
    if MOCK_MODE:
        logger.info("[Qdrant][MOCK] card_id 업데이트 생략: qdrant_id=%s → card_id=%d",
                    qdrant_id, card_id)
        return
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=f"http://{QDRANT_HOST}:{QDRANT_PORT}")
        client.set_payload(
            collection_name=COLLECTION_CARDS,
            payload={"card_id": card_id},
            points=[int(qdrant_id)],
        )
        logger.info("[Qdrant] card_id 업데이트 완료: qdrant_id=%s → card_id=%d",
                    qdrant_id, card_id)
    except Exception as e:
        logger.error("[Qdrant] card_id 업데이트 실패: %s", e, exc_info=True)


# ══════════════════════════════════════════════════════════════════════════
# RDS 저장 (INFO_CARDS 단일 테이블)
# ══════════════════════════════════════════════════════════════════════════

def _get_conn():
    import pymysql
    import pymysql.cursors
    return pymysql.connect(
        host=RDS_HOST, port=int(RDS_PORT),
        user=RDS_USER, password=RDS_PASSWORD, database=RDS_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def save_news_card(card: dict, category: str = "", source_urls: list = None) -> Optional[int]:
    """CardResponse(NEWS) → INFO_CARDS 저장 후 Qdrant card_id 업데이트. 반환: card_id"""
    if MOCK_MODE:
        logger.info("[RDS][MOCK] NEWS 카드 저장 생략: %s", card.get("title", ""))
        return 0

    tabs        = card.get("tabs", {})
    category_id = CATEGORY_MAP.get(category, 5)
    urls_json   = json.dumps(source_urls or [], ensure_ascii=False)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO INFO_CARDS
                  (type, card_title, intro, summary, core_content,
                   perspectives, debate_topic, category_id, source_urls, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    "news",
                    card.get("title", ""),
                    card.get("intro", ""),
                    json.dumps(tabs.get("SUMMARY", {}),  ensure_ascii=False),
                    json.dumps(tabs.get("CORE", ""),     ensure_ascii=False),
                    json.dumps(tabs.get("OPINION", []),  ensure_ascii=False),
                    card.get("debate_topic", ""),
                    category_id,
                    urls_json,
                ),
            )
            card_id = cur.lastrowid

        conn.commit()
        logger.info("[RDS] NEWS 카드 저장 완료 → card_id=%d, title=%s, source_urls=%d건",
                    card_id, card.get("title", ""), len(source_urls or []))

        # Qdrant payload.card_id를 실제 RDS ID로 업데이트
        # _update_qdrant_card_id(card.get("qdrant_id"), card_id)
        from upload_cards import upload_card_to_qdrant
        upload_card_to_qdrant({
            "card_id":   card_id,
            "card_type": "NEWS",  # 또는 "POLICY"
            "title":     card.get("title", ""),
            "tabs":      card.get("tabs", {}),
        })

        return card_id

    except Exception as e:
        conn.rollback()
        logger.error("[RDS] NEWS 카드 저장 실패: %s", e, exc_info=True)
        return None
    finally:
        conn.close()


def save_policy_card(card: dict, category_id: int = 5, source_urls: list = None) -> Optional[int]:
    """CardResponse(POLICY) → INFO_CARDS 저장 후 Qdrant card_id 업데이트. 반환: card_id"""
    if MOCK_MODE:
        logger.info("[RDS][MOCK] POLICY 카드 저장 생략: %s", card.get("title", ""))
        return 0

    tabs        = card.get("tabs", {})
    urls_json   = json.dumps(source_urls or [], ensure_ascii=False)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO INFO_CARDS
                  (type, card_title, intro, summary, core_content,
                   perspectives, debate_topic, category_id, source_urls, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                """,
                (
                    "policy",
                    card.get("title", ""),
                    card.get("intro", ""),
                    json.dumps(tabs.get("SUMMARY", {}),  ensure_ascii=False),
                    json.dumps(tabs.get("CORE", ""),     ensure_ascii=False),
                    json.dumps(tabs.get("OPINION", []),  ensure_ascii=False),
                    card.get("debate_topic", ""),
                    category_id,
                    urls_json,
                ),
            )
            card_id = cur.lastrowid

        conn.commit()
        logger.info("[RDS] POLICY 카드 저장 완료 → card_id=%d, title=%s, source_urls=%d건",
                    card_id, card.get("title", ""), len(source_urls or []))

        # Qdrant payload.card_id를 실제 RDS ID로 업데이트
        # _update_qdrant_card_id(card.get("qdrant_id"), card_id)
        from upload_cards import upload_card_to_qdrant
        upload_card_to_qdrant({
            "card_id":   card_id,
            "card_type": "POLICY",
            "title":     card.get("title", ""),
            "tabs":      card.get("tabs", {}),
        })

        return card_id

    except Exception as e:
        conn.rollback()
        logger.error("[RDS] POLICY 카드 저장 실패: %s", e, exc_info=True)
        return None
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════
# 뉴스 카드 생성
# ══════════════════════════════════════════════════════════════════════════

def _to_article_schema(art: dict) -> dict:
    """naver_*_clean.json 항목 → Article 스키마"""
    return {
        "title":     art.get("title", ""),
        "content":   art.get("content", ""),
        "url":       art.get("url", ""),
        "publisher": art.get("publisher", art.get("press", "")),
        "press":     art.get("press", art.get("publisher", "")),
    }


def _select_articles(articles: list) -> list:
    """
    최신순 정렬 후 찬성 4 / 반대 4 / 중립 2 선택
    stance 필드: pro / con / neutral
    """
    sorted_arts = sorted(articles, key=lambda a: a.get("published_at", ""), reverse=True)
    pro     = [a for a in sorted_arts if a.get("stance") == "pro"][:4]
    con     = [a for a in sorted_arts if a.get("stance") == "con"][:4]
    neutral = [a for a in sorted_arts if a.get("stance") == "neutral"][:2]
    return pro + con + neutral


def generate_news_cards(force: bool = False) -> dict:
    """
    naver_*_clean.json 파일 1개 = 키워드 1개 → 카드 1장
    찬성/반대 각 1건 이상 + 전체 최소 3건 이상이어야 생성
    """
    results = {"성공": 0, "실패": 0, "스킵": 0}
    files   = sorted(NEWS_CLEAN_DIR.glob("naver_*_clean.json"))

    if not files:
        logger.warning("[News] naver_*_clean.json 없음: %s", NEWS_CLEAN_DIR)
        return results

    logger.info("[News] 처리 대상 파일 %d개", len(files))

    for f in files:
        try:
            articles = _read_json(f)
            if not isinstance(articles, list):
                logger.warning("[News] 잘못된 형식 스킵: %s", f.name)
                results["스킵"] += 1
                continue
        except Exception as e:
            logger.warning("[News] 파일 읽기 실패 %s: %s", f.name, e)
            results["스킵"] += 1
            continue

        keyword  = articles[0].get("keyword_matched", f.stem) if articles else f.stem
        category = articles[0].get("category_name", "") if articles else ""
        selected = _select_articles(articles)

        pro_cnt     = sum(1 for a in selected if a.get("stance") == "pro")
        con_cnt     = sum(1 for a in selected if a.get("stance") == "con")
        neutral_cnt = sum(1 for a in selected if a.get("stance") == "neutral")

        # 전체 건수 미달 스킵
        if len(selected) < NEWS_MIN_ARTICLES:
            logger.info("[News] '%s' 선택 기사 %d건 → 최소 %d건 미만, 스킵",
                        keyword, len(selected), NEWS_MIN_ARTICLES)
            results["스킵"] += 1
            continue

        # 찬반 균형 미달 스킵 (찬성 또는 반대 0건이면 토론 카드 의미 없음)
        if pro_cnt < 4 or con_cnt < 4 or neutral_cnt < 2:
            logger.info("[News] '%s' 찬성 %d / 반대 %d → 찬반 균형 미달, 스킵",
                        keyword, pro_cnt, con_cnt)
            results["스킵"] += 1
            continue

        logger.info("[News] '%s' → 찬성 %d / 반대 %d / 중립 %d으로 카드 생성 요청",
                    keyword, pro_cnt, con_cnt, neutral_cnt)

        payload = {"articles": [_to_article_schema(a) for a in selected]}
        card    = _post("/cards/generate/news", payload)

        if not card:
            results["실패"] += 1
            continue

        # 카드 생성에 사용된 기사 URL 수집 (빈 문자열 제외)
        source_urls = [a.get("url", "") for a in selected if a.get("url", "")]

        card_id = save_news_card(card, category=category, source_urls=source_urls)
        results["성공" if card_id is not None else "실패"] += 1

    logger.info("[News] 완료: %s", results)
    return results


# ══════════════════════════════════════════════════════════════════════════
# 정책 카드 생성
# ══════════════════════════════════════════════════════════════════════════

def _load_laws_index() -> dict:
    """
    law_grouped_clean_*.json → {service_id: [law, ...]}
    """
    index = defaultdict(list)

    law_path = _latest_file(LAWS_PROCESSED_DIR, "law_grouped_clean_*.json")
    if not law_path:
        logger.warning("[Law] law_grouped_clean_*.json 없음: %s", LAWS_PROCESSED_DIR)
        return index

    laws = _read_json(law_path)
    for law in laws:
        for related in law.get("관련정책", []):
            sid = related.get("서비스ID", "").strip()
            if sid:
                index[sid].append(law)

    logger.info("[Law] 법령 %d건 로드 (service_id 인덱스 %d개)", len(laws), len(index))
    return index


def _load_policy_news_index() -> dict:
    """
    data/policy/related_news/processed/{date}/cleaned_news.jsonl
    → {keyword_matched: [article, ...]}
    """
    index = defaultdict(list)

    if not POLICY_NEWS_PROCESSED_DIR.exists():
        logger.warning("[PolicyNews] 디렉토리 없음: %s", POLICY_NEWS_PROCESSED_DIR)
        return index

    date_dirs = sorted(
        [p for p in POLICY_NEWS_PROCESSED_DIR.iterdir() if p.is_dir()],
        reverse=True,
    )
    cleaned_path = None
    for d in date_dirs:
        candidate = d / "cleaned_news.jsonl"
        if candidate.exists():
            cleaned_path = candidate
            break

    if not cleaned_path:
        cleaned_path = _latest_file(POLICY_NEWS_PROCESSED_DIR, "cleaned_news_*.jsonl")

    if not cleaned_path:
        logger.warning("[PolicyNews] cleaned_news.jsonl 없음")
        return index

    logger.info("[PolicyNews] 로드: %s", cleaned_path)
    for art in _read_jsonl(cleaned_path):
        kw = art.get("keyword_matched", "").strip()
        if kw:
            index[kw].append(art)

    logger.info("[PolicyNews] 키워드 %d개 / 총 %d건",
                len(index), sum(len(v) for v in index.values()))
    return index


def _to_policy_source_schema(policy: dict) -> dict:
    """gov24_top5_clean_*.json 항목 → PolicySource 스키마"""
    raw_content = policy.get("content", "")
    try:
        c = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
    except (json.JSONDecodeError, ValueError):
        c = {}

    return {
        "id":      policy.get("service_id", ""),
        "name":    policy.get("title", ""),
        "content": c.get("지원내용", raw_content),
        "target":  c.get("지원대상", ""),
        "method":  c.get("신청방법", ""),
        "period":  c.get("신청기한", policy.get("apply_period", "")),
        "contact": c.get("문의처", ""),
        "org":     policy.get("department", c.get("소관기관명", "")),
        "url":     policy.get("source_url", c.get("온라인신청사이트URL", "")),
    }


def _to_policy_article_schema(art: dict) -> dict:
    """cleaned_news.jsonl 항목 → Article 스키마"""
    return {
        "title":     art.get("title", ""),
        "content":   art.get("content", ""),
        "url":       art.get("url", ""),
        "publisher": art.get("publisher", ""),
        "press":     art.get("publisher", ""),
    }


def generate_policy_cards(force: bool = False) -> dict:
    results = {"성공": 0, "실패": 0, "스킵": 0}

    policy_path = _latest_file(POLICY_PROCESSED_DIR, "gov24_top5_clean_*.json")
    if not policy_path:
        logger.warning("[Policy] gov24_top5_clean_*.json 없음: %s", POLICY_PROCESSED_DIR)
        return results

    policies   = _read_json(policy_path)
    laws_index = _load_laws_index()
    news_index = _load_policy_news_index()

    logger.info("[Policy] 처리 대상 %d건", len(policies))

    for policy in policies:
        title      = policy.get("title", "").strip()
        service_id = policy.get("service_id", "").strip()
        category_id = policy.get("category_id", 5)  # 수집 시점의 category_id 직접 사용

        if not title:
            results["스킵"] += 1
            continue

        related_articles = news_index.get(title, [])[:POLICY_MAX_ARTICLES]
        related_laws     = laws_index.get(service_id, [])[:3]

        payload = {
            "source":           _to_policy_source_schema(policy),
            "related_articles": [_to_policy_article_schema(a) for a in related_articles],
            "related_laws":     related_laws,
        }

        logger.info("[Policy] '%s' → 관련기사 %d건 / 관련법령 %d건으로 카드 생성 요청",
                    title, len(related_articles), len(related_laws))
        card = _post("/cards/generate/policy", payload)

        if not card:
            results["실패"] += 1
            continue

        # 카드 생성에 사용된 출처 URL 수집
        # 1) 정책 자체 URL (온라인신청 또는 source_url)
        # 2) 관련기사 URL
        policy_url   = policy.get("source_url", "")
        article_urls = [a.get("url", "") for a in related_articles if a.get("url", "")]
        source_urls  = [u for u in ([policy_url] + article_urls) if u]

        card_id = save_policy_card(card, category_id=category_id, source_urls=source_urls)
        results["성공" if card_id is not None else "실패"] += 1

    logger.info("[Policy] 완료: %s", results)
    return results


# ══════════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════════

def run_card_generation(types: list, force: bool = False) -> dict:
    """main.py에서 호출하는 진입 함수"""
    total = {}
    if "news" in types:
        total["news"] = generate_news_cards(force=force)
    if "policy" in types:
        total["policy"] = generate_policy_cards(force=force)
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="카드 생성 실행")
    parser.add_argument("--type", choices=["news", "policy", "all"], default="all")
    parser.add_argument("--force", action="store_true", help="강제 재생성")
    args    = parser.parse_args()
    targets = ["news", "policy"] if args.type == "all" else [args.type]
    result  = run_card_generation(targets, force=args.force)
    logger.info("최종 결과: %s", result)