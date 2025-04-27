from rest_framework import serializers
from core.models import Message, UserTwinChat, VoiceRecording, MediaFile


class VoiceRecordingSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoiceRecording
        fields = ['id', 'duration_seconds', 'format', 'sample_rate', 'created_at', 'is_processed', 'transcription']
        read_only_fields = ['id', 'created_at', 'is_processed', 'transcription']


class MediaFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = MediaFile
        fields = ['id', 'original_name', 'file_category', 'mime_type', 'size_bytes', 'uploaded_at', 'is_public', 'thumbnail_path', 'dimensions']
        read_only_fields = ['id', 'uploaded_at']


class MessageSerializer(serializers.ModelSerializer):
    voice_note_details = VoiceRecordingSerializer(source='voice_note', read_only=True, required=False)
    file_details = MediaFileSerializer(source='file_attachment', read_only=True, required=False)

    class Meta:
        model = Message
        fields = [
            'id', 'chat', 'is_from_user', 'message_type', 'text_content',
            'voice_note', 'voice_note_details', 'file_attachment', 'file_details',
            'created_at', 'status', 'status_updated_at', 'duration_seconds', 'file_preview_url'
        ]
        read_only_fields = ['id', 'created_at', 'status_updated_at']


class UserTwinChatSerializer(serializers.ModelSerializer):
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()
    twin_name = serializers.CharField(source='twin.name', read_only=True)
    twin_avatar = serializers.SerializerMethodField()

    class Meta:
        model = UserTwinChat
        fields = [
            'id', 'user', 'twin', 'twin_name', 'twin_avatar',
            'created_at', 'last_active', 'user_has_access',
            'twin_is_active', 'last_message', 'unread_count'
        ]
        read_only_fields = ['id', 'created_at', 'last_active']

    def get_last_message(self, obj):
        last_message = obj.messages.order_by('-created_at').first()
        if last_message:
            return {
                'id': last_message.id,
                'text_content': last_message.text_content,
                'message_type': last_message.message_type,
                'created_at': last_message.created_at,
                'is_from_user': last_message.is_from_user,
            }
        return None

    def get_unread_count(self, obj):
        # Count messages from twin that are not read by the user
        user = self.context.get('request').user if self.context.get('request') else None
        if user and user.id == obj.user.id:
            return obj.messages.filter(is_from_user=False, status__in=['sent', 'delivered']).count()
        return 0

    def get_twin_avatar(self, obj):
        if obj.twin.avatar:
            return {
                'id': obj.twin.avatar.id,
                'thumbnail_path': obj.twin.avatar.thumbnail_path
            }
        return None