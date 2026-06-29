"""
데이터 모델
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Article:
    """뉴스 기사 단위"""
    title:           str
    content:         str
    publisher:       str
    published_at:    datetime
    url:             str
    source:          str                    # naver / rss / molit 등
    keyword_matched: Optional[str] = None  # 매칭된 검색 쿼리
    category:        Optional[str] = None  # 정책 카테고리 (일자리/주거/금융 등)
    file_path:       Optional[str] = None  # 본문 텍스트 파일 경로

    def __post_init__(self):
        self.title     = self.title.strip()
        self.content   = self.content.strip()
        self.publisher = self.publisher.strip()

    def to_dict(self) -> dict:
        return {
            "category":        self.category,
            "keyword_matched": self.keyword_matched,
            "title":           self.title,
            "content":         self.content,
            "publisher":       self.publisher,
            "published_at":    self.published_at.isoformat(),
            "url":             self.url,
            "source":          self.source,
            "file_path":       self.file_path,
        }