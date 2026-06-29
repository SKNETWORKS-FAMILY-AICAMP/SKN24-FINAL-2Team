from django.db import models

from apps.cards.models import InfoCard
from apps.users.models import User


class DebateSession(models.Model):
    ROUND_CHOICES = [
        ("1", "입장 제시"),
        ("2", "찬성 세부주장"),
        ("3", "반대 세부주장"),
        ("4", "주장 다지기"),
    ]

    debate_session_id = models.AutoField(primary_key=True)
    card              = models.ForeignKey(InfoCard, on_delete=models.PROTECT, db_column="card_id")
    user              = models.ForeignKey(User, on_delete=models.PROTECT, db_column="user_id")
    current_round     = models.CharField(max_length=1, choices=ROUND_CHOICES, default="1")
    created_at        = models.DateTimeField(auto_now_add=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "DEBATE_SESSIONS"

    def __str__(self):
        return f"DebateSession #{self.debate_session_id} (user={self.user_id}, card={self.card_id})"


class DebateMessage(models.Model):
    ROLE_CHOICES = [("USER", "User"), ("AI", "AI")]
    MSG_TYPE_CHOICES = [
        ("PRO",      "찬성"),
        ("CON",      "반대"),
        ("QUESTION", "질문"),
        ("ANSWER",   "답변"),
    ]

    debate_msg_id  = models.AutoField(primary_key=True)
    debate_session = models.ForeignKey(
        DebateSession, on_delete=models.CASCADE,
        db_column="debate_session_id", related_name="messages",
    )
    role           = models.CharField(max_length=4, choices=ROLE_CHOICES)
    content        = models.TextField()
    created_at     = models.DateTimeField(auto_now_add=True)
    message_type   = models.CharField(max_length=8, choices=MSG_TYPE_CHOICES)

    class Meta:
        db_table = "DEBATE_MESSAGES"
        ordering = ["created_at"]

    def __str__(self):
        return f"[{self.role}/{self.message_type}] {self.content[:30]}"
