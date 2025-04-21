# serializers.py - Update TwinSerializer for avatar image upload
from rest_framework import serializers
from core.models import Twin, MediaFile, User

from .constants import MAX_ANSWER_LENGTH, MAX_QUESTION_LENGTH, PERSONA_DESCRIPTION_MAX_LENGTH, MAX_CONVERSATION_EXAMPLES, DEFAULT_PERSONA_DATA
import os
from PIL import Image
import uuid
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from drf_spectacular.utils import extend_schema_field, OpenApiTypes
import logging

logger = logging.getLogger(__name__)

class TwinListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list views without persona_data
    """
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        default=serializers.CurrentUserDefault()
    )


    class Meta:
        model = Twin# serializers.py - Update TwinSerializer to include direct avatar_url
from rest_framework import serializers
from core.models import Twin, MediaFile, User

from .constants import MAX_ANSWER_LENGTH, MAX_QUESTION_LENGTH, PERSONA_DESCRIPTION_MAX_LENGTH, MAX_CONVERSATION_EXAMPLES, DEFAULT_PERSONA_DATA
import os
from PIL import Image
import uuid
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from drf_spectacular.utils import extend_schema_field, OpenApiTypes
import logging

logger = logging.getLogger(__name__)

class TwinListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list views without persona_data
    """
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        default=serializers.CurrentUserDefault()
    )

    avatar_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Twin
        fields = [
            'id', 'name', 'owner', 'avatar_url',
            'privacy_setting', 'created_at', 'updated_at',
            'is_active'
        ]
        read_only_fields = ['created_at', 'updated_at', 'avatar_url']

    @extend_schema_field(OpenApiTypes.STR)
    def get_avatar_url(self, obj):
        if obj.avatar:
            from django.conf import settings
            request = self.context.get('request')
            if request:
                # Use the request's domain to build absolute URL
                domain = request.build_absolute_uri('/').rstrip('/')
                return f"{domain}/media/{obj.avatar.path}"
            else:
                # Fallback to settings.MEDIA_URL if available, or use relative path
                base_url = getattr(settings, 'MEDIA_URL_DOMAIN', '')
                return f"{base_url}/media/{obj.avatar.path}"
        return None


class TwinSerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        default=serializers.CurrentUserDefault()
    )

    avatar_url = serializers.SerializerMethodField(read_only=True)
    persona_data = serializers.JSONField(
        required=False,
        initial=DEFAULT_PERSONA_DATA
    )
    # Add a write-only field for handling avatar uploads in serializer
    avatar_image = serializers.ImageField(write_only=True, required=False)

    class Meta:
        model = Twin
        fields = [
            'id', 'name', 'owner', 'persona_data',
            'avatar', 'avatar_url', 'privacy_setting',
            'created_at', 'updated_at', 'is_active', 'avatar_image'
        ]
        read_only_fields = ['created_at', 'updated_at', 'avatar_url', 'avatar']

    @extend_schema_field(OpenApiTypes.STR)
    def get_avatar_url(self, obj):
        if obj.avatar:
            return f"/media/{obj.avatar.path}"
        return None

    def validate_persona_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Persona data must be a JSON object.")

        # Validate persona_description
        if 'persona_description' not in value:
            raise serializers.ValidationError("persona_description is required.")

        if not isinstance(value['persona_description'], str):
            raise serializers.ValidationError("persona_description must be a string.")

        if len(value['persona_description']) > PERSONA_DESCRIPTION_MAX_LENGTH:
            raise serializers.ValidationError(
                f"persona_description cannot exceed {PERSONA_DESCRIPTION_MAX_LENGTH} characters."
            )

        # Validate conversations
        if 'conversations' not in value:
            value['conversations'] = []
        elif not isinstance(value['conversations'], list):
            raise serializers.ValidationError("conversations must be an array.")

        if len(value['conversations']) > MAX_CONVERSATION_EXAMPLES:
            raise serializers.ValidationError(
                f"Cannot have more than {MAX_CONVERSATION_EXAMPLES} conversation examples."
            )

        # Validate each conversation example
        for idx, conv in enumerate(value['conversations']):
            if not isinstance(conv, dict):
                raise serializers.ValidationError(
                    f"Conversation at index {idx} must be an object."
                )

            if 'question' not in conv or 'answer' not in conv:
                raise serializers.ValidationError(
                    f"Conversation at index {idx} must have both 'question' and 'answer'."
                )

            if len(conv['question']) > MAX_QUESTION_LENGTH:
                raise serializers.ValidationError(
                    f"Question at index {idx} cannot exceed {MAX_QUESTION_LENGTH} characters."
                )

            if len(conv['answer']) > MAX_ANSWER_LENGTH:
                raise serializers.ValidationError(
                    f"Answer at index {idx} cannot exceed {MAX_ANSWER_LENGTH} characters."
                )

        return value

    def create(self, validated_data):
        """Handle avatar_image upload during twin creation"""
        # Extract avatar_image from validated_data if present
        avatar_image = validated_data.pop('avatar_image', None)

        # First create the twin without the avatar
        twin = Twin.objects.create(**validated_data)

        # Process avatar image if provided
        if avatar_image:
            try:
                # Process image with Pillow
                img = Image.open(avatar_image)

                # Resize if needed
                max_dimension = 500
                if img.width > max_dimension or img.height > max_dimension:
                    img.thumbnail((max_dimension, max_dimension))

                # Generate a unique filename
                filename = f"{uuid.uuid4()}-{avatar_image.name}"
                temp_path = f"temp_{filename}"
                img.save(temp_path)

                # Save to storage
                with open(temp_path, 'rb') as f:
                    file_path = f"avatars/{filename}"
                    path = default_storage.save(file_path, ContentFile(f.read()))
                    size_mb = os.path.getsize(temp_path) / (1024 * 1024)

                    # Create MediaFile record
                    media_file = MediaFile.objects.create(
                        filename=filename,
                        file_type='image',
                        uploaded_by=self.context['request'].user,
                        size_mb=size_mb,
                        path=path,
                        is_public=True
                    )

                os.remove(temp_path)

                # Assign the newly created MediaFile as twin's avatar
                twin.avatar = media_file
                twin.save()

            except Exception as e:
                logger.error(f"Avatar creation failed during twin creation: {str(e)}")
                # The twin is still created even if avatar upload fails

        return twin

    def update(self, instance, validated_data):
        """Handle avatar replacement during update operations"""
        # Handle avatar_image if provided in update
        avatar_image = validated_data.pop('avatar_image', None)

        if avatar_image:
            # Process image - similar to upload_avatar
            try:
                img = Image.open(avatar_image)

                # Resize if needed
                max_dimension = 500
                if img.width > max_dimension or img.height > max_dimension:
                    img.thumbnail((max_dimension, max_dimension))

                # Generate filename and save
                filename = f"{uuid.uuid4()}-{avatar_image.name}"
                temp_path = f"temp_{filename}"
                img.save(temp_path)

                with open(temp_path, 'rb') as f:
                    file_path = f"avatars/{filename}"
                    path = default_storage.save(file_path, ContentFile(f.read()))
                    size_mb = os.path.getsize(temp_path) / (1024 * 1024)

                    # Create new MediaFile
                    media_file = MediaFile.objects.create(
                        filename=filename,
                        file_type='image',
                        uploaded_by=self.context['request'].user,
                        size_mb=size_mb,
                        path=path,
                        is_public=True
                    )

                os.remove(temp_path)

                # Handle old avatar cleanup
                old_avatar = instance.avatar
                instance.avatar = media_file

                if old_avatar:
                    try:
                        default_storage.delete(old_avatar.path)
                        old_avatar.delete()
                    except Exception as e:
                        # Log but continue
                        logger.warning(f"Failed to clean up old avatar: {str(e)}")

            except Exception as e:
                # Log the error but continue with the update
                logger.error(f"Avatar update failed: {str(e)}")

        # Update other fields
        return super().update(instance, validated_data)

    def to_internal_value(self, data):
        # Ensure persona_data is always present with at least empty defaults
        if 'persona_data' not in data:
            data = data.copy()
            data['persona_data'] = DEFAULT_PERSONA_DATA
        return super().to_internal_value(data)

class PersonaDataUpdateSerializer(serializers.Serializer):
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

            # Add length validation
            if len(conv.get('question', '')) > MAX_QUESTION_LENGTH:
                raise serializers.ValidationError(
                    f"Question at index {idx} cannot exceed {MAX_QUESTION_LENGTH} characters."
                )

            if len(conv.get('answer', '')) > MAX_ANSWER_LENGTH:
                raise serializers.ValidationError(
                    f"Answer at index {idx} cannot exceed {MAX_ANSWER_LENGTH} characters."
                )
        return value
        fields = [
            'id', 'name', 'owner', 'avatar',
            'privacy_setting', 'created_at', 'updated_at',
            'is_active'
        ]
        read_only_fields = ['created_at', 'updated_at']


class TwinSerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        default=serializers.CurrentUserDefault()
    )

    avatar_details = serializers.SerializerMethodField(read_only=True)
    persona_data = serializers.JSONField(
        required=False,
        initial=DEFAULT_PERSONA_DATA
    )
    # Add a write-only field for handling avatar uploads in serializer
    avatar_image = serializers.ImageField(write_only=True, required=False)

    class Meta:
        model = Twin
        fields = [
            'id', 'name', 'owner', 'persona_data',
            'avatar', 'avatar_details', 'privacy_setting',
            'created_at', 'updated_at', 'is_active', 'avatar_image'
        ]
        read_only_fields = ['created_at', 'updated_at', 'avatar_details', 'avatar']

    def get_avatar_details(self, obj):
        if obj.avatar:
            return {
                'id': str(obj.avatar.id),
                'filename': obj.avatar.filename,
                'file_type': obj.avatar.file_type,
                'url': f"/media/{obj.avatar.path}"  # Add URL for frontend use
            }
        return None

    def validate_persona_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Persona data must be a JSON object.")

        # Validate persona_description
        if 'persona_description' not in value:
            raise serializers.ValidationError("persona_description is required.")

        if not isinstance(value['persona_description'], str):
            raise serializers.ValidationError("persona_description must be a string.")

        if len(value['persona_description']) > PERSONA_DESCRIPTION_MAX_LENGTH:
            raise serializers.ValidationError(
                f"persona_description cannot exceed {PERSONA_DESCRIPTION_MAX_LENGTH} characters."
            )

        # Validate conversations
        if 'conversations' not in value:
            value['conversations'] = []
        elif not isinstance(value['conversations'], list):
            raise serializers.ValidationError("conversations must be an array.")

        if len(value['conversations']) > MAX_CONVERSATION_EXAMPLES:
            raise serializers.ValidationError(
                f"Cannot have more than {MAX_CONVERSATION_EXAMPLES} conversation examples."
            )

        # Validate each conversation example
        for idx, conv in enumerate(value['conversations']):
            if not isinstance(conv, dict):
                raise serializers.ValidationError(
                    f"Conversation at index {idx} must be an object."
                )

            if 'question' not in conv or 'answer' not in conv:
                raise serializers.ValidationError(
                    f"Conversation at index {idx} must have both 'question' and 'answer'."
                )

            if len(conv['question']) > MAX_QUESTION_LENGTH:
                raise serializers.ValidationError(
                    f"Question at index {idx} cannot exceed {MAX_QUESTION_LENGTH} characters."
                )

            if len(conv['answer']) > MAX_ANSWER_LENGTH:
                raise serializers.ValidationError(
                    f"Answer at index {idx} cannot exceed {MAX_ANSWER_LENGTH} characters."
                )

        return value

    def create(self, validated_data):
        """Handle avatar_image upload during twin creation"""
        # Extract avatar_image from validated_data if present
        avatar_image = validated_data.pop('avatar_image', None)

        # First create the twin without the avatar
        twin = Twin.objects.create(**validated_data)

        # Process avatar image if provided
        if avatar_image:
            try:
                # Process image with Pillow
                img = Image.open(avatar_image)

                # Resize if needed
                max_dimension = 500
                if img.width > max_dimension or img.height > max_dimension:
                    img.thumbnail((max_dimension, max_dimension))

                # Generate a unique filename
                filename = f"{uuid.uuid4()}-{avatar_image.name}"
                temp_path = f"temp_{filename}"
                img.save(temp_path)

                # Save to storage
                with open(temp_path, 'rb') as f:
                    file_path = f"avatars/{filename}"
                    path = default_storage.save(file_path, ContentFile(f.read()))
                    size_mb = os.path.getsize(temp_path) / (1024 * 1024)

                    # Create MediaFile record
                    media_file = MediaFile.objects.create(
                        filename=filename,
                        file_type='image',
                        uploaded_by=self.context['request'].user,
                        size_mb=size_mb,
                        path=path,
                        is_public=True
                    )

                os.remove(temp_path)

                # Assign the newly created MediaFile as twin's avatar
                twin.avatar = media_file
                twin.save()

            except Exception as e:
                logger.error(f"Avatar creation failed during twin creation: {str(e)}")
                # The twin is still created even if avatar upload fails

        return twin

    def update(self, instance, validated_data):
        """Handle avatar replacement during update operations"""
        # Handle avatar_image if provided in update
        avatar_image = validated_data.pop('avatar_image', None)

        if avatar_image:
            # Process image - similar to upload_avatar
            try:
                img = Image.open(avatar_image)

                # Resize if needed
                max_dimension = 500
                if img.width > max_dimension or img.height > max_dimension:
                    img.thumbnail((max_dimension, max_dimension))

                # Generate filename and save
                filename = f"{uuid.uuid4()}-{avatar_image.name}"
                temp_path = f"temp_{filename}"
                img.save(temp_path)

                with open(temp_path, 'rb') as f:
                    file_path = f"avatars/{filename}"
                    path = default_storage.save(file_path, ContentFile(f.read()))
                    size_mb = os.path.getsize(temp_path) / (1024 * 1024)

                    # Create new MediaFile
                    media_file = MediaFile.objects.create(
                        filename=filename,
                        file_type='image',
                        uploaded_by=self.context['request'].user,
                        size_mb=size_mb,
                        path=path,
                        is_public=True
                    )

                os.remove(temp_path)

                # Handle old avatar cleanup
                old_avatar = instance.avatar
                instance.avatar = media_file

                if old_avatar:
                    try:
                        default_storage.delete(old_avatar.path)
                        old_avatar.delete()
                    except Exception as e:
                        # Log but continue
                        logger.warning(f"Failed to clean up old avatar: {str(e)}")

            except Exception as e:
                # Log the error but continue with the update
                logger.error(f"Avatar update failed: {str(e)}")

        # Update other fields
        return super().update(instance, validated_data)

    def to_internal_value(self, data):
        # Ensure persona_data is always present with at least empty defaults
        if 'persona_data' not in data:
            data = data.copy()
            data['persona_data'] = DEFAULT_PERSONA_DATA
        return super().to_internal_value(data)

class PersonaDataUpdateSerializer(serializers.Serializer):
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

            # Add length validation
            if len(conv.get('question', '')) > MAX_QUESTION_LENGTH:
                raise serializers.ValidationError(
                    f"Question at index {idx} cannot exceed {MAX_QUESTION_LENGTH} characters."
                )

            if len(conv.get('answer', '')) > MAX_ANSWER_LENGTH:
                raise serializers.ValidationError(
                    f"Answer at index {idx} cannot exceed {MAX_ANSWER_LENGTH} characters."
                )
        return value