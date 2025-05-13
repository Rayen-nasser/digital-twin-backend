from rest_framework import serializers
from core.models import Message, Twin, UserTwinChat, VoiceRecording, MediaFile
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

    created_at = serializers.DateTimeField(format='%Y-%m-%dT%H:%M:%S', read_only=True)
    status_updated_at = serializers.DateTimeField(format='%Y-%m-%dT%H:%M:%S', read_only=True)

    class Meta:
        model = Message
        fields = [
            'id', 'chat', 'is_from_user', 'message_type', 'text_content',
            'voice_note', 'voice_note_details', 'file_attachment', 'file_details',
            'created_at', 'status', 'status_updated_at', 'duration_seconds', 'file_preview_url'
        ]
        read_only_fields = ['id', 'created_at', 'status_updated_at']


class UserTwinChatSerializer(serializers.ModelSerializer, BaseAvatarMixin):
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    twin = serializers.PrimaryKeyRelatedField(
        queryset=Twin.objects.all(),
        required=False,
        write_only=False
    )
    twin_details = serializers.SerializerMethodField(read_only=True)

    created_at = serializers.DateTimeField(format='%Y-%m-%dT%H:%M:%S', read_only=True)
    last_active = serializers.DateTimeField(format='%Y-%m-%dT%H:%M:%S', read_only=True)

    class Meta:
        model = UserTwinChat
        fields = [
            'id', 'twin', 'twin_details', 'created_at', 'last_active',
            'user_has_access', 'twin_is_active', 'last_message', 'unread_count'
        ]
        read_only_fields = ['id', 'created_at', 'last_active', 'twin_details']

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