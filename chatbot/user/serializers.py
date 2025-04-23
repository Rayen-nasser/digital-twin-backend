# user/serializers.py
from rest_framework import serializers
from core.models import User, AuthToken
from django.contrib.auth.password_validation import validate_password
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
import os

class UserSerializer(serializers.ModelSerializer):
    """Serializer for the user object."""
    profile_image = serializers.SerializerMethodField()

    def get_profile_image(self, obj):
        request = self.context.get('request')
        if obj.profile_image and hasattr(obj.profile_image, 'url'):
            return request.build_absolute_uri(obj.profile_image.url)
        return None

    class Meta:
        model = User
        fields = ['id', 'email', 'username', 'password', 'is_verified', 'created_at', 'profile_image']
        extra_kwargs = {
            'password': {'write_only': True, 'min_length': 8},
            'id': {'read_only': True},
            'created_at': {'read_only': True},
            'is_verified': {'read_only': True}
        }

    def create(self, validated_data):
        """Create and return a user with encrypted password."""
        return User.objects.create_user(**validated_data)

    def update(self, instance, validated_data):
        """Update and return user."""
        password = validated_data.pop('password', None)
        profile_image = validated_data.pop('profile_image', None)

        if profile_image is not None:
            # Delete old image if it exists
            if instance.profile_image and hasattr(instance.profile_image, 'path'):
                if os.path.isfile(instance.profile_image.path):
                    os.remove(instance.profile_image.path)
            instance.profile_image = profile_image

        user = super().update(instance, validated_data)

        if password:
            user.set_password(password)
            user.save()

        return user


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Custom token serializer to include user details."""

    def validate(self, attrs):
        data = super().validate(attrs)
        request = self.context.get('request')

        profile_image_url = None
        if self.user.profile_image and hasattr(self.user.profile_image, 'url'):
            profile_image_url = request.build_absolute_uri(self.user.profile_image.url)

        data.update({
            'user': {
                'id': str(self.user.id),
                'username': self.user.username,
                'email': self.user.email,
                'is_verified': self.user.is_verified,
                'profile_image': profile_image_url
            }
        })

        return data



class ChangePasswordSerializer(serializers.Serializer):
    """Serializer for password change endpoint."""
    old_password = serializers.CharField(required=True)
    new_password = serializers.CharField(required=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value


class EmailVerificationSerializer(serializers.Serializer):
    """Serializer for email verification."""
    token = serializers.UUIDField()


class PasswordResetRequestSerializer(serializers.Serializer):
    """Serializer for password reset request."""
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    """Serializer for password reset confirmation."""
    token = serializers.CharField()
    new_password = serializers.CharField(min_length=8, write_only=True)

    def validate_new_password(self, value):
        validate_password(value)
        return value

class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        required=True,
        help_text="The refresh token to blacklist during logout."
    )

class ResendVerificationSerializer(serializers.Serializer):
    """Serializer for resending email verification."""
    email = serializers.EmailField(
        required=True,
        help_text="The email address to send a new verification link to."
    )