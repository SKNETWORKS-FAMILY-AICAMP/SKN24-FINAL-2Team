import json
from rest_framework import serializers
from .models import InfoCard, RawData, Bookmark
from django.db import connection


class InfoCardListSerializer(serializers.ModelSerializer):
    """카드 목록 / 챗봇 추천 카드용 — 가벼운 필드만."""

    category_name = serializers.CharField(source="category.category_name", read_only=True)

    class Meta:
        model  = InfoCard
        fields = ["card_id", "category_name", "type", "card_title", "intro"]


class RawDataSerializer(serializers.ModelSerializer):
    """원문 링크."""

    class Meta:
        model  = RawData
        fields = ["data_id", "data_title", "source_url"]


class InfoCardDetailSerializer(serializers.ModelSerializer):
    """카드 상세 — summary/perspectives를 JSON 파싱해서 반환."""

    category_name = serializers.CharField(source="category.category_name", read_only=True)
    summary       = serializers.SerializerMethodField()
    perspectives  = serializers.SerializerMethodField()
    sources       = serializers.SerializerMethodField()
    is_bookmarked = serializers.SerializerMethodField()

    class Meta:
        model  = InfoCard
        fields = [
            "card_id",
            "category_name",
            "type",
            "card_title",
            "intro",
            "summary",       # dict (파싱됨)
            "core_content",  # 순수 텍스트
            "perspectives",  # list (파싱됨)
            "debate_topic",  # 뉴스 카드만 값 있음, 정책 카드는 null
            "created_at",
            "updated_at",
            "sources",
            "is_bookmarked",
        ]

    def _parse_json(self, value):
        """JSON 문자열 → dict/list 변환. 실패 시 원본 문자열 반환."""
        if not value:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    def get_summary(self, obj):
        return self._parse_json(obj.summary)

    def get_perspectives(self, obj):
        return self._parse_json(obj.perspectives)

    def get_sources(self, obj):
        """
        카드 타입(news, policy)에 따라 적절한 테이블을 동적으로 조인하여 출처 리스트를 반환합니다.
        - news: RAW_ARTICLES의 press 컬럼 사용
        - policy: RAW_POLICIES의 department 컬럼을 press로 매핑하여 사용
        """
        urls = self._parse_json(obj.source_urls) or []
        if not urls:
            return []

        placeholders = ', '.join(['%s'] * len(urls))
        
        # 1. 카드 타입에 따라 실행할 SQL 쿼리를 동적으로 분기
        if obj.type == 'news':
            query = f"""
                SELECT d.source_url, d.data_title, a.press
                FROM RAW_DATAS d
                LEFT JOIN RAW_ARTICLES a ON d.data_id = a.data_id
                WHERE d.source_url IN ({placeholders})
            """
        elif obj.type == 'policy':
            # 🌟 수정된 부분: p.department 컬럼을 프론트엔드 규격에 맞춰 AS press로 매핑
            query = f"""
                SELECT d.source_url, d.data_title, p.department AS press
                FROM RAW_DATAS d
                LEFT JOIN RAW_POLICIES p ON d.data_id = p.data_id
                WHERE d.source_url IN ({placeholders})
            """
        else:
            query = f"""
                SELECT d.source_url, d.data_title, '' AS press
                FROM RAW_DATAS d
                WHERE d.source_url IN ({placeholders})
            """

        with connection.cursor() as cur:
            cur.execute(query, urls)
            rows = cur.fetchall()

        # 조회 결과를 딕셔너리로 변환
        url_to_info = {
            row[0]: {"data_title": row[1], "press": row[2] or ""}
            for row in rows
        }

        # 2. 원문 제목이 없을 경우 URL이 제목으로 둔갑하지 않도록 안전하게 빈 문자열("") 반환
        return [
            {
                "title":      url_to_info.get(url, {}).get("data_title") or "",
                "source_url": url,
                "press":      url_to_info.get(url, {}).get("press") or "",
            }
            for url in urls
            if url
        ]

    def get_is_bookmarked(self, obj):
        request = self.context.get('request')
        if not request or not request.user or not request.user.is_authenticated:
            return False
        return Bookmark.objects.filter(user_id=request.user.pk, card_id=obj.card_id).exists()


class BookmarkSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Bookmark
        fields = "__all__"