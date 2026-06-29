from django.urls import path
from .views import (
    card_list_view,
    bus_stop_view,
    bus_detail_view,
    InfoCardListView,
    InfoCardDetailView,
    InfoCardBulkListView,
    BookmarkListCreateView,
    BookmarkDeleteView,
)

urlpatterns = [
    # ── 페이지 ────────────────────────────────────────────────
    path("",               card_list_view,  name="page-card-list"),   # ?type=news | ?type=policy
    path("bus/",           bus_stop_view,   name="page-bus-stop"),
    path("bus/detail/",    bus_detail_view, name="page-bus-detail"),  # ?category=jobs | housing | ...

    # ── API ───────────────────────────────────────────────────
    path("api/",                                          InfoCardListView.as_view(),       name="card-list"),
    path("api/<int:card_id>/",                            InfoCardDetailView.as_view(),     name="card-detail"),
    path("api/bulk/",                                     InfoCardBulkListView.as_view(),   name="card-bulk"),
    path("api/bookmarks/<int:user_id>/",                  BookmarkListCreateView.as_view(), name="bookmark-list"),
    path("api/bookmarks/<int:user_id>/cards/<int:card_id>/", BookmarkDeleteView.as_view(), name="bookmark-delete"),
]