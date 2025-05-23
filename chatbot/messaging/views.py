from django.conf import settings
import datetime
from django.core.exceptions import PermissionDenied
from jsonschema import ValidationError
from rest_framework import viewsets, mixins, status
from rest_framework.exceptions import NotFound
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone

from core.models import Message, Twin, TwinAccess, UserTwinChat, VoiceRecording, MessageReport
from messaging.services.speech_service import SpeechToTextService
from .serializers import MessageSerializer, UserTwinChatSerializer, VoiceRecordingSerializer
from .permissions import IsChatParticipant
from .pagination import MessagePagination
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.parsers import MultiPartParser, FormParser
import os
import uuid
from django.core.files.storage import default_storage
import logging
import threading
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

logger = logging.getLogger(__name__)

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

    def perform_create(self, serializer):
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

        serializer.save()

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