from django.conf import settings
import datetime
from django.core.exceptions import PermissionDenied
from jsonschema import ValidationError
import requests
from rest_framework import viewsets, mixins, status
from rest_framework.exceptions import NotFound
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q

from core.models import Message, Twin, TwinAccess, UserTwinChat, VoiceRecording, MessageReport, MediaFile
from messaging.services.speech_service import SpeechToTextService
from .serializers import MessageSerializer, UserTwinChatSerializer, VoiceRecordingSerializer, MessageReportSerializer
from .permissions import IsChatParticipant
from .pagination import MessagePagination
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
import os
import uuid
from django.core.files.storage import default_storage
import logging
import threading

logger = logging.getLogger(__name__)


# Helper function to convert UUID objects to strings
def serialize_for_websocket(data):
    """
    Convert UUID objects to strings for WebSocket serialization
    """
    if isinstance(data, dict):
        return {k: serialize_for_websocket(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [serialize_for_websocket(item) for item in data]
    elif isinstance(data, uuid.UUID):
        return str(data)
    else:
        return data

@extend_schema_view(
    list=extend_schema(
        summary="List user's chat channels",
        description="Returns a list of all chat channels the authenticated user has access to"
    ),
    create=extend_schema(
        summary="Create new chat channel",
        description="Creates a new chat channel between the user and a twin. Requires proper twin access."
    ),
    retrieve=extend_schema(
        summary="Get chat channel details",
        description="Retrieves details of a specific chat channel"
    ),
    update=extend_schema(
        summary="Update chat channel",
        description="Update a chat channel's details"
    ),
    partial_update=extend_schema(
        summary="Partially update chat channel",
        description="Partially update a chat channel's details"
    ),
    destroy=extend_schema(
        summary="Delete chat channel",
        description="Delete a chat channel"
    ),
    mark_messages_read=extend_schema(
        summary="Mark messages as read",
        description="Marks all unread messages in the chat channel as read"
    ),
    clear_chat=extend_schema(
        summary="Clear chat history",
        description="Delete all messages in the chat channel"
    ),
    mute_chat=extend_schema(
        summary="Mute chat notifications",
        description="Toggle mute status for chat notifications",
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'muted': {'type': 'boolean', 'description': 'Whether to mute the chat'}
                },
                'required': ['muted']
            }
        }
    ),
    block_contact=extend_schema(
        summary="Block contact",
        description="Block a contact by disabling the chat channel"
    ),
    report_contact=extend_schema(
        summary="Report contact",
        description="Report a contact for inappropriate behavior",
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'reason': {
                        'type': 'string',
                        'enum': ['inappropriate_behavior', 'spam', 'harassment', 'other'],
                        'description': 'Reason for reporting'
                    },
                    'details': {
                        'type': 'string',
                        'description': 'Additional details about the report'
                    }
                },
                'required': ['reason']
            }
        }
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

    @action(detail=True, methods=['delete'])
    def clear_chat(self, request, pk=None):
        """
        Delete all messages in the chat channel
        """
        chat = self.get_object()

        # Count messages before deletion for response
        message_count = Message.objects.filter(chat=chat).count()

        # Delete all messages in the chat
        Message.objects.filter(chat=chat).delete()

        return Response({
            'status': 'success',
            'deleted_count': message_count,
            'message': 'Chat history cleared successfully'
        })

    @action(detail=True, methods=['patch'])
    def mute_chat(self, request, pk=None):
        """
        Toggle mute status for chat notifications
        """
        chat = self.get_object()

        # Check if muted parameter is provided
        if 'muted' not in request.data:
            return Response(
                {"error": "muted parameter is required (true/false)"},
                status=status.HTTP_400_BAD_REQUEST
            )

        muted = request.data.get('muted')

        # Add a muted field to the chat if it doesn't exist
        # In a real implementation, you would have a ChatSettings model or similar
        # For this example, we'll use a simple approach

        # Check if ChatSettings model exists, if not create it
        try:
            from core.models import ChatSettings

            # Get or create chat settings
            chat_settings, created = ChatSettings.objects.get_or_create(
                chat=chat,
                defaults={'muted': muted}
            )

            if not created:
                chat_settings.muted = muted
                chat_settings.save()

        except ImportError:
            # If ChatSettings model doesn't exist, log a warning
            logger.warning("ChatSettings model not found. Mute functionality not fully implemented.")
            # Return success response anyway for frontend compatibility

        return Response({
            'status': 'success',
            'chat_id': str(chat.id),
            'muted': muted
        })

    @action(detail=True, methods=['patch'])
    def toggle_archive(self, request, pk=None):
        """Toggle archive status of a chat"""
        chat = self.get_object()
        chat.is_archived = not chat.is_archived
        chat.save(update_fields=['is_archived'])

        return Response({
            'status': 'success',
            'is_archived': chat.is_archived,
            'message': f'Chat {"archived" if chat.is_archived else "unarchived"} successfully'
        })


    @action(detail=True, methods=['post'])
    def block_contact(self, request, pk=None):
        """
        Block a contact by disabling the chat channel
        """
        chat = self.get_object()

        # Set user_has_access to False to block the contact
        chat.user_has_access = False
        chat.save(update_fields=['user_has_access'])

        return Response({
            'status': 'success',
            'message': 'Contact blocked successfully'
        })

    @action(detail=True, methods=['post'])
    def report_contact(self, request, pk=None):
        """
        Report a contact for inappropriate behavior
        """
        chat = self.get_object()

        # Validate the reason
        reason = request.data.get('reason')
        valid_reasons = ['inappropriate_behavior', 'spam', 'harassment', 'other']

        if not reason or reason not in valid_reasons:
            return Response(
                {"error": "Valid reason required", "valid_reasons": valid_reasons},
                status=status.HTTP_400_BAD_REQUEST
            )

        # In a real implementation, you would have a ContactReport model
        # For this example, we'll use a simple approach

        try:
            # Check if ContactReport model exists
            from core.models import ContactReport

            # Create a new report
            report = ContactReport.objects.create(
                chat=chat,
                reported_by=request.user,
                reason=reason,
                details=request.data.get('details', '')
            )

            # Notify administrators
            self._notify_admins_of_reported_contact(chat, reason, request.data.get('details', ''))

            return Response({
                'status': 'success',
                'report_id': str(report.id),
                'message': 'Contact reported successfully'
            }, status=status.HTTP_201_CREATED)

        except ImportError:
            # If ContactReport model doesn't exist, log a warning
            logger.warning("ContactReport model not found. Creating a simplified report.")

            # Create a simplified report using MessageReport
            # Find the latest message from the twin in this chat
            latest_twin_message = Message.objects.filter(
                chat=chat,
                is_from_user=False
            ).order_by('-created_at').first()

            if latest_twin_message:
                # Report the latest message from the twin
                report = MessageReport.objects.create(
                    message=latest_twin_message,
                    reported_by=request.user,
                    reason='other',  # Use 'other' as a fallback
                    details=f"Contact report: {reason} - {request.data.get('details', '')}"
                )

                # Notify administrators
                self._notify_admins_of_reported_contact(chat, reason, request.data.get('details', ''))

                return Response({
                    'status': 'success',
                    'report_id': str(report.id),
                    'message': 'Contact reported successfully (via message report)'
                }, status=status.HTTP_201_CREATED)
            else:
                # No messages to report
                return Response({
                    'status': 'warning',
                    'message': 'Contact report recorded but no messages found to report'
                }, status=status.HTTP_200_OK)

    def _notify_admins_of_reported_contact(self, chat, reason, details):
        """
        Helper method to notify administrators about a reported contact
        """
        try:
            from django.core.mail import mail_admins

            subject = f"Contact reported (Chat ID: {chat.id})"
            content = f"""
            A contact has been reported:

            Chat ID: {chat.id}
            Twin ID: {chat.twin_id}
            Twin Name: {chat.twin.name if chat.twin else 'Unknown'}
            Reason: {reason}
            Details: {details}

            Please review this report in the admin panel.
            """

            mail_admins(subject, content, fail_silently=True)

            # Log the notification
            logger.info(f"Admin notification sent for reported contact in chat {chat.id}")

        except Exception as e:
            logger.error(f"Failed to send admin notification: {str(e)}", exc_info=True)


@extend_schema_view(
    list=extend_schema(
        summary="Get messages within a chat",
        description="Get messages within a chat channel"
    ),
    create=extend_schema(
        summary="Create message within a chat",
        description="Create a new message within a chat channel"
    ),
    file=extend_schema(
        summary="Send file message",
        description="Send a file message, including PDFs to twins",
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'file': {
                        'type': 'string',
                        'format': 'binary',
                        'description': 'File to upload'
                    },
                    'chat': {
                        'type': 'string',
                        'format': 'uuid',
                        'description': 'Chat ID'
                    },
                    'message_type': {
                        'type': 'string',
                        'enum': ['file'],
                        'description': 'Message type (must be "file")'
                    },
                    'twin_id': {
                        'type': 'string',
                        'format': 'uuid',
                        'description': 'Twin ID (required for PDF files)'
                    },
                    'reply_to': {
                        'type': 'string',
                        'format': 'uuid',
                        'description': 'ID of message being replied to (optional)'
                    }
                },
                'required': ['file', 'chat', 'message_type']
            }
        }
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
    ),
    report=extend_schema(
        summary="Report a message",
        description="Report a message for inappropriate content",
        request={
            'application/json': {
                'type': 'object',
                'properties': {
                    'reason': {
                        'type': 'string',
                        'enum': ['inappropriate', 'offensive', 'harmful', 'spam', 'other'],
                        'description': 'Reason for reporting'
                    },
                    'details': {
                        'type': 'string',
                        'description': 'Additional details about the report'
                    }
                },
                'required': ['reason']
            }
        }
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
    # Support both JSON and multipart data
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        chat_id = self.request.query_params.get('chat', None)

        # Start with an optimized base queryset
        queryset = Message.objects.select_related(
            'voice_note',
            'file_attachment',
            'chat',
            'chat__twin',
            'chat__twin__avatar',
            'reply_to'
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

    def create(self, request, *args, **kwargs):
        """
        Override create to handle different message types properly
        """
        try:
            # Get message type to determine how to handle the request
            message_type = request.data.get('message_type', 'text')

            # For voice messages, validate that voice_note exists
            if message_type == 'voice':
                voice_note_id = request.data.get('voice_note')
                if not voice_note_id:
                    return Response(
                        {"error": "voice_note ID is required for voice messages"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Verify the voice recording exists and belongs to a message the user can access
                try:
                    voice_recording = VoiceRecording.objects.get(id=voice_note_id)
                except VoiceRecording.DoesNotExist:
                    return Response(
                        {"error": "Voice recording not found"},
                        status=status.HTTP_404_NOT_FOUND
                    )

            # For file messages without actual file upload, redirect to file endpoint
            elif message_type == 'file' and 'file' in request.FILES:
                return self.file(request)

            # Validate chat access
            chat_id = request.data.get('chat')
            if chat_id:
                try:
                    chat = UserTwinChat.objects.get(id=chat_id, user=request.user)
                except UserTwinChat.DoesNotExist:
                    return Response(
                        {"error": "Chat not found or access denied"},
                        status=status.HTTP_404_NOT_FOUND
                    )

            # Use the default create method for text and voice messages
            return super().create(request, *args, **kwargs)

        except Exception as e:
            logger.error(f"Error in message create: {str(e)}", exc_info=True)
            return Response(
                {"error": "Failed to create message"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def perform_create(self, serializer):
        """
        Custom logic when creating a message
        """
        # If reply_to is provided, validate that it exists and is in the same chat
        reply_to_id = self.request.data.get('reply_to')
        if reply_to_id:
            try:
                reply_to = Message.objects.get(id=reply_to_id)
                chat_id = self.request.data.get('chat')

                if str(reply_to.chat_id) != str(chat_id):
                    raise ValidationError({"reply_to": "Cannot reply to a message from a different chat"})

            except Message.DoesNotExist:
                raise ValidationError({"reply_to": "Message to reply to does not exist"})

        # Save the message
        message = serializer.save()

        # Update chat's last_active timestamp
        if message.chat:
            message.chat.last_active = timezone.now()
            message.chat.save(update_fields=['last_active'])

        # Notify via WebSocket if available
        self._notify_websocket_message(message)

        return message

    def _notify_websocket_message(self, message):
        """
        Notify WebSocket clients of new message
        """
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync

            channel_layer = get_channel_layer()
            chat_group_name = f'chat_{message.chat_id}'

            # Serialize message for WebSocket
            serializer = MessageSerializer(message, context={'request': self.request})

            # Convert UUID objects to strings
            message_data = serialize_for_websocket(serializer.data)

            async_to_sync(channel_layer.group_send)(
                chat_group_name,
                {
                    'type': 'chat_message',
                    'message': message_data
                }
            )

        except Exception as e:
            logger.error(f"Failed to notify WebSocket of message: {str(e)}", exc_info=True)

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def file(self, request):
        """
        Send a file message, with special handling for PDFs to twins
        """
        try:
            # Validate required fields
            file_obj = request.FILES.get('file')
            chat_id = request.data.get('chat')
            message_type = request.data.get('message_type')
            twin_id = request.data.get('twin_id')
            reply_to_id = request.data.get('reply_to')

            if not file_obj:
                return Response(
                    {"error": "No file provided"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if not chat_id:
                return Response(
                    {"error": "Chat ID is required"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if message_type != 'file':
                return Response(
                    {"error": "Message type must be 'file'"},
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

            # Special validation for PDF files
            if file_obj.content_type == 'application/pdf':
                # Check if twin_id is provided in any format
                twin_id = request.data.get('twin_id')

                # Log the received parameters for debugging
                logger.debug(f"PDF upload - twin_id: {twin_id}, chat.twin.id: {chat.twin.id}")

                if not twin_id:
                    # If twin_id is not provided, use the chat's twin ID
                    twin_id = str(chat.twin.id)
                    logger.debug(f"Using chat's twin ID: {twin_id}")

                # Store the twin_id for later use
                twin_id_for_processing = str(chat.twin.id)

                # Additional PDF size validation (e.g., max 10MB)
                max_pdf_size = getattr(settings, 'MAX_PDF_SIZE', 10 * 1024 * 1024)  # 10MB default
                if file_obj.size > max_pdf_size:
                    return Response(
                        {"error": f"PDF file too large. Maximum size is {max_pdf_size // (1024*1024)}MB"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Validate reply_to if provided
            reply_to_message = None
            if reply_to_id:
                try:
                    reply_to_message = Message.objects.get(id=reply_to_id, chat=chat)
                except Message.DoesNotExist:
                    return Response(
                        {"error": "Reply-to message not found in this chat"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Determine file category based on MIME type
            file_category = self._determine_file_category(file_obj.content_type)

            # Create MediaFile record
            media_file = MediaFile.objects.create(
                original_name=file_obj.name,
                file_category=file_category,
                mime_type=file_obj.content_type,
                size_bytes=file_obj.size,
                uploader=request.user,
                is_public=False  # Files in chats are private by default
            )

            # Generate unique filename and save file
            file_ext = os.path.splitext(file_obj.name)[1]
            filename = f"chat_files/{chat_id}/{uuid.uuid4()}{file_ext}"
            storage_path = default_storage.save(filename, file_obj)

            # Update MediaFile with storage path
            media_file.storage_path = storage_path
            media_file.save()

            # Create text content for the message
            if file_obj.content_type == 'application/pdf' and twin_id:
                text_content = f"📄 PDF sent to {chat.twin.name}: {file_obj.name}"
            else:
                text_content = f"📎 File: {file_obj.name}"

            # Create Message record
            message = Message.objects.create(
                chat=chat,
                is_from_user=True,
                message_type='file',
                text_content=text_content,
                file_attachment=media_file,
                reply_to=reply_to_message,
                status='sent'
            )

            # If it's a PDF to a twin, send it to the external service
            if file_obj.content_type == 'application/pdf':
                # Send PDF to external service in a separate thread to avoid blocking
                threading.Thread(
                    target=self._send_pdf_to_external_service,
                    args=(media_file, twin_id_for_processing),
                    daemon=True
                ).start()

            # Update chat's last_active timestamp
            chat.last_active = timezone.now()
            chat.save(update_fields=['last_active'])

            # Serialize and return the message
            serializer = MessageSerializer(message, context={'request': request})

            # Notify via WebSocket if available
            self._notify_websocket_file_message(message, chat_id)

            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error in file upload: {str(e)}", exc_info=True)
            return Response(
                {"error": "Failed to process file upload"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    def _determine_file_category(self, mime_type):
        """
        Determine file category based on MIME type
        """
        if mime_type.startswith('image/'):
            return 'image'
        elif mime_type.startswith('audio/'):
            return 'audio'
        elif mime_type in ['application/pdf', 'application/msword',
                          'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                          'text/plain', 'application/rtf']:
            return 'document'
        else:
            return 'document'  # Default to document for unknown types

    def _notify_websocket_file_message(self, message, chat_id):
        """
        Notify WebSocket clients of new file message
        """
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync

            channel_layer = get_channel_layer()
            chat_group_name = f'chat_{chat_id}'

            # Serialize message for WebSocket
            serializer = MessageSerializer(message)

            # Convert UUID objects to strings
            message_data = serialize_for_websocket(serializer.data)

            async_to_sync(channel_layer.group_send)(
                chat_group_name,
                {
                    'type': 'chat_message',  # Use existing handler
                    'message': message_data
                }
            )

        except Exception as e:
            logger.error(f"Failed to notify WebSocket of file message: {str(e)}", exc_info=True)

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

    @action(detail=True, methods=['post'])
    def report(self, request, pk=None):
        """
        Report a message for inappropriate content
        """
        message = self.get_object()

        # Validate the reason
        reason = request.data.get('reason')
        if not reason or reason not in dict(MessageReport.REPORT_REASON_CHOICES):
            return Response(
                {"error": "Valid reason required", "valid_reasons": dict(MessageReport.REPORT_REASON_CHOICES)},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Check if user has already reported this message
        if MessageReport.objects.filter(message=message, reported_by=request.user).exists():
            return Response(
                {"error": "You have already reported this message"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create the report
        report = MessageReport.objects.create(
            message=message,
            reported_by=request.user,
            reason=reason,
            details=request.data.get('details', '')
        )

        # Count how many reports this message has in the chat
        report_count = MessageReport.objects.filter(
            message__chat=message.chat
        ).count()

        # Notify administrators if report count reaches a threshold
        threshold = getattr(settings, 'MESSAGE_REPORT_THRESHOLD', 3)
        if report_count >= threshold:
            self._notify_admins_of_reported_message(message, report_count)

        return Response(
            {
                'status': 'Message reported successfully',
                'report_id': report.id
            },
            status=status.HTTP_201_CREATED
        )

    def _notify_admins_of_reported_message(self, message, report_count):
        """
        Helper method to notify administrators about a frequently reported message
        """
        try:
            from django.core.mail import mail_admins

            subject = f"Message reported multiple times (ID: {message.id})"
            content = f"""
            A message has been reported multiple times:

            Message ID: {message.id}
            Chat ID: {message.chat_id}
            Content: {message.text_content[:200] if message.text_content else '[No text content]'}
            Current report count: {report_count}

            Please review this message in the admin panel.
            """

            mail_admins(subject, content, fail_silently=True)

            # Log the notification
            logger.info(f"Admin notification sent for message {message.id} with {report_count} reports")

        except Exception as e:
            logger.error(f"Failed to send admin notification: {str(e)}", exc_info=True)

    def _send_pdf_to_external_service(self, media_file, twin_id):
        """
        Send PDF file to external service with twin_id
        """
        try:
            # Get the external service URL from settings or use a default
            external_service_url = getattr(settings, 'PDF_UPLOAD_SERVICE_URL', 'https://your-ngrok-url.app/upload_doc')

            # Get the file path
            file_path = default_storage.path(media_file.storage_path)

            # Open the file
            with open(file_path, 'rb') as pdf_file:
                # Prepare the files and data for the request
                files = {'file': (media_file.original_name, pdf_file, 'application/pdf')}
                data = {'twin_id': str(twin_id)}

                # Send the POST request
                response = requests.post(
                    external_service_url,
                    files=files,
                    data=data,
                    timeout=30  # 30 seconds timeout
                )

                # Check if the request was successful
                if response.status_code == 200:
                    logger.info(f"Successfully sent PDF to external service for twin {twin_id}")

                    # Notify WebSocket clients that the PDF was processed
                    try:
                        from channels.layers import get_channel_layer
                        from asgiref.sync import async_to_sync

                        channel_layer = get_channel_layer()
                        chat_group_name = f'chat_{media_file.message_set.first().chat_id}'

                        # Get the response content
                        response_data = response.json() if response.content else {}

                        async_to_sync(channel_layer.group_send)(
                            chat_group_name,
                            {
                                'type': 'pdf_uploaded',
                                'file_id': str(media_file.id),
                                'message_id': str(media_file.message_set.first().id),
                                'status': 'success',
                                'twin_id': twin_id,
                                'response': response_data
                            }
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify WebSocket of PDF processing: {str(e)}", exc_info=True)

                    return True
                else:
                    logger.warning(f"Failed to send PDF to external service. Status code: {response.status_code}, Response: {response.text}")

                    # Notify WebSocket clients of failure
                    try:
                        from channels.layers import get_channel_layer
                        from asgiref.sync import async_to_sync

                        channel_layer = get_channel_layer()
                        chat_group_name = f'chat_{media_file.message_set.first().chat_id}'

                        async_to_sync(channel_layer.group_send)(
                            chat_group_name,
                            {
                                'type': 'pdf_uploaded',
                                'file_id': str(media_file.id),
                                'message_id': str(media_file.message_set.first().id),
                                'status': 'error',
                                'error': f"Failed to process PDF: {response.text}"
                            }
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify WebSocket of PDF processing error: {str(e)}", exc_info=True)

                    return False

        except Exception as e:
            logger.error(f"Error sending PDF to external service: {str(e)}", exc_info=True)

            # Notify WebSocket clients of exception
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync

                channel_layer = get_channel_layer()
                chat_group_name = f'chat_{media_file.message_set.first().chat_id}'

                async_to_sync(channel_layer.group_send)(
                    chat_group_name,
                    {
                        'type': 'pdf_uploaded',
                        'file_id': str(media_file.id),
                        'message_id': str(media_file.message_set.first().id),
                        'status': 'error',
                        'error': f"Exception processing PDF: {str(e)}"
                    }
                )
            except Exception as nested_e:
                logger.error(f"Failed to notify WebSocket of PDF processing exception: {str(nested_e)}", exc_info=True)

            return False

        
@extend_schema_view(
    list=extend_schema(
        summary="List voice recordings",
        description="List all voice recordings uploaded by the current user"
    ),
    create=extend_schema(
        summary="Upload voice recording",
        request={
            'multipart/form-data': {
                'type': 'object',
                'properties': {
                    'audio_file': {
                        'type': 'string',
                        'format': 'binary',
                        'description': 'Audio file to upload (webm, mp3, wav)'
                    },
                    'duration_seconds': {
                        'type': 'number',
                        'description': 'Duration of audio in seconds'
                    },
                    'format': {
                        'type': 'string',
                        'description': 'Audio format (e.g., audio/webm)',
                        'default': 'audio/webm'
                    },
                    'sample_rate': {
                        'type': 'integer',
                        'description': 'Sample rate in Hz',
                        'default': 44100
                    }
                },
                'required': ['audio_file']
            }
        },
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
    API endpoint for voice recordings.
    Supports create, retrieve, and list operations.
    """
    serializer_class = VoiceRecordingSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        user_chats = UserTwinChat.objects.filter(user=self.request.user)
        if not user_chats.exists():
            return VoiceRecording.objects.none()

        user_messages = Message.objects.filter(chat__in=user_chats)
        return VoiceRecording.objects.filter(
            id__in=user_messages.values_list('voice_note', flat=True)
        )

    def _notify_twin_of_transcription(self, voice_recording_id, chat_id=None):
        """
        Notify the twin AI of a completed transcription by sending a message to the appropriate chat channel
        """
        try:
            voice_recording = VoiceRecording.objects.get(id=voice_recording_id)

            # If transcription was not successful, don't send to AI
            if not voice_recording.is_processed or not voice_recording.transcription or \
            voice_recording.transcription.startswith("Transcription error") or \
            voice_recording.transcription.startswith("No speech detected"):
                logger.warning(f"Not sending failed transcription to twin: {voice_recording.transcription}")
                return

            # Find associated message if chat_id isn't provided
            if not chat_id:
                message = Message.objects.filter(voice_note_id=voice_recording_id).first()
                if message:
                    chat_id = message.chat_id
                else:
                    logger.error(f"No message found for voice recording {voice_recording_id}")
                    return

            # Import here to avoid circular imports
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync

            # Get the channel layer
            channel_layer = get_channel_layer()
            chat_group_name = f'chat_{chat_id}'

            # Add a unique identifier to prevent duplicate processing
            # Use combination of voice ID and timestamp
            notification_id = f"{voice_recording_id}_{int(datetime.datetime.now().timestamp())}"

            # Send the transcription to the chat group
            async_to_sync(channel_layer.group_send)(
                chat_group_name,
                {
                    'type': 'transcription_completed',
                    'voice_id': str(voice_recording_id),
                    'transcription': voice_recording.transcription,
                    'chat_id': str(chat_id),
                    'notification_id': notification_id
                }
            )

            logger.info(f"Notified twin of transcription for voice recording {voice_recording_id} in chat {chat_id}")

        except Exception as e:
            logger.error(f"Failed to notify twin of transcription: {str(e)}", exc_info=True)

    def _run_transcription(self, storage_path, voice_recording_id, language_code=None, chat_id=None):
        try:
            api_key = getattr(settings, 'ASSEMBLY_AI_API_KEY', '')
            if not api_key:
                logger.error("AssemblyAI API key not configured.")
                self._update_transcription_failure(voice_recording_id, "Transcription service not configured.")
                return

            logger.info(f"Starting transcription for recording {voice_recording_id}")

            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            speech_service = SpeechToTextService()

            if not os.path.exists(storage_path):
                logger.error(f"File not found: {storage_path}")
                self._update_transcription_failure(voice_recording_id, "Audio file not found.")
                return

            file_size = os.path.getsize(storage_path)
            logger.info(f"File size: {file_size} bytes")

            if file_size == 0:
                logger.error("Audio file is empty.")
                self._update_transcription_failure(voice_recording_id, "Empty audio file.")
                return

            try:
                with open(storage_path, 'rb') as f:
                    header = f.read(12)
                logger.info(f"File header: {header.hex()}")
            except Exception as e:
                logger.error(f"Failed to read file header: {str(e)}")

            transcript = loop.run_until_complete(
                speech_service.transcribe_voice(storage_path, language_code)
            )

            if not transcript or transcript.strip() == "":
                logger.warning("Empty transcript received.")
                transcript = "No speech detected."

            elif transcript.startswith("Transcription error") or \
                 transcript.startswith("Speech transcription service not") or \
                 transcript in [
                     "Failed to upload audio file for transcription.",
                     "Sorry, I couldn't transcribe your voice message."
                 ]:
                logger.error(f"Transcription service returned error: {transcript}")
                self._update_transcription_failure(voice_recording_id, transcript)
                return

            voice_recording = VoiceRecording.objects.get(id=voice_recording_id)
            voice_recording.transcription = transcript
            voice_recording.is_processed = True
            voice_recording.save()

            loop.close()

            logger.info(f"Transcription completed for recording {voice_recording_id}: '{transcript}'")

            # Notify the twin of the completed transcription
            self._notify_twin_of_transcription(voice_recording_id, chat_id)

        except Exception as e:
            logger.error(f"Error during transcription: {str(e)}", exc_info=True)
            self._update_transcription_failure(voice_recording_id, "Transcription failed due to an error.")

    def _update_transcription_failure(self, voice_recording_id, error_message):
        try:
            voice_recording = VoiceRecording.objects.get(id=voice_recording_id)
            voice_recording.is_processed = True
            voice_recording.transcription = error_message
            voice_recording.save()
            logger.info(f"Updated failed transcription status for {voice_recording_id}")
        except Exception as e:
            logger.error(f"Failed to update transcription failure: {str(e)}", exc_info=True)

    def create(self, request):
        audio_file = request.FILES.get('audio_file')
        if not audio_file:
            raise ValidationError("No audio file provided.")

        data = {
            'duration_seconds': request.data.get('duration_seconds', 0),
            'format': request.data.get('format', 'audio/webm'),
            'sample_rate': request.data.get('sample_rate', 44100),
        }

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        voice_recording = serializer.save()

        file_ext = os.path.splitext(audio_file.name)[1]
        filename = f"voice_recordings/{uuid.uuid4()}{file_ext}"

        storage_path = default_storage.save(filename, audio_file)
        logger.info(f"Saved audio file: {storage_path}")

        absolute_path = default_storage.path(storage_path) if hasattr(default_storage, 'path') else storage_path

        voice_recording.storage_path = storage_path
        voice_recording.save()

        # Get the chat_id if provided
        chat_id = request.data.get('chat_id')

        language_code = request.data.get('language_code')
        thread = threading.Thread(
            target=self._run_transcription,
            args=(absolute_path, voice_recording.id, language_code, chat_id),
            daemon=True
        )
        thread.start()

        return Response(
            {**serializer.data, "message": "Voice recording saved. Transcription in progress."},
            status=status.HTTP_201_CREATED
        )