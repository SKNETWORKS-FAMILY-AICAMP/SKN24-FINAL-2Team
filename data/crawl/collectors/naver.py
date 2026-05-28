"""
네이버 뉴스 검색 API 수집기
"""
import logging
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    NAVER_CLIENT_ID,
    NAVER_CLIENT_SECRET,
    REQUEST_TIMEOUT,
)
from models import Article
from utils import (
    clean_html_tags,
    fetch_article_body,
    is_duplicate,
    log_failure,
    make_session,
    mark_seen,
    parse_datetime,
    polite_sleep,
    with_retry,
)

logger = logging.getLogger(__name__)

NAVER_API_URL = "https://openapi.naver.com/v1/search/news.json"
DISPLAY_COUNT = 50
SOURCE_NAME   = "naver"


class NaverCollector:

    def __init__(self):
        self.session = make_session()
        self.session.headers.update({
            "X-Naver-Client-Id":     NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        })

    def collect(
        self,
        queries: Optional[List[str]] = None,
        skip_filter: bool = False,
    ) -> List[Article]:
        if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
            logger.error("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 환경변수를 설정하세요.")
            return []

        queries = queries or []
        if not queries:
            logger.error("[Naver] 검색 쿼리가 없습니다.")
            return []
        
        results: List[Article] = []

        for query in queries:
            logger.info(f"[Naver] 검색: {query!r}")
            items = self._search(query)
            if not items:
                logger.info(f"[Naver] {query!r} — 검색 결과 없음")
                continue

            query_count = 0
            for item in items:
                article = self._parse_item(item, query)
                if article:
                    results.append(article)
                    mark_seen(article.url)
                    query_count += 1
                polite_sleep()
            logger.info(f"[Naver] {query!r} — {query_count}건 수집")

        logger.info(f"[Naver] 총 {len(results)}건 수집 완료")
        return results

    @with_retry
    def _search(self, query: str) -> Optional[List[dict]]:
        params = {
            "query":   query,
            "display": DISPLAY_COUNT,
            "start":   1,
            "sort":    "date",
        }
        resp = self.session.get(NAVER_API_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("items", [])

    def _parse_item(self, item: dict, query: str) -> Optional[Article]:
        title        = clean_html_tags(item.get("title", ""))
        original_url = item.get("originallink") or item.get("link", "")

        if not title or not original_url:
            return None

        if is_duplicate(original_url):
            logger.debug(f"[Naver] 중복 건너뜀: {original_url}")
            return None

        pub_date    = parse_datetime(item.get("pubDate", "")) or datetime.now()
        content     = fetch_article_body(original_url, self.session)

        if not content:
            logger.debug(f"[Naver] 본문 추출 실패로 제외: {original_url}")
            return None
        
        publisher   = self._extract_publisher(original_url)

        return Article(
            title=title,
            content=content,
            publisher=publisher,
            published_at=pub_date,
            url=original_url,
            source=SOURCE_NAME,
            keyword_matched=query,
        )

    @staticmethod
    def _extract_publisher(url: str) -> str:
        domain = urlparse(url).netloc
        return domain.replace("www.", "").split(".")[0] or "unknown"