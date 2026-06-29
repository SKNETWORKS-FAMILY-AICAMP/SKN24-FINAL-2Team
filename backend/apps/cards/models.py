from django.db import models
from apps.users.models import User, Category


class RawData(models.Model):
    """RAW_DATAS — 수집된 원본 데이터 (뉴스 기사 / 정책)."""

    data_id      = models.AutoField(primary_key=True)
    category     = models.ForeignKey(
        Category, on_delete=models.PROTECT,
        db_column="category_id", related_name="raw_datas",
    )
    data_title   = models.CharField(max_length=255, verbose_name="제목")
    source_url   = models.CharField(max_length=500, verbose_name="원문 URL")
    collected_at = models.DateTimeField(verbose_name="수집 일시")
    updated_at   = models.DateTimeField(auto_now=True, verbose_name="갱신 일시")

    class Meta:
        db_table = "RAW_DATAS"
        verbose_name = "Raw Data"
        verbose_name_plural = "Raw Datas"

    def __str__(self):
        return self.data_title


class RawArticle(models.Model):
    """RAW_ARTICLES — 뉴스 기사 전용 추가 정보 (RAW_DATAS 1:1 확장)."""

    data     = models.OneToOneField(
        RawData, on_delete=models.CASCADE,
        db_column="data_id", primary_key=True, related_name="article",
    )
    press        = models.CharField(max_length=100, verbose_name="언론사명")
    published_at = models.DateField(verbose_name="기사 발행일")

    class Meta:
        db_table = "RAW_ARTICLES"
        verbose_name = "Raw Article"
        verbose_name_plural = "Raw Articles"

    def __str__(self):
        return f"[{self.press}] {self.data.data_title}"


class RawPolicy(models.Model):
    """RAW_POLICIES — 정책 전용 추가 정보 (RAW_DATAS 1:1 확장)."""

    data         = models.OneToOneField(
        RawData, on_delete=models.CASCADE,
        db_column="data_id", primary_key=True, related_name="policy",
    )
    department   = models.CharField(max_length=100, verbose_name="소관부처")
    apply_period = models.TextField(verbose_name="신청기한")
    policy_law   = models.TextField(null=True, blank=True, verbose_name="근거법령")

    class Meta:
        db_table = "RAW_POLICIES"
        verbose_name = "Raw Policy"
        verbose_name_plural = "Raw Policies"

    def __str__(self):
        return f"[{self.department}] {self.data.data_title}"


class InfoCard(models.Model):
    """INFO_CARDS — AI가 생성한 정보 카드 (뉴스/정책)."""

    TYPE_CHOICES = [("news", "News"), ("policy", "Policy")]

    card_id       = models.AutoField(primary_key=True)
    category      = models.ForeignKey(
        Category, on_delete=models.PROTECT,
        db_column="category_id", related_name="cards",
    )
    type          = models.CharField(max_length=10, choices=TYPE_CHOICES, verbose_name="카드 종류")
    card_title    = models.CharField(max_length=255, verbose_name="제목")
    intro         = models.CharField(max_length=255, verbose_name="한 줄 소개")
    summary       = models.TextField(verbose_name="요약")
    core_content  = models.TextField(verbose_name="핵심 내용")
    perspectives  = models.TextField(verbose_name="다양한 입장")
    debate_topic  = models.TextField(null=True, blank=True, verbose_name="토론 주제 (뉴스 카드 전용)")
    source_urls = models.TextField(default="[]", blank=False, verbose_name="출처 URL 목록")
    created_at    = models.DateTimeField(auto_now_add=True, verbose_name="발행 일시")
    updated_at    = models.DateTimeField(auto_now=True, verbose_name="갱신 일시")

    class Meta:
        db_table = "INFO_CARDS"
        verbose_name = "Info Card"
        verbose_name_plural = "Info Cards"

    def __str__(self):
        return self.card_title


class Bookmark(models.Model):
    """BOOKMARKS — 사용자 북마크."""

    user       = models.ForeignKey(User, on_delete=models.CASCADE, db_column="user_id")
    card       = models.ForeignKey(InfoCard, on_delete=models.CASCADE, db_column="card_id")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="북마크 설정 일시")

    class Meta:
        db_table        = "BOOKMARKS"
        unique_together = [("user", "card")]
        verbose_name = "Bookmark"
        verbose_name_plural = "Bookmarks"

    def __str__(self):
        return f"Bookmark(user={self.user_id}, card={self.card_id})"
