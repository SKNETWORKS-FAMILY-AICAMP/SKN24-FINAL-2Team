from rest_framework import serializers
from .models import User, Region, Category, UserInterest
# apps/users/serializers.py
from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth.hashers import check_password
from apps.users.models import User

class CustomTokenObtainSerializer(serializers.Serializer):
    email    = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        try:
            user = User.objects.get(email=attrs['email'])
        except User.DoesNotExist:
            raise serializers.ValidationError("No account found with this email.")

        if not check_password(attrs['password'], user.password):
            raise serializers.ValidationError("Incorrect password.")

        if user.deleted_at is not None:
            raise serializers.ValidationError("This account has been deactivated.")

        refresh = RefreshToken()
        refresh['user_id'] = user.user_id
        refresh['email']   = user.email
        refresh['nickname'] = user.nickname

        return {
            'refresh': str(refresh),
            'access':  str(refresh.access_token),
        }

class RegionSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Region
        fields = "__all__"


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model  = Category
        fields = "__all__"


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model  = User
        fields = "__all__"
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        # Hash password in real usage: make_password(validated_data['password'])
        return super().create(validated_data)


class UserInterestSerializer(serializers.ModelSerializer):
    class Meta:
        model  = UserInterest
        fields = "__all__"
