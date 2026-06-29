# apps/chatbot/models.py

from django.db import models
from apps.users.models import User
from apps.cards.models import InfoCard


class ChatSession(models.Model):
    """CHAT_SESSIONS — One conversation thread per user."""

    chat_session_id = models.AutoField(primary_key=True, db_column="chat_session_id")
    user            = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        db_column="user_id",
        related_name="chat_sessions",
        verbose_name="사용자",
    )
    session_title = models.CharField(max_length=255, verbose_name="세션 타이틀")
    created_at    = models.DateTimeField(auto_now_add=True, verbose_name="세션 개설 일시")
    updated_at    = models.DateTimeField(auto_now=True, verbose_name="세션 갱신 일시")
    is_delete     = models.SmallIntegerField(default=0, verbose_name="삭제 여부")  # 0=false, 1=true

    class Meta:
        db_table = "CHAT_SESSIONS"
        verbose_name = "Chat Session"
        verbose_name_plural = "Chat Sessions"

    def __str__(self):
        return f"[{self.user.nickname}] {self.session_title}"


class ChatSummary(models.Model):
    """CHAT_SUMMARIES — Rolling LLM-generated summaries for a session."""

    chat_summary_id = models.AutoField(primary_key=True, db_column="chat_summary_id")
    chat_summary    = models.TextField(verbose_name="대화 요약")
    is_memory       = models.SmallIntegerField(verbose_name="사용 상태 (0=사용중, 1=미사용)")
    created_at      = models.DateTimeField(auto_now_add=True, verbose_name="요약 생성 일시")
    chat_session    = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        db_column="chat_session_id",
        related_name="summaries",
        verbose_name="채팅 세션",
    )

    class Meta:
        db_table = "CHAT_SUMMARIES"
        verbose_name = "Chat Summary"
        verbose_name_plural = "Chat Summaries"

    def __str__(self):
        return f"Summary #{self.pk} for session {self.chat_session_id}"


class ChatMessage(models.Model):
    """CHAT_MESSAGES — Individual Q&A turns within a session."""

    chat_msg_id  = models.AutoField(primary_key=True, db_column="chat_msg_id")
    chat_session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        db_column="chat_session_id",
        related_name="messages",
        verbose_name="채팅 세션",
    )
    input     = models.TextField(verbose_name="유저 입력")
    output    = models.TextField(verbose_name="AI 출력 / 에러 메시지")
    is_memory = models.SmallIntegerField(verbose_name="메모리 사용 여부 (0=사용, 1=미사용)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="메시지 발송 일시")

    class Meta:
        db_table = "CHAT_MESSAGES"
        verbose_name = "Chat Message"
        verbose_name_plural = "Chat Messages"

    def __str__(self):
        return f"Message #{self.pk} in session {self.chat_session_id}"


class ChatMsgCard(models.Model):
    """CHAT_MSG_CARDS — Info cards recommended by the chatbot in a turn."""

    chat_msg_card_id = models.AutoField(primary_key=True, db_column="chat_msg_card_id")
    is_selected      = models.SmallIntegerField(verbose_name="사용자 카드 선택 여부")
    chat_message     = models.ForeignKey(
        ChatMessage,
        on_delete=models.CASCADE,
        db_column="chat_msg_id",
        related_name="recommended_cards",
        verbose_name="채팅 메시지",
    )
    card             = models.ForeignKey(
        InfoCard,
        on_delete=models.CASCADE,
        db_column="card_id",
        related_name="chat_msg_cards",
        verbose_name="정보 카드",
    )

    class Meta:
        db_table = "CHAT_MSG_CARDS"
        verbose_name = "Chat Msg Card"
        verbose_name_plural = "Chat Msg Cards"

    def __str__(self):
        return f"Card {self.card_id} recommended in message {self.chat_message_id}"