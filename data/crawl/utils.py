"""
공통 유틸리티
"""
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from functools import wraps
from typing import Callable, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from config import (
    CRAWL_DELAY_MAX,
    CRAWL_DELAY_MIN,
    LOG_DIR,
    MAX_RETRY,
    REQUEST_TIMEOUT,
    RETRY_DELAY,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

FAILURE_LOG_PATH = LOG_DIR / "failures.jsonl"

# ── 중복 판별 (메모리) ─────────────────────────────────────────────────────
_seen_urls: set = set()


def is_duplicate(url: str) -> bool:
    return _normalize_url(url) in _seen_urls


def mark_seen(url: str) -> None:
    _seen_urls.add(_normalize_url(url))


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(fragment="", scheme=parsed.scheme.lower()).geturl()


# ── 재시도 데코레이터 ──────────────────────────────────────────────────────
def with_retry(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(1, MAX_RETRY + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                last_exc = exc
                logger.warning(f"[{func.__name__}] 시도 {attempt}/{MAX_RETRY} 실패: {exc}")
                if attempt < MAX_RETRY:
                    time.sleep(RETRY_DELAY * attempt)
        log_failure(func.__name__, str(last_exc))
        return None
    return wrapper


# ── 실패 로그 ──────────────────────────────────────────────────────────────
def log_failure(target: str, reason: str) -> None:
    FAILURE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "failed_at": datetime.now().isoformat(),
        "target":    target,
        "reason":    reason,
    }
    with FAILURE_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.error(f"[FAILURE] {target}: {reason}")


# ── HTTP 세션 ──────────────────────────────────────────────────────────────
def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent":      USER_AGENT,
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8",
    })
    return session


# ── 크롤링 딜레이 ──────────────────────────────────────────────────────────
def polite_sleep():
    time.sleep(random.uniform(CRAWL_DELAY_MIN, CRAWL_DELAY_MAX))


# ── 본문 추출 ──────────────────────────────────────────────────────────────
def extract_article_body(html: str, url: str = "") -> str:
    """trafilatura → newspaper3k → BeautifulSoup 순서로 시도"""
    try:
        import trafilatura
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        if text and len(text) > 100:
            return text.strip()
    except ImportError:
        pass

    try:
        from newspaper import Article as NewsArticle
        art = NewsArticle(url, language="ko")
        art.set_html(html)
        art.parse()
        if art.text and len(art.text) > 100:
            return art.text.strip()
    except Exception:
        pass

    soup = BeautifulSoup(html, "lxml")
    for selector in [
        "article", "#articleBody", "#article-view-content-div",
        ".article-body", ".news-con", "#newsContent",
        "#cont_newsBodyArea", ".view_txt", ".view_content",
    ]:
        el = soup.select_one(selector)
        if el:
            text = el.get_text(separator="\n", strip=True)
            if len(text) > 100:
                return text
            
    # 본문 영역을 못 찾았으면 body 전체를 저장하지 않음
    return ""


def fetch_article_body(url: str, session: Optional[requests.Session] = None) -> str:
    sess = session or make_session()
    try:
        resp = sess.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        return extract_article_body(resp.text, url)
    except Exception as e:
        logger.warning(f"본문 fetch 실패 [{url}]: {e}")
        return ""


# ── 날짜 파싱 ──────────────────────────────────────────────────────────────
def parse_datetime(date_str: str) -> Optional[datetime]:
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%Y.%m.%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    date_str = date_str.strip()
    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return datetime.now()


def clean_html_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()