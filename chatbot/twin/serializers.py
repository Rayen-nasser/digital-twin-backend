from rest_framework import serializers
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from drf_spectacular.utils import extend_schema_field, OpenApiTypes
from PIL import Image
import os
import uuid
import logging

from core.models import Twin, MediaFile, User
from .constants import (
    MIN_QUESTION_LENGTH,  # Add the new constant here
    MAX_ANSWER_LENGTH,
    MAX_QUESTION_LENGTH,
    PERSONA_DESCRIPTION_MAX_LENGTH,
    MAX_CONVERSATION_EXAMPLES,
    DEFAULT_PERSONA_DATA
)

logger = logging.getLogger(__name__)


class BaseAvatarMixin:
    """Mixin for handling avatar URL generation for Twin serializers."""

    @extend_schema_field(OpenApiTypes.STR)
    def get_avatar_url(self, obj):
        if not obj.avatar:
            return None

        request = self.context.get('request')
        if request:
            domain = request.build_absolute_uri('/').rstrip('/')
            return f"{domain}/media/{obj.avatar.storage_path}"

        base_url = getattr(settings, 'MEDIA_URL_DOMAIN', '')
        return f"{base_url}/media/{obj.avatar.storage_path}"


class PersonaDataValidator:
    """Utility class for validating persona data."""

    @staticmethod
    def validate_persona_description(description):
        if not isinstance(description, str):
            raise serializers.ValidationError("persona_description must be a string.")

        if len(description) > PERSONA_DESCRIPTION_MAX_LENGTH:
            raise serializers.ValidationError(
                f"persona_description cannot exceed {PERSONA_DESCRIPTION_MAX_LENGTH} characters."
            )
        return description

    @staticmethod
    def validate_conversation(conversation, idx):
        if not isinstance(conversation, dict):
            raise serializers.ValidationError(f"Conversations must be an object.")

        if 'question' not in conversation or 'answer' not in conversation:
            raise serializers.ValidationError(
                f"Conversations must have both 'question' and 'answer'."
            )

        # Check if question meets minimum length requirement
        if len(conversation['question']) < MIN_QUESTION_LENGTH:
            raise serializers.ValidationError(
                f"Questions must be at least {MIN_QUESTION_LENGTH} characters."
            )

        if len(conversation['question']) > MAX_QUESTION_LENGTH:
            raise serializers.ValidationError(
                f"Questions cannot exceed {MAX_QUESTION_LENGTH} characters."
            )

        if len(conversation['answer']) > MAX_ANSWER_LENGTH:
            raise serializers.ValidationError(
                f"Answers cannot exceed {MAX_ANSWER_LENGTH} characters."
            )

        return conversation

    @classmethod
    def validate_conversations_list(cls, conversations):
        if not isinstance(conversations, list):
            raise serializers.ValidationError("conversations must be an array.")

        if len(conversations) > MAX_CONVERSATION_EXAMPLES:
            raise serializers.ValidationError(
                f"Cannot have more than {MAX_CONVERSATION_EXAMPLES} conversation examples."
            )

        return [cls.validate_conversation(conv, idx) for idx, conv in enumerate(conversations)]


class TwinListSerializer(BaseAvatarMixin, serializers.ModelSerializer):
    """Serializer for listing twins with minimal information."""

    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        default=serializers.CurrentUserDefault()
    )
    avatar_url = serializers.SerializerMethodField(read_only=True)
    description = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Twin
        fields = [
            'id', 'name', 'owner', 'avatar_url', 'description',
            'privacy_setting', 'created_at', 'updated_at', 'is_active'
        ]
        read_only_fields = ['created_at', 'updated_at', 'avatar_url', 'description']

    @extend_schema_field(OpenApiTypes.STR)
    def get_description(self, obj):
        if isinstance(obj.persona_data, dict):
            return obj.persona_data.get('persona_description')
        return None


class PersonaDataUpdateSerializer(serializers.Serializer):
    """Serializer for updating just the persona data of a twin."""

    persona_description = serializers.CharField(
        max_length=PERSONA_DESCRIPTION_MAX_LENGTH,
        required=False,
        allow_blank=True
    )
    conversations = serializers.ListField(
        child=serializers.DictField(
            child=serializers.CharField(),
            allow_empty=True
        ),
        required=False,
        max_length=MAX_CONVERSATION_EXAMPLES
    )

    def validate_conversations(self, value):
        for idx, conv in enumerate(value):
            if 'question' not in conv or 'answer' not in conv:
                raise serializers.ValidationError(
                    f"Conversation at index {idx} must have both 'question' and 'answer'."
                )

            # Add minimum length validation
            if len(conv.get('question', '')) < MIN_QUESTION_LENGTH:
                raise serializers.ValidationError(
                    f"Questions must be at least {MIN_QUESTION_LENGTH} characters."
                )

            # Maximum length validation
            if len(conv.get('question', '')) > MAX_QUESTION_LENGTH:
                raise serializers.ValidationError(
                    f"Questions cannot exceed {MAX_QUESTION_LENGTH} characters."
                )

            if len(conv.get('answer', '')) > MAX_ANSWER_LENGTH:
                raise serializers.ValidationError(
                    f"Answers cannot exceed {MAX_ANSWER_LENGTH} characters."
                )
        return value


class AvatarHandlerMixin:
    """Mixin for handling avatar image processing and storage."""

    def process_avatar_image(self, avatar_image, user):
        """Process an avatar image and return a MediaFile object."""
        try:
            img = Image.open(avatar_image)
            max_dimension = 500
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension))

            filename = f"{uuid.uuid4()}-{avatar_image.name}"
            temp_path = f"temp_{filename}"
            img.save(temp_path)

            with open(temp_path, 'rb') as f:
                file_path = f"avatars/{filename}"
                saved_path = default_storage.save(file_path, ContentFile(f.read()))
                mime_type = Image.MIME.get(img.format, 'image/jpeg')

                media_file = MediaFile.objects.create(
                    original_name=avatar_image.name,
                    storage_path=saved_path,
                    file_category='image',
                    mime_type=mime_type,
                    size_bytes=os.path.getsize(temp_path),
                    uploader=user,
                )

            os.remove(temp_path)
            return media_file

        except Exception as e:
            logger.error(f"Avatar processing failed: {str(e)}")
            return None

    def cleanup_old_avatar(self, old_avatar):
        """Delete old avatar file and database record."""
        if not old_avatar:
            return

        try:
            default_storage.delete(old_avatar.storage_path)
            old_avatar.delete()
        except Exception as e:
            logger.warning(f"Failed to clean up old avatar: {str(e)}")


class TwinSerializer(BaseAvatarMixin, AvatarHandlerMixin, serializers.ModelSerializer):
    """Main serializer for Twin model with full functionality."""

    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        default=serializers.CurrentUserDefault()
    )
    avatar_url = serializers.SerializerMethodField(read_only=True)
    persona_data = serializers.JSONField(
        required=False,
        initial=DEFAULT_PERSONA_DATA
    )
    avatar_image = serializers.ImageField(write_only=True, required=False)

    class Meta:
        model = Twin
        fields = [
            'id', 'name', 'owner', 'persona_data',
            'avatar', 'privacy_setting',
            'created_at', 'updated_at', 'is_active',
            'avatar_image', 'avatar_url'
        ]
        read_only_fields = ['created_at', 'updated_at', 'avatar']

    def validate_persona_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Persona data must be a JSON object.")

        # Validate or set default persona_description
        if 'persona_description' not in value:
            raise serializers.ValidationError("persona_description is required.")

        value['persona_description'] = PersonaDataValidator.validate_persona_description(
            value['persona_description']
        )

        # Validate or set default conversations
        if 'conversations' not in value:
            value['conversations'] = []
        else:
            value['conversations'] = PersonaDataValidator.validate_conversations_list(
                value['conversations']
            )

        return value

    def create(self, validated_data):
        avatar_image = validated_data.pop('avatar_image', None)
        twin = Twin.objects.create(**validated_data)

        if avatar_image:
            media_file = self.process_avatar_image(
                avatar_image,
                self.context['request'].user
            )
            if media_file:
                twin.avatar = media_file
                twin.save()

        return twin

    def update(self, instance, validated_data):
        avatar_image = validated_data.pop('avatar_image', None)

        if avatar_image:
            media_file = self.process_avatar_image(
                avatar_image,
                self.context['request'].user
            )

            if media_file:
                old_avatar = instance.avatar
                instance.avatar = media_file
                self.cleanup_old_avatar(old_avatar)

        return super().update(instance, validated_data)

    def to_internal_value(self, data):
        if 'persona_data' not in data:
            data = data.copy()  # Make a mutable copy
            data['persona_data'] = DEFAULT_PERSONA_DATA
        return super().to_internal_value(data)