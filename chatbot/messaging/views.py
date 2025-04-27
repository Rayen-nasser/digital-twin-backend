from rest_framework import viewsets, mixins, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q, F, Window, Max
from django.db.models.functions import RowNumber

from core.models import Message, UserTwinChat, VoiceRecording, MediaFile
from .serializers import MessageSerializer, UserTwinChatSerializer, VoiceRecordingSerializer, MediaFileSerializer
from .permissions import IsChatParticipant
from .pagination import MessagePagination


class UserTwinChatViewSet(viewsets.ModelViewSet):
    """
    Viewset for managing user-twin chat channels
    """
    serializer_class = UserTwinChatSerializer
    permission_classes = [IsAuthenticated]
    filterset_fields = ['twin', 'user_has_access', 'twin_is_active']
    ordering_fields = ['last_active', 'created_at']
    ordering = ['-last_active']

    def get_queryset(self):
        return UserTwinChat.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=['post'])
    def mark_messages_read(self, request, pk=None):
        chat = self.get_object()
        # Mark all messages from twin as read
        unread_count = Message.objects.filter(
            chat=chat,
            is_from_user=False,
            status__in=['sent', 'delivered']
        ).update(status='read', status_updated_at=timezone.now())

        return Response({'status': 'success', 'read_count': unread_count})


from rest_framework import viewsets, mixins, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q, F, Window, Max, Count
from django.db.models.functions import RowNumber

from core.models import Message, UserTwinChat, VoiceRecording, MediaFile, Twin
from .serializers import MessageSerializer, UserTwinChatSerializer, VoiceRecordingSerializer, MediaFileSerializer
from .permissions import IsChatParticipant
from .pagination import MessagePagination


class MessageViewSet(viewsets.ModelViewSet):
    """
    Viewset for messages within a chat
    """
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated, IsChatParticipant]
    pagination_class = MessagePagination
    filterset_fields = ['message_type', 'is_from_user', 'status']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def get_queryset(self):
        chat_id = self.request.query_params.get('chat', None)
        queryset = Message.objects.all()

        if chat_id:
            # Ensure the chat belongs to the requesting user
            chats = UserTwinChat.objects.filter(id=chat_id, user=self.request.user)
            if chats.exists():
                queryset = queryset.filter(chat=chat_id)
            else:
                queryset = Message.objects.none()
        else:
            # Get messages from all chats the user is part of
            user_chats = UserTwinChat.objects.filter(user=self.request.user)
            queryset = queryset.filter(chat__in=user_chats)

        return queryset

    def perform_create(self, serializer):
        # Set is_from_user to True since user is sending
        chat = serializer.validated_data['chat']

        # Ensure the user has access to this chat
        if chat.user != self.request.user:
            return Response(
                {'detail': 'You do not have access to this chat.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Update chat's last_active timestamp
        chat.last_active = timezone.now()
        chat.save(update_fields=['last_active'])

        # Save the user's message
        message = serializer.save(is_from_user=True)

        # Check if this is the first message in the conversation
        message_count = Message.objects.filter(chat=chat).count()
        if message_count == 1:  # This is the first message
            # Get twin's persona data
            twin = chat.twin
            persona_data = twin.persona_data

            # Create twin's response based on persona data
            twin_response = self.generate_twin_response(message, persona_data)

            # Create and save the twin's response message
            Message.objects.create(
                chat=chat,
                is_from_user=False,  # From twin
                message_type='text',
                text_content=twin_response,
                status='sent'
            )

    def generate_twin_response(self, user_message, persona_data):
        """
        Generate twin's first response based on persona data
        In a real implementation, this would likely call an external AI service
        """
        # Extract persona description for greeting
        persona_description = persona_data.get('persona_description', '')

        # Basic initial greeting logic
        greeting = f"Hello! I'm {user_message.chat.twin.name}."

        if persona_description:
            # Add a bit of persona context if available
            greeting += f" {persona_description.split('.')[0]}."  # Just the first sentence

        greeting += " How can I help you today?"

        # In a real implementation, you would:
        # 1. Send the user message and persona data to your AI/NLP service
        # 2. Get back a properly formatted response
        # 3. Return that response

        return greeting

    @action(detail=False, methods=['get'])
    def recent_conversations(self, request):
        """Get most recent message from each conversation"""
        user_chats = UserTwinChat.objects.filter(user=request.user)

        # Use window function to get the most recent message per chat
        recent_messages = Message.objects.filter(
            chat__in=user_chats
        ).annotate(
            row_num=Window(
                expression=RowNumber(),
                partition_by=[F('chat')],
                order_by=F('created_at').desc(),
            )
        ).filter(row_num=1)

        serializer = self.get_serializer(recent_messages, many=True)
        return Response(serializer.data)


class VoiceRecordingViewSet(mixins.CreateModelMixin,
                          mixins.RetrieveModelMixin,
                          mixins.ListModelMixin,
                          viewsets.GenericViewSet):
    """
    API endpoint for voice recordings
    Limited to create, retrieve, and list operations
    """
    serializer_class = VoiceRecordingSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Users can only access voice recordings from their messages
        user_chats = UserTwinChat.objects.filter(user=self.request.user)
        user_messages = Message.objects.filter(chat__in=user_chats)
        return VoiceRecording.objects.filter(
            id__in=user_messages.values_list('voice_note', flat=True)
        )

    def perform_create(self, serializer):
        # Process the voice recording upload
        # Additional processing logic can be added here
        serializer.save()