from rest_framework import serializers
from core.models import Message, MessageReport, Twin, UserTwinChat, VoiceRecording, MediaFile
from twin.serializers import BaseAvatarMixin


class VoiceRecordingSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceRecording
        fields = ['id', 'duration_seconds', 'format', 'sample_rate', 'created_at',
                 'is_processed', 'transcription', 'storage_path']
        read_only_fields = ['id', 'created_at', 'is_processed', 'transcription', 'storage_path']


class MediaFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaFile
        fields = ['id', 'original_name', 'file_category', 'mime_type', 'size_bytes', 'uploaded_at', 'is_public', 'thumbnail_path', 'dimensions']
        read_only_fields = ['id', 'uploaded_at']


class MessageSerializer(serializers.ModelSerializer):
    voice_note_details = VoiceRecordingSerializer(source='voice_note', read_only=True, required=False)
    file_details = MediaFileSerializer(source='file_attachment', read_only=True, required=False)
    reply_details = serializers.SerializerMethodField(read_only=True)

    created_at = serializers.DateTimeField(format='%Y-%m-%dT%H:%M:%S', read_only=True)
    status_updated_at = serializers.DateTimeField(format='%Y-%m-%dT%H:%M:%S', read_only=True)

    class Meta:
        model = Message
        fields = [
            'id', 'chat', 'is_from_user', 'message_type', 'text_content',
            'voice_note', 'voice_note_details', 'file_attachment', 'file_details',
            'created_at', 'status', 'status_updated_at', 'duration_seconds', 'file_preview_url',
            'reply_to', 'reply_details'
        ]
        read_only_fields = ['id', 'created_at', 'status_updated_at']

    def get_reply_details(self, obj):
        """
        Return simplified details about the message being replied to
        """
        if not obj.reply_to:
            return None

        return {
            'id': obj.reply_to.id,
            'text_content': obj.reply_to.text_content[:100] if obj.reply_to.text_content else None,  # First 100 chars
            'message_type': obj.reply_to.message_type,
            'is_from_user': obj.reply_to.is_from_user,
            'created_at': obj.reply_to.created_at.isoformat()
        }

    def validate_reply_to(self, value):
        """
        Validate that the message being replied to exists and is in the same chat
        """
        if value:
            request = self.context.get('request')
            chat_id = request.data.get('chat')

            if chat_id and value.chat_id != chat_id:
                raise serializers.ValidationError("Cannot reply to a message from a different chat")

        return value


class MessageReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageReport
        fields = ['id', 'message', 'reason', 'details', 'created_at']
        read_only_fields = ['id', 'created_at']


class UserTwinChatSerializer(serializers.ModelSerializer, BaseAvatarMixin):
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    twin = serializers.PrimaryKeyRelatedField(
        queryset=Twin.objects.all(),
        required=False,
        write_only=False
    )
    twin_details = serializers.SerializerMethodField(read_only=True)
    is_muted = serializers.SerializerMethodField(read_only=True)

    created_at = serializers.DateTimeField(format='%Y-%m-%dT%H:%M:%S', read_only=True)
    last_active = serializers.DateTimeField(format='%Y-%m-%dT%H:%M:%S', read_only=True)

    class Meta:
        model = UserTwinChat
        fields = [
            'id', 'twin', 'twin_details', 'created_at', 'last_active',
            'user_has_access', 'twin_is_active', 'last_message', 'unread_count', 'is_muted', 'is_archived'
        ]
        read_only_fields = ['id', 'created_at', 'last_active', 'twin_details', 'is_muted']

    def get_twin_details(self, obj):
        """
        Return twin data as a nested object with id, twin_name, and avatar_url
        """
        if not obj.twin:
            return None

        return {
            'id': obj.twin.id,
            'twin_name': obj.twin.name,
            'avatar_url': self.get_avatar_url(obj.twin)
        }

    def get_last_message(self, obj):
        # Use prefetched data if available (set by ViewSet)
        if hasattr(obj, 'prefetched_last_message') and obj.prefetched_last_message:
            last_message = obj.prefetched_last_message[0]
            return {
                'id': last_message.id,
                'text_content': last_message.text_content,
                'message_type': last_message.message_type,
                'created_at': last_message.created_at.isoformat(),
                'is_from_user': last_message.is_from_user,
            }

        # Fallback to database query if prefetch data isn't available
        last_message = obj.messages.order_by('-created_at').first()
        if last_message:
            return {
                'id': last_message.id,
                'text_content': last_message.text_content,
                'message_type': last_message.message_type,
                'created_at': last_message.created_at.isoformat(),
                'is_from_user': last_message.is_from_user,
            }
        return None

    def get_unread_count(self, obj):
        # Check for annotated unread_count (from ViewSet)
        if hasattr(obj, 'unread_count_annotation'):
            return obj.unread_count_annotation or 0

        # Fallback to database query if annotation isn't available
        user = self.context.get('request').user if self.context.get('request') else None
        if user and user.id == obj.user.id:
            return obj.messages.filter(is_from_user=False, status__in=['sent', 'delivered']).count()
        return 0

    def get_is_muted(self, obj):
        """
        Return whether the chat is muted
        """
        try:
            from core.models import ChatSettings

            # Try to get chat settings
            chat_settings = ChatSettings.objects.filter(chat=obj).first()
            if chat_settings:
                return chat_settings.muted
            return False

        except ImportError:
            # If ChatSettings model doesn't exist, return False
            return False