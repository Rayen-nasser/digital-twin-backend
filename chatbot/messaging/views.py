from django.shortcuts import render
from django.core.exceptions import PermissionDenied
from jsonschema import ValidationError
from rest_framework import viewsets, mixins, status
from rest_framework.exceptions import NotFound
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone

from core.models import Message, Twin, TwinAccess, UserTwinChat, VoiceRecording
from .serializers import MessageSerializer, UserTwinChatSerializer, VoiceRecordingSerializer, MediaFileSerializer
from .permissions import IsChatParticipant
from .pagination import MessagePagination
from drf_spectacular.utils import extend_schema, extend_schema_view


@extend_schema_view(
    list=extend_schema(
        summary="List user's chat channels",
        description="Returns a list of all chat channels the authenticated user has access to"
    ),
    create=extend_schema(
        summary="Create new chat channel",
        description="Creates a new chat channel between the user and a twin. Requires proper twin access.",
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'twin': {'type': 'integer', 'description': 'Twin ID to create chat with'},
                    'custom': {'type': 'string', 'description': 'Optional custom name for the chat'}
                },
                'required': ['twin'],
                'example': {
                    'twin': 123,
                    'custom': 'My Chat with Twin'
                }
            }
        }
    ),
    retrieve=extend_schema(
        summary="Get chat channel details",
        description="Retrieves details of a specific chat channel"
    ),
    update=extend_schema(
        summary="Update chat channel",
        description="Update a chat channel's details",
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'twin': {'type': 'integer', 'description': 'Twin ID'},
                    'custom': {'type': 'string', 'description': 'Custom name for the chat'},
                },
                'required': ['twin'],
                'example': {
                    'twin': 123,
                    'custom': 'Updated Chat Name',
                    'is_muted': True
                }
            }
        }
    ),
    partial_update=extend_schema(
        summary="Partially update chat channel",
        description="Partially update a chat channel's details",
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'custom': {'type': 'string', 'description': 'Custom name for the chat'},
                },
                'example': {
                    'custom_name': 'New Chat Name'
                }
            }
        }
    ),
    destroy=extend_schema(
        summary="Delete chat channel",
        description="Delete a chat channel"
    ),
    mark_messages_read=extend_schema(
        summary="Mark messages as read",
        description="Marks all unread messages in the chat channel as read"
    )
)
class UserTwinChatViewSet(viewsets.ModelViewSet):
    """
    Viewset for managing user-twin chat channels with proper authorization checks
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
        # Extract twin_id from request data
        # Try both 'twin' and 'twin_id' fields to be flexible
        twin_id = serializer.initial_data.get('twin')
        if not twin_id and 'twin_id' in serializer.initial_data:
            twin_id = serializer.initial_data.get('twin_id')

        if not twin_id:
            raise ValidationError({"twin": "Twin ID is required"})

        # Check if the user owns the twin or has access to it
        try:
            twin = Twin.objects.get(id=twin_id)
            # Case 1: User owns the twin
            if twin.owner == self.request.user:
                pass  # User is authorized
            # Case 2: Twin is public
            elif twin.privacy_setting == 'public':
                pass  # Public twins are accessible to all authenticated users
            # Case 3: Twin is shared and user has explicit access
            elif twin.privacy_setting == 'shared':
                # Check if user has access through TwinAccess model
                has_access = TwinAccess.objects.filter(
                    user=self.request.user,
                    twin=twin,
                    grant_expires__gt=timezone.now()  # Access hasn't expired
                ).exists()

                if not has_access:
                    raise PermissionDenied("You don't have access to this twin")
            # Case 4: Twin is private and not owned by the user
            else:
                raise PermissionDenied("This twin is private")

        except Twin.DoesNotExist:
            raise NotFound("Twin not found")
        except ValueError:
            # Handle case where twin_id is not a valid format (e.g., not an integer)
            raise ValidationError({"twin": "Invalid Twin ID format"})

        # If we reach here, the user is authorized
        # Make sure to pass the twin object to the serializer
        serializer.save(user=self.request.user, twin=twin)

    def check_twin_access(self, twin_id):
        """
        Helper method to check if the current user has access to the specified twin
        Returns True if access is granted, False otherwise
        """
        try:
            twin = Twin.objects.get(id=twin_id)

            # Case 1: User owns the twin
            if twin.owner == self.request.user:
                return True

            # Case 2: Twin is public
            if twin.privacy_setting == 'public':
                return True

            # Case 3: Twin is shared and user has explicit access
            if twin.privacy_setting == 'shared':
                has_access = TwinAccess.objects.filter(
                    user=self.request.user,
                    twin=twin,
                    grant_expires__gt=timezone.now()  # Access hasn't expired
                ).exists()

                return has_access

            # Case 4: Twin is private and not owned by the user
            return False

        except Twin.DoesNotExist:
            return False
        except ValueError:
            # Handle case where twin_id cannot be converted to expected type
            return False

    def get_object(self):
        """
        Override get_object to add additional security check
        """
        obj = super().get_object()

        # Verify the user can access this twin
        if not self.check_twin_access(obj.twin.id):
            raise PermissionDenied("You don't have access to this twin")

        return obj

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


@extend_schema_view(
    list=extend_schema(
        summary="Get messages within a chat",
        description="Get messages within a chat channel"
    ),
    create=extend_schema(
        summary="Create message within a chat",
        description="Create a new message within a chat channel"
    ),
    retrieve=extend_schema(
        summary="Get message details",
        description="Get details for a specific message"
    ),
    update=extend_schema(
        summary="Update message details",
        description="Update details for a specific message"
    ),
    partial_update=extend_schema(
        summary="Partially update message details",
        description="Partially update details for a specific message"
    ),
    destroy=extend_schema(
        summary="Delete message",
        description="Delete a specific message"
    )
)
class MessageViewSet(viewsets.ModelViewSet):
    """
    Viewset for messages within a chat with optimized pagination
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
        Get message history for a specific chat with optimized pagination and no duplicates
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

        # Mark messages as read while we're here
        unread_count = Message.objects.filter(
            chat=chat,
            is_from_user=False,
            status__in=['sent', 'delivered']
        ).update(status='read', status_updated_at=timezone.now())

        # Update chat's last_active timestamp when viewing
        chat.save(update_fields=['last_active'])

        # Use paginator if defined
        page = self.paginate_queryset(messages)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            response = self.get_paginated_response(serializer.data)
            # Add unread_count to help frontend refresh counts
            response.data['unread_count'] = unread_count
            return response

        serializer = self.get_serializer(messages, many=True)
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        summary="List voice recordings",
        description="List all voice recordings uploaded by the current user"
    ),
    create=extend_schema(
        summary="Upload voice recording",
        description="Upload a new voice recording"
    ),
    retrieve=extend_schema(
        summary="Get voice recording details",
        description="Get details for a specific voice recording"
    ),
    update=extend_schema(
        summary="Update voice recording details",
        description="Update details for a specific voice recording"
    ),
    partial_update=extend_schema(
        summary="Partially update voice recording details",
        description="Partially update details for a specific voice recording"
    ),
    destroy=extend_schema(
        summary="Delete voice recording",
        description="Delete a specific voice recording"
    )
)
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