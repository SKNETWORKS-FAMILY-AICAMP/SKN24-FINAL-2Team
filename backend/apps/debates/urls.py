from django.urls import path

from .views import (
    DebateActionView,
    DebateCardInfoView,
    DebateDeleteView,
    DebateDetailView,
    DebateHistoryView,
    DebateInputView,
    DebateMessageCallbackView,
    DebateQuestionView,
    DebateStartView,
    DebateStreamView,
    test_page,
)

urlpatterns = [
    path("test/",                      test_page,                           name="debate-test"),
    path("card-info/<int:card_id>/",   DebateCardInfoView.as_view(),        name="debate-card-info"),
    path("history/",                   DebateHistoryView.as_view(),         name="debate-history"),
    path("",                           DebateStartView.as_view(),           name="debate-start"),
    path("<int:debate_session_id>/",   DebateDetailView.as_view(),          name="debate-detail"),
    path("<int:debate_session_id>/stream/",   DebateStreamView.as_view(),   name="debate-stream"),
    path("<int:debate_session_id>/input/",    DebateInputView.as_view(),    name="debate-input"),
    path("<int:debate_session_id>/action/",   DebateActionView.as_view(),   name="debate-action"),
    path("<int:debate_session_id>/delete/",   DebateDeleteView.as_view(),   name="debate-delete"),
    path("<int:debate_session_id>/messages/", DebateMessageCallbackView.as_view(), name="debate-messages-callback"),
    path("<int:debate_session_id>/question/", DebateQuestionView.as_view(),        name="debate-question"),
]
