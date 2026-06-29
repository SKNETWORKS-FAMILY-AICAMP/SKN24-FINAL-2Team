from rest_framework import generics, status
from rest_framework.response import Response
from django.db import IntegrityError
from django.shortcuts import get_object_or_404, render
from django.contrib.auth.decorators import login_required

from .models import InfoCard, Bookmark, RawData
from apps.users.models import Category
from .serializers import (
    InfoCardListSerializer,
    InfoCardDetailSerializer,
    BookmarkSerializer,
)


# ── API: 카드 목록 ─────────────────────────────────────────────────────────────

class InfoCardListView(generics.ListAPIView):
    """
    GET /cards/api/
    Query params:
        type        : "news" | "policy"
        category_id : int
    """
    serializer_class = InfoCardListSerializer

    def get_queryset(self):
        qs        = InfoCard.objects.select_related("category").all()
        card_type = self.request.query_params.get("type")
        category  = self.request.query_params.get("category_id")
        if card_type:
            qs = qs.filter(type=card_type)
        if category:
            qs = qs.filter(category_id=category)
        return qs.order_by("-created_at")


# ── API: 카드 상세 ─────────────────────────────────────────────────────────────

class InfoCardDetailView(generics.RetrieveAPIView):
    """
    GET /cards/api/<card_id>/
    """
    queryset         = InfoCard.objects.select_related("category")
    serializer_class = InfoCardDetailSerializer
    lookup_field     = "card_id"


# ── API: 카드 bulk 조회 ────────────────────────────────────────────────────────

class InfoCardBulkListView(generics.ListAPIView):
    """
    GET /cards/api/bulk/?ids=1,2,3
    챗봇에서 여러 카드 ID를 한 번에 조회할 때 사용
    """
    serializer_class = InfoCardListSerializer

    def get_queryset(self):
        ids_param = self.request.query_params.get("ids", "")
        try:
            ids = [int(i) for i in ids_param.split(",") if i.strip()]
        except ValueError:
            ids = []
        return InfoCard.objects.select_related("category").filter(
            card_id__in=ids
        ).order_by("-created_at")


# ── API: 북마크 ───────────────────────────────────────────────────────────────

class BookmarkListCreateView(generics.ListCreateAPIView):
    """
    GET  /cards/api/bookmarks/<user_id>/
    POST /cards/api/bookmarks/<user_id>/
    """
    serializer_class = BookmarkSerializer

    def get_queryset(self):
        return Bookmark.objects.filter(user_id=self.kwargs["user_id"])

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError:
            return Response({"detail": "Bookmark already exists."},
                            status=status.HTTP_409_CONFLICT)


class BookmarkDeleteView(generics.DestroyAPIView):
    """
    DELETE /cards/api/bookmarks/<user_id>/cards/<card_id>/
    """
    queryset     = Bookmark.objects.all()
    lookup_field = "pk"

    def get_object(self):
        return get_object_or_404(
            Bookmark,
            user_id=self.kwargs["user_id"],
            card_id=self.kwargs["card_id"],
        )


# ── 페이지 뷰 ─────────────────────────────────────────────────────────────────

BUS_INFO = {
    'jobs':      {'num': 1, 'name': '일자리',  'color': '#1e5c32', 'img': 'bus_half_1_transparent.png'},
    'housing':   {'num': 2, 'name': '주거',    'color': '#0d3f8a', 'img': 'bus_half_2_transparent.png'},
    'education': {'num': 3, 'name': '교육',    'color': '#94521f', 'img': 'bus_half_3_transparent.png'},
    'culture':   {'num': 4, 'name': '문화',    'color': '#561f63', 'img': 'bus_half_4_transparent.png'},
    'welfare':   {'num': 5, 'name': '생활복지', 'color': '#00635a', 'img': 'bus_half_5_transparent.png'},
    'finance':   {'num': 6, 'name': '금융',    'color': '#937220', 'img': 'bus_half_6_transparent.png'},
}


def card_list_view(request):
    """
    GET /cards/
    Query params:
        type : "news" | "policy"  (기본값: policy)
    """
    card_type  = request.GET.get('type', 'policy')
    category   = request.GET.get('category_id')

    qs = InfoCard.objects.select_related('category').filter(type=card_type)
    if category:
        qs = qs.filter(category_id=category)
    cards = qs.order_by('-created_at')

    categories = Category.objects.all().order_by('category_id')

    context = {
        'card_type':  card_type,
        'cards':      cards,
        'categories': categories,
    }
    return render(request, 'cards/card_list.html', context)


def bus_stop_view(request):
    """
    GET /cards/bus/
    """
    bus_list = [
        {'key': k, 'num': v['num'], 'name': v['name'], 'color': v['color'], 'img': v['img']}
        for k, v in BUS_INFO.items()
    ]
    return render(request, 'cards/bus_stop.html', {'bus_list': bus_list})


def bus_detail_view(request):
    """
    GET /cards/bus/detail/
    Query params:
        category : jobs | housing | education | culture | welfare | finance  (기본값: jobs)
    """
    category_key = request.GET.get('category', 'jobs')
    bus          = BUS_INFO.get(category_key, BUS_INFO['jobs'])

    cards = InfoCard.objects.select_related('category').filter(
        category__category_name=bus['name']
    ).order_by('-created_at')[:3]

    context = {
        'category':      category_key,
        'bus_num':        bus['num'],
        'category_name':  bus['name'],
        'bus_color':      bus['color'],
        'bus_img':        bus['img'],
        'bus_stops': [
            {'num': v['num'], 'name': v['name'], 'color': v['color'], 'category': k}
            for k, v in BUS_INFO.items()
        ],
        'cards': cards,
    }
    return render(request, 'cards/bus_detail.html', context)