from django.contrib import admin
from .models import ChatSession, ChatMessage, ChatMsgCard, ChatSummary

admin.site.register(ChatSession)
admin.site.register(ChatMessage)
admin.site.register(ChatMsgCard)
admin.site.register(ChatSummary)