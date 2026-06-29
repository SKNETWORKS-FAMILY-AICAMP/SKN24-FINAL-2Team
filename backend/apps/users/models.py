# apps/users/models.py
from django.db import models
# apps/users/serializers.py



class Category(models.Model):
    """CATEGORIES — News / policy topic categories."""

    category_id   = models.AutoField(primary_key=True, db_column="category_id")
    category_name = models.CharField(max_length=50, verbose_name="카테고리 이름")

    class Meta:
        db_table = "CATEGORIES"
        verbose_name = "Category"
        verbose_name_plural = "Categories"

    def __str__(self):
        return self.category_name


class Region(models.Model):
    """REGIONS — Administrative region (시도 / 시군구)."""

    region_id = models.AutoField(primary_key=True, db_column="region_id")
    sido    = models.CharField(max_length=25, verbose_name="광역시도")
    sigungu = models.CharField(max_length=15, verbose_name="시군구")

    class Meta:
        db_table = "REGIONS"
        verbose_name = "Region"
        verbose_name_plural = "Regions"

    def __str__(self):
        return f"{self.sido} {self.sigungu}"


class User(models.Model):
    """USERS — Service accounts (custom auth, not Django's built-in User)."""

    class Gender(models.TextChoices):
        MALE   = "MALE",   "Male"
        FEMALE = "FEMALE", "Female"
        OTHER  = "OTHER",  "Other"

    user_id          = models.AutoField(primary_key=True, db_column="user_id")
    email            = models.EmailField(max_length=255, verbose_name="이메일")
    password         = models.CharField(max_length=255, verbose_name="비밀번호 해시")
    nickname         = models.CharField(max_length=40, verbose_name="닉네임")
    created_at       = models.DateTimeField(auto_now_add=True, verbose_name="계정 생성 일시")
    updated_at       = models.DateTimeField(auto_now=True, verbose_name="계정 수정 일시")
    deleted_at       = models.DateTimeField(null=True, blank=True, verbose_name="회원탈퇴 일시")
    age              = models.PositiveSmallIntegerField(verbose_name="나이")
    gender           = models.CharField(max_length=6, choices=Gender.choices, verbose_name="성별")
    login_fail_count = models.SmallIntegerField(default=0, verbose_name="로그인 실패 횟수")
    region           = models.ForeignKey(
        Region,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        db_column="region_id",
        related_name="users",
        verbose_name="거주지",
    )
    foul_count       = models.SmallIntegerField(default=0, verbose_name="욕설 사용 횟수")

    class Meta:
        db_table = "USERS"
        verbose_name = "User"
        verbose_name_plural = "Users"
        constraints = [
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(deleted_at__isnull=True),
                name="unique_active_email",
            ),
        ]

    def __str__(self):
        return f"{self.nickname} <{self.email}>"


class UserInterest(models.Model):
    """USER_INTERESTS — M:N between users and categories they care about."""

    interest_id = models.AutoField(primary_key=True, db_column="interest_id")
    user     = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        db_column="user_id",
        related_name="interests",
        verbose_name="사용자",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        db_column="category_id",
        related_name="interested_users",
        verbose_name="카테고리",
    )

    class Meta:
        db_table = "USER_INTERESTS"
        verbose_name = "User Interest"
        verbose_name_plural = "User Interests"

    def __str__(self):
        return f"{self.user} → {self.category}"