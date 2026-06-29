from rest_framework import serializers

from .models import DebateMessage, DebateSession


class DebateMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model  = DebateMessage
        fields = ["debate_msg_id", "role", "content", "message_type", "created_at"]


class DebateSessionSerializer(serializers.ModelSerializer):
    messages = DebateMessageSerializer(many=True, read_only=True)

    class Meta:
        model  = DebateSession
        fields = [
            "debate_session_id", "card_id", "user_id",
            "current_round", "created_at", "updated_at",
            "messages",
        ]


class DebateStartRequestSerializer(serializers.Serializer):
    card_id    = serializers.IntegerField()
    mode       = serializers.ChoiceField(choices=["ai_vs_ai", "ai_vs_user"])
    difficulty = serializers.ChoiceField(choices=["easy", "hard"], default="hard")
    user_stance = serializers.ChoiceField(choices=["pro", "con"], required=False, allow_null=True)

    def validate(self, data):
        if data["mode"] == "ai_vs_user" and not data.get("user_stance"):
            raise serializers.ValidationError("AI vs User 모드는 user_stance(pro/con)가 필요합니다.")
        return data


class UserInputSerializer(serializers.Serializer):
    user_input = serializers.CharField(max_length=500)


class UserActionSerializer(serializers.Serializer):
    ACTION_CHOICES = ["next", "extra", "question"]
    user_action    = serializers.ChoiceField(choices=ACTION_CHOICES)
    question_target = serializers.ChoiceField(choices=["pro", "con"], required=False, allow_null=True)

    def validate(self, data):
        if data["user_action"] == "question" and not data.get("question_target"):
            raise serializers.ValidationError("question 액션은 question_target(pro/con)이 필요합니다.")
        return data


# ai_agent → Django 콜백용 (RDB 저장)
class MessageCallbackSerializer(serializers.Serializer):
    ROLE_CHOICES     = ["USER", "AI"]
    MSG_TYPE_CHOICES = ["PRO", "CON", "QUESTION", "ANSWER"]

    role         = serializers.ChoiceField(choices=ROLE_CHOICES)
    content      = serializers.CharField()
    message_type = serializers.ChoiceField(choices=MSG_TYPE_CHOICES)
