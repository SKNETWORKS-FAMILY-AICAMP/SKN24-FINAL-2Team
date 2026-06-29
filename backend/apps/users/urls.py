from django.urls import path
from . import views

urlpatterns = [

    # ── Regions ───────────────────────────────────────────────────────────────
    path("regions/",                      views.RegionListCreateView.as_view(),      name="region-list"),
    path("regions/<int:region_id>/",      views.RegionDetailView.as_view(),          name="region-detail"),

    # ── Categories ────────────────────────────────────────────────────────────
    path("categories/",                   views.CategoryListCreateView.as_view(),    name="category-list"),
    path("categories/<int:category_id>/", views.CategoryDetailView.as_view(),        name="category-detail"),

    # ── Users ─────────────────────────────────────────────────────────────────
    path("",                              views.UserListCreateView.as_view(),         name="user-list"),
    path("<int:user_id>/",                views.UserDetailView.as_view(),             name="user-detail"),
    path("<int:user_id>/password/",       views.ChangePasswordView.as_view(),         name="user-change-password"),
    path("<int:user_id>/withdraw/",       views.WithdrawView.as_view(),               name="user-withdraw"),

    # ── User Interests ────────────────────────────────────────────────────────
    path("<int:user_id>/interests/",      views.UserInterestListCreateView.as_view(), name="user-interest-list"),
    path("interests/<int:interest_id>/",  views.UserInterestDetailView.as_view(),     name="user-interest-detail"),

    # ── 회원가입 / 이메일 인증
    path("signup/",              views.SignupView.as_view(),          name="signup"),
    path("email/send-code/",     views.SendEmailCodeView.as_view(),   name="email-send-code"),
    path("email/verify-code/",   views.VerifyEmailCodeView.as_view(), name="email-verify-code"),
    path("nickname-check/",      views.NicknameCheckView.as_view(),   name="nickname-check"),  # 닉네임 금칙어 사전 검사
    path("login/", views.LoginView.as_view(), name="login"),
    path("password/send-code/",   views.SendPasswordFindCodeView.as_view(),   name="password-send-code"),
    path("password/verify-code/", views.VerifyPasswordFindCodeView.as_view(),  name="password-verify-code"),
    path("password/reset/",       views.PasswordResetView.as_view(),           name="password-reset"),

    # ── Bookmarks ─────────────────────────────────────────────────────────────
    path("<int:user_id>/bookmarks/",                          views.BookmarkListCreateView.as_view(),  name="bookmark-list"),
    path("<int:user_id>/bookmarks/cards/<int:card_id>/",      views.BookmarkDeleteView.as_view(),       name="bookmark-delete"),

    # ── Chat History ──────────────────────────────────────────────────────────
    path("chat-history/",                       views.ChatHistoryWithTypeView.as_view(),       name="chat-history-with-type"),
    path("chat-history/<int:chat_session_id>/", views.ChatHistoryDetailWithTypeView.as_view(), name="chat-history-detail"),

    # ── Me ────────────────────────────────────────────────────────────────────
    path("me/", views.MeView.as_view(), name="member-me"),
]