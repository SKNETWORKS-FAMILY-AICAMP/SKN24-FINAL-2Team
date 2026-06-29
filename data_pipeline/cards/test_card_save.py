"""
test_card_save.py
카드 생성 API 호출 결과를 RDS 저장 없이 로컬 JSON으로만 저장하는 테스트용 스크립트.
Fargate에 올리지 않고 로컬에서만 사용.

실행:
  python test_card_save.py --type news
  python test_card_save.py --type policy
  python test_card_save.py --type all
"""

import json
import logging
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

from config import (
    LOG_DIR,
    DATA_DIR,
    POLICY_PROCESSED_DIR,
    LAWS_PROCESSED_DIR,
)

NEWS_CLEAN_DIR            = DATA_DIR / "news" / "preprocessed"
POLICY_NEWS_PROCESSED_DIR = DATA_DIR / "policy" / "related_news" / "processed"

AI_AGENT_URL     = os.getenv("AI_AGENT_URL", "http://localhost:8000")
CARD_GEN_TIMEOUT = int(os.getenv("CARD_GEN_TIMEOUT", "600"))

# 로컬 저장 디렉토리
OUTPUT_DIR = _ROOT / "test_output" / "cards"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

NEWS_PRO_MIN     = 4
NEWS_CON_MIN     = 4
NEWS_NEUTRAL_MIN = 2
POLICY_MAX_ARTICLES = 5

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("test_card_save")


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


def _latest_file(directory: Path, pattern: str):
    files = sorted(directory.glob(pattern), reverse=True)
    return files[0] if files else None


def _post(endpoint: str, payload: dict):
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


def _save_local(card: dict, card_type: str, name: str) -> Path:
    """카드 결과를 test_output/cards/ 에 JSON으로 저장"""
    date_str  = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = name.replace(" ", "_").replace("/", "_")[:40]
    filename  = f"{card_type}_{safe_name}_{date_str}.json"
    out_path  = OUTPUT_DIR / filename

    out_path.write_text(
        json.dumps(card, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("[저장] %s", out_path)
    return out_path


# ══════════════════════════════════════════════════════════════════════════
# 뉴스 카드 테스트
# ══════════════════════════════════════════════════════════════════════════

def _to_article_schema(art: dict) -> dict:
    return {
        "title":     art.get("title", ""),
        "content":   art.get("content", ""),
        "url":       art.get("url", ""),
        "publisher": art.get("publisher", art.get("press", "")),
        "press":     art.get("press", art.get("publisher", "")),
    }


def _select_articles(articles: list) -> list:
    sorted_arts = sorted(articles, key=lambda a: a.get("published_at", ""), reverse=True)
    pro     = [a for a in sorted_arts if a.get("stance") == "pro"][:NEWS_PRO_MIN]
    con     = [a for a in sorted_arts if a.get("stance") == "con"][:NEWS_CON_MIN]
    neutral = [a for a in sorted_arts if a.get("stance") == "neutral"][:NEWS_NEUTRAL_MIN]
    return pro + con + neutral


def test_news_cards() -> dict:
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
                results["스킵"] += 1
                continue
        except Exception as e:
            logger.warning("[News] 파일 읽기 실패 %s: %s", f.name, e)
            results["스킵"] += 1
            continue

        keyword  = articles[0].get("keyword_matched", f.stem) if articles else f.stem
        selected = _select_articles(articles)

        pro_cnt     = sum(1 for a in selected if a.get("stance") == "pro")
        con_cnt     = sum(1 for a in selected if a.get("stance") == "con")
        neutral_cnt = sum(1 for a in selected if a.get("stance") == "neutral")

        # 찬성 4 / 반대 4 / 중립 2 미달 스킵
        if pro_cnt < NEWS_PRO_MIN or con_cnt < NEWS_CON_MIN or neutral_cnt < NEWS_NEUTRAL_MIN:
            logger.info("[News] '%s' 찬성 %d / 반대 %d / 중립 %d → 기준 미달(찬성%d/반대%d/중립%d), 스킵",
                        keyword, pro_cnt, con_cnt, neutral_cnt,
                        NEWS_PRO_MIN, NEWS_CON_MIN, NEWS_NEUTRAL_MIN)
            results["스킵"] += 1
            continue

        logger.info("[News] '%s' → 찬성 %d / 반대 %d / 중립 %d으로 카드 생성 요청",
                    keyword, pro_cnt, con_cnt, neutral_cnt)

        payload = {"articles": [_to_article_schema(a) for a in selected]}
        card    = _post("/cards/generate/news", payload)

        if not card:
            results["실패"] += 1
            continue

        _save_local(card, "NEWS", keyword)
        results["성공"] += 1

    logger.info("[News] 완료: %s", results)
    return results


# ══════════════════════════════════════════════════════════════════════════
# 정책 카드 테스트
# ══════════════════════════════════════════════════════════════════════════

def _load_laws_index() -> dict:
    index    = defaultdict(list)
    law_path = _latest_file(LAWS_PROCESSED_DIR, "law_grouped_clean_*.json")
    if not law_path:
        logger.warning("[Law] law_grouped_clean_*.json 없음")
        return index
    for law in _read_json(law_path):
        for related in law.get("관련정책", []):
            sid = related.get("서비스ID", "").strip()
            if sid:
                index[sid].append(law)
    return index


def _load_policy_news_index() -> dict:
    index = defaultdict(list)
    if not POLICY_NEWS_PROCESSED_DIR.exists():
        return index
    date_dirs    = sorted([p for p in POLICY_NEWS_PROCESSED_DIR.iterdir() if p.is_dir()], reverse=True)
    cleaned_path = None
    for d in date_dirs:
        candidate = d / "cleaned_news.jsonl"
        if candidate.exists():
            cleaned_path = candidate
            break
    if not cleaned_path:
        cleaned_path = _latest_file(POLICY_NEWS_PROCESSED_DIR, "cleaned_news_*.jsonl")
    if not cleaned_path:
        return index
    for art in _read_jsonl(cleaned_path):
        kw = art.get("keyword_matched", "").strip()
        if kw:
            index[kw].append(art)
    return index


def _to_policy_source_schema(policy: dict) -> dict:
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
    return {
        "title":     art.get("title", ""),
        "content":   art.get("content", ""),
        "url":       art.get("url", ""),
        "publisher": art.get("publisher", ""),
        "press":     art.get("publisher", ""),
    }


def test_policy_cards() -> dict:
    results = {"성공": 0, "실패": 0, "스킵": 0}

    policy_path = _latest_file(POLICY_PROCESSED_DIR, "gov24_top5_clean_*.json")
    if not policy_path:
        logger.warning("[Policy] gov24_top5_clean_*.json 없음")
        return results

    policies   = _read_json(policy_path)
    laws_index = _load_laws_index()
    news_index = _load_policy_news_index()

    logger.info("[Policy] 처리 대상 %d건", len(policies))

    for policy in policies:
        title      = policy.get("title", "").strip()
        service_id = policy.get("service_id", "").strip()

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

        _save_local(card, "POLICY", title)
        results["성공"] += 1

    logger.info("[Policy] 완료: %s", results)
    return results


# ══════════════════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="카드 생성 결과 로컬 JSON 저장 테스트")
    parser.add_argument("--type", choices=["news", "policy", "all"], default="all")
    args = parser.parse_args()

    total = {}
    if args.type in ("news", "all"):
        total["news"] = test_news_cards()
    if args.type in ("policy", "all"):
        total["policy"] = test_policy_cards()

    logger.info("최종 결과: %s", total)
    logger.info("저장 위치: %s", OUTPUT_DIR)