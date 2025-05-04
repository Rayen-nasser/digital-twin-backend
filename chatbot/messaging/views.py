from django.shortcuts import render
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
        # Optimize query with select_related to prefetch twin data including avatar
        return UserTwinChat.objects.select_related(
            'twin',
            'twin__avatar'
        ).filter(user=self.request.user)

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

        # Start with an optimized base queryset
        queryset = Message.objects.select_related(
            'voice_note',
            'file_attachment',
            'chat',
            'chat__twin',
            'chat__twin__avatar'
        )

        # If requesting a specific chat's messages
        if chat_id:
            # Verify user has access to this chat
            if UserTwinChat.objects.filter(id=chat_id, user=self.request.user).exists():
                return queryset.filter(chat_id=chat_id)
            return Message.objects.none()

        # If requesting messages across all chats (potentially expensive)
        user_chats = UserTwinChat.objects.filter(user=self.request.user)

        # Use more restrictive pagination for multi-chat requests
        if not self.pagination_class or not hasattr(self, 'paginator'):
            # If no pagination is set up, limit results
            return queryset.filter(chat__in=user_chats)[:50]

        return queryset.filter(chat__in=user_chats)

    # Include a new action to get messages for a specific chat with optimization
    @action(detail=False, methods=['get'])
    def chat_history(self, request):
        """
        Get message history for a specific chat with optimized pagination
        """
        chat_id = request.query_params.get('chat', None)
        if not chat_id:
            return Response(
                {"error": "chat parameter is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Verify user has access to this chat
        try:
            chat = UserTwinChat.objects.get(id=chat_id, user=request.user)
        except UserTwinChat.DoesNotExist:
            return Response(
                {"error": "Chat not found or access denied"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Get messages with optimized query
        messages = Message.objects.select_related(
            'voice_note',
            'file_attachment'
        ).filter(chat=chat).order_by('-created_at')

        # Update chat's last_active timestamp when viewing
        chat.last_active = timezone.now()
        chat.save(update_fields=['last_active'])

        # Use paginator if defined
        page = self.paginate_queryset(messages)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(messages, many=True)
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