from django.urls import path
from .views import (
    ChatHistoryDetailView,
    ChatHistoryListView,
    ChatMsgCardSelectView,
    ChatStreamView,
    ChatView,
    IsFirstChatView,
    RecommendationsView,
)

urlpatterns = [
    path("recommendations/", RecommendationsView.as_view(), name="chatbot-recommendations"),
    path("chat/",            ChatView.as_view(),             name="chatbot-chat"),
    path("stream/",          ChatStreamView.as_view(),       name="chatbot-stream"),
    path("history/",         ChatHistoryListView.as_view(),  name="chatbot-history-list"),
    path("history/<int:chat_session_id>/", ChatHistoryDetailView.as_view(), name="chatbot-history-detail"),
    path("cards/<int:chat_msg_card_id>/select/", ChatMsgCardSelectView.as_view(), name="chatbot-card-select"),
    path("is-first-chat/", IsFirstChatView.as_view(), name="chatbot-is-first-chat"),
]