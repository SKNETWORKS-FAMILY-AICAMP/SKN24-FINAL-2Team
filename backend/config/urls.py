from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from apps.cards.views import bus_detail_view

def page(tpl):
    return TemplateView.as_view(template_name=tpl)

urlpatterns = [
    path("admin/", admin.site.urls),


    # Pages — defined before API includes to avoid prefix conflicts
    path("",                    page("pages/landing.html"),          name="landing"),
    path("login/",              page("pages/login.html"),            name="page-login"),
    path("signup/",             page("pages/signup.html"),           name="page-signup"),
    path("terms/",              page("pages/terms.html"),            name="page-terms"),
    path("privacy/",            page("pages/privacy.html"),          name="page-privacy"),
    path("cards/news/",         page("pages/card_list.html"),        name="page-news-card"),
    path("cards/policy/",       page("pages/card_list.html"),      name="page-policy-card"),
    path("bus/stop/",           page("pages/bus_stop.html"),         name="page-bus-stop"),
    path("bus/detail/",         bus_detail_view,       name="page-bus-detail"),
    path("debate/",             page("pages/debate.html"),           name="page-debate"),
    path("debate/test-female/", page("pages/debate_female.html"),    name="page-debate-female"),
    path("history/",            page("pages/history.html"),          name="page-history"),
    path("mypage/profile/",     page("pages/mypage.html"),           name="page-mypage-profile"),
    path("mypage/bookmark/",    page("pages/mypage.html"),           name="page-mypage-bookmark"),
    path("mypage/withdraw/",    page("pages/mypage.html"),           name="page-mypage-withdraw"),
    path("chat/",              page("chatbot/chatbot.html"),        name="page-chatbot"),
    path("password/find/",  page("pages/password_find.html"),  name="page-password-find"),
    path("password/reset/", page("pages/password_reset.html"), name="page-password-reset"),

    # ── API routes ────────────────────────────────────────────────────────────
    path("cards/",          include("apps.cards.urls")),
    path("api/debates/",    include("apps.debates.urls")),
    path("member/debates/", include("apps.debates.urls")),
    path("member/",         include("apps.users.urls")),
    path("api/chatbot/",   include("apps.chatbot.urls")),
    path("api/token/",         TokenObtainPairView.as_view(), name="token_obtain"),
    path("api/token/refresh/", TokenRefreshView.as_view(),    name="token_refresh"),

    # Django built-in auth (admin login, password reset, etc.)
    path("accounts/", include("django.contrib.auth.urls")),
]
