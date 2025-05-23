from concurrent.futures import ThreadPoolExecutor
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
from django.utils.decorators import method_decorator

from core.models import Message, Twin, TwinAccess, UserTwinChat, VoiceRecording, MessageReport
from messaging.services.heygen_streaming_service import HeyGenStreamingService
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
import asyncio
from django.views.decorators.cache import never_cache

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



class HeyGenStreamingViewSet(viewsets.ViewSet):
    """
    ViewSet for HeyGen streaming operations that integrates with Node.js microservice
    """
    permission_classes = [IsAuthenticated]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.heygen_service = HeyGenStreamingService()
        self.streaming_base_url = getattr(
            settings,
            'STREAMING_MICROSERVICE_URL',
            'http://localhost:3001'
        )

    def _verify_chat_access(self, chat_id, user):
        """Verify user has access to the chat"""
        try:
            return UserTwinChat.objects.get(
                id=chat_id,
                user=user,
                user_has_access=True
            )
        except UserTwinChat.DoesNotExist:
            return None

    def _run_async_task(self, async_func):
        """Helper to run async functions in sync context"""
        def run_in_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(async_func())
            finally:
                loop.close()

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(run_in_thread)
            return future.result(timeout=30)  # 30 second timeout

    @action(detail=False, methods=['post'], url_path='generate-script')
    @method_decorator(never_cache)
    def generate_script(self, request):
        """
        Generate a script from chat conversation
        POST /api/v1/messaging/heygen-streaming/generate-script/

        Body:
        {
            "chat_id": "uuid",
            "script_type": "summary|continuation|introduction",
            "context_messages": 10
        }
        """
        try:
            # Validate required fields
            chat_id = request.data.get('chat_id')
            if not chat_id:
                return Response({
                    'success': False,
                    'error': 'chat_id is required'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Verify chat access
            chat = self._verify_chat_access(chat_id, request.user)
            if not chat:
                return Response({
                    'success': False,
                    'error': 'Chat not found or access denied'
                }, status=status.HTTP_404_NOT_FOUND)

            # Extract parameters
            script_type = request.data.get('script_type', 'summary')
            context_messages = request.data.get('context_messages', 10)

            # Generate script asynchronously
            async def generate_script_async():
                return await self.heygen_service.generate_streaming_script(
                    chat_id=chat_id,
                    context_messages=context_messages,
                    script_type=script_type
                )

            result = self._run_async_task(generate_script_async)

            if result.get('success'):
                return Response({
                    'success': True,
                    'script': result['script'],
                    'metadata': {
                        'chat_id': result['chat_id'],
                        'script_type': result['script_type'],
                        'message_count': result['message_count']
                    }
                })
            else:
                return Response({
                    'success': False,
                    'error': result.get('error', 'Failed to generate script')
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            logger.error(f"Error generating script: {str(e)}")
            return Response({
                'success': False,
                'error': 'Internal server error'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='start-streaming')
    @method_decorator(never_cache)
    def start_streaming(self, request):
        """
        Start HeyGen streaming with provided script
        POST /api/v1/messaging/heygen-streaming/start-streaming/

        Body:
        {
            "script": "text to speak",
            "avatar_id": "avatar_id",
            "voice_id": "voice_id",
            "language": "en",
            "quality": "high"
        }
        """
        try:
            # Validate required fields
            required_fields = ['script', 'avatar_id', 'voice_id']
            missing_fields = [field for field in required_fields if not request.data.get(field)]

            if missing_fields:
                return Response({
                    'success': False,
                    'error': f'Missing required fields: {", ".join(missing_fields)}'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Prepare payload for Node.js microservice
            payload = {
                'avatar_id': request.data['avatar_id'],
                'voice_id': request.data['voice_id'],
                'text': request.data['script'],
                'language': request.data.get('language', 'en'),
                'quality': request.data.get('quality', 'high')
            }

            # Call Node.js streaming microservice
            response = requests.post(
                f"{self.streaming_base_url}/api/streaming/start",
                json=payload,
                timeout=30,
                headers={'Content-Type': 'application/json'}
            )

            response.raise_for_status()
            result = response.json()

            if result.get('success'):
                return Response({
                    'success': True,
                    'session_id': result['sessionId'],
                    'stream_info': result['streamInfo'],
                    'message': 'Streaming session started successfully'
                })
            else:
                return Response({
                    'success': False,
                    'error': result.get('error', 'Failed to start streaming')
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling streaming microservice: {str(e)}")
            return Response({
                'success': False,
                'error': 'Streaming service unavailable'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Error starting streaming: {str(e)}")
            return Response({
                'success': False,
                'error': 'Internal server error'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='generate-and-stream')
    @method_decorator(never_cache)
    def generate_and_stream(self, request):
        """
        Combined endpoint: Generate script from chat and start streaming
        POST /api/v1/messaging/heygen-streaming/generate-and-stream/

        Body:
        {
            "chat_id": "uuid",
            "avatar_id": "avatar_id",
            "voice_id": "voice_id",
            "script_type": "summary",
            "language": "en",
            "quality": "high"
        }
        """
        try:
            # Validate required fields
            required_fields = ['chat_id', 'avatar_id', 'voice_id']
            missing_fields = [field for field in required_fields if not request.data.get(field)]

            if missing_fields:
                return Response({
                    'success': False,
                    'error': f'Missing required fields: {", ".join(missing_fields)}'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Verify chat access
            chat = self._verify_chat_access(request.data['chat_id'], request.user)
            if not chat:
                return Response({
                    'success': False,
                    'error': 'Chat not found or access denied'
                }, status=status.HTTP_404_NOT_FOUND)

            # Async pipeline for generating script and starting stream
            async def pipeline():
                try:
                    # Step 1: Generate script
                    logger.info(f"Generating script for chat {request.data['chat_id']}")
                    script_result = await self.heygen_service.generate_streaming_script(
                        chat_id=request.data['chat_id'],
                        script_type=request.data.get('script_type', 'summary'),
                        context_messages=request.data.get('context_messages', 10)
                    )

                    if not script_result.get('success'):
                        return {
                            'success': False,
                            'error': f"Script generation failed: {script_result.get('error')}"
                        }

                    # Step 2: Start streaming with generated script
                    logger.info(f"Starting stream with generated script")
                    payload = {
                        'avatar_id': request.data['avatar_id'],
                        'voice_id': request.data['voice_id'],
                        'text': script_result['script'],
                        'language': request.data.get('language', 'en'),
                        'quality': request.data.get('quality', 'high')
                    }

                    # Call Node.js microservice
                    response = requests.post(
                        f"{self.streaming_base_url}/api/streaming/start",
                        json=payload,
                        timeout=30,
                        headers={'Content-Type': 'application/json'}
                    )

                    response.raise_for_status()
                    stream_result = response.json()

                    if stream_result.get('success'):
                        return {
                            'success': True,
                            'session_id': stream_result['sessionId'],
                            'stream_info': stream_result['streamInfo'],
                            'script_preview': script_result['script'][:200] + '...' if len(script_result['script']) > 200 else script_result['script'],
                            'metadata': {
                                'chat_id': request.data['chat_id'],
                                'script_type': script_result['script_type'],
                                'message_count': script_result['message_count']
                            }
                        }
                    else:
                        return {
                            'success': False,
                            'error': f"Streaming failed: {stream_result.get('error')}"
                        }

                except requests.exceptions.RequestException as e:
                    logger.error(f"Microservice request failed: {str(e)}")
                    return {
                        'success': False,
                        'error': 'Streaming service unavailable'
                    }
                except Exception as e:
                    logger.error(f"Pipeline error: {str(e)}")
                    return {
                        'success': False,
                        'error': f'Pipeline failed: {str(e)}'
                    }

            # Execute pipeline
            result = self._run_async_task(pipeline)

            if result.get('success'):
                return Response(result)
            else:
                error_status = status.HTTP_503_SERVICE_UNAVAILABLE if 'unavailable' in result.get('error', '') else status.HTTP_500_INTERNAL_SERVER_ERROR
                return Response(result, status=error_status)

        except Exception as e:
            logger.error(f"Generate and stream error: {str(e)}")
            return Response({
                'success': False,
                'error': 'Internal server error'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='send-text')
    @method_decorator(never_cache)
    def send_text(self, request):
        """
        Send additional text to existing streaming session
        POST /api/v1/messaging/heygen-streaming/send-text/

        Body:
        {
            "session_id": "uuid",
            "text": "additional text to speak"
        }
        """
        try:
            session_id = request.data.get('session_id')
            text = request.data.get('text')

            if not session_id or not text:
                return Response({
                    'success': False,
                    'error': 'session_id and text are required'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Call Node.js microservice
            payload = {
                'sessionId': session_id,
                'text': text
            }

            response = requests.post(
                f"{self.streaming_base_url}/api/streaming/speak",
                json=payload,
                timeout=10,
                headers={'Content-Type': 'application/json'}
            )

            response.raise_for_status()
            result = response.json()

            return Response({
                'success': result.get('success', True),
                'message': result.get('message', 'Text sent successfully')
            })

        except requests.exceptions.RequestException as e:
            logger.error(f"Error sending text to stream: {str(e)}")
            return Response({
                'success': False,
                'error': 'Failed to send text to streaming session'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Send text error: {str(e)}")
            return Response({
                'success': False,
                'error': 'Internal server error'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='stop-streaming')
    @method_decorator(never_cache)
    def stop_streaming(self, request):
        """
        Stop streaming session
        POST /api/v1/messaging/heygen-streaming/stop-streaming/

        Body:
        {
            "session_id": "uuid"
        }
        """
        try:
            session_id = request.data.get('session_id')

            if not session_id:
                return Response({
                    'success': False,
                    'error': 'session_id is required'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Call Node.js microservice
            payload = {'sessionId': session_id}

            response = requests.post(
                f"{self.streaming_base_url}/api/streaming/stop",
                json=payload,
                timeout=10,
                headers={'Content-Type': 'application/json'}
            )

            response.raise_for_status()
            result = response.json()

            return Response({
                'success': result.get('success', True),
                'message': result.get('message', 'Session stopped successfully')
            })

        except requests.exceptions.RequestException as e:
            logger.error(f"Error stopping stream: {str(e)}")
            return Response({
                'success': False,
                'error': 'Failed to stop streaming session'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Stop streaming error: {str(e)}")
            return Response({
                'success': False,
                'error': 'Internal server error'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='active-sessions')
    def active_sessions(self, request):
        """
        Get active streaming sessions
        GET /api/v1/messaging/heygen-streaming/active-sessions/
        """
        try:
            response = requests.get(
                f"{self.streaming_base_url}/api/streaming/sessions",
                timeout=10
            )

            response.raise_for_status()
            result = response.json()

            return Response({
                'success': True,
                'sessions': result.get('sessions', [])
            })

        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting active sessions: {str(e)}")
            return Response({
                'success': False,
                'error': 'Failed to get active sessions'
            }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        except Exception as e:
            logger.error(f"Active sessions error: {str(e)}")
            return Response({
                'success': False,
                'error': 'Internal server error'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='health')
    def health_check(self, request):
        """
        Check streaming service health
        GET /api/v1/messaging/heygen-streaming/health/
        """
        try:
            # Check Node.js microservice health
            response = requests.get(
                f"{self.streaming_base_url}/health",
                timeout=5
            )

            microservice_healthy = response.status_code == 200

            # Check HeyGen service health
            heygen_healthy = self.heygen_service.health_check()

            return Response({
                'success': True,
                'microservice': {
                    'healthy': microservice_healthy,
                    'url': self.streaming_base_url
                },
                'heygen_service': {
                    'healthy': heygen_healthy
                },
                'overall_status': 'healthy' if microservice_healthy and heygen_healthy else 'unhealthy'
            })

        except Exception as e:
            logger.error(f"Health check error: {str(e)}")
            return Response({
                'success': False,
                'microservice': {
                    'healthy': False,
                    'url': self.streaming_base_url
                },
                'heygen_service': {
                    'healthy': False
                },
                'overall_status': 'unhealthy',
                'error': str(e)
            })

    @action(detail=False, methods=['post'], url_path='quick-chat-stream')
    @method_decorator(never_cache)
    def quick_chat_stream(self, request):
        """
        Quick endpoint for real-time chat streaming:
        Takes user message -> generates AI response -> streams response
        POST /api/v1/messaging/heygen-streaming/quick-chat-stream/

        Body:
        {
            "message": "user message",
            "chat_id": "uuid",
            "avatar_id": "avatar_id",
            "voice_id": "voice_id",
            "language": "en"
        }
        """
        try:
            # Validate required fields
            required_fields = ['message', 'chat_id', 'avatar_id', 'voice_id']
            missing_fields = [field for field in required_fields if not request.data.get(field)]

            if missing_fields:
                return Response({
                    'success': False,
                    'error': f'Missing required fields: {", ".join(missing_fields)}'
                }, status=status.HTTP_400_BAD_REQUEST)

            # Verify chat access
            chat = self._verify_chat_access(request.data['chat_id'], request.user)
            if not chat:
                return Response({
                    'success': False,
                    'error': 'Chat not found or access denied'
                }, status=status.HTTP_404_NOT_FOUND)

            async def chat_and_stream():
                try:
                    # Step 1: Generate AI response to user message
                    # This would typically involve your AI service
                    user_message = request.data['message']

                    # Get recent conversation context
                    recent_messages = await self.heygen_service.history_service.get_recent_messages(
                        request.data['chat_id'],
                        limit=5
                    )

                    # Generate AI response (simplified - you'd use your actual AI service)
                    ai_response = await self._generate_ai_response(user_message, recent_messages)

                    # Step 2: Stream the AI response
                    payload = {
                        'avatar_id': request.data['avatar_id'],
                        'voice_id': request.data['voice_id'],
                        'text': ai_response,
                        'language': request.data.get('language', 'en'),
                        'quality': request.data.get('quality', 'high')
                    }

                    response = requests.post(
                        f"{self.streaming_base_url}/api/streaming/start",
                        json=payload,
                        timeout=30,
                        headers={'Content-Type': 'application/json'}
                    )

                    response.raise_for_status()
                    stream_result = response.json()

                    if stream_result.get('success'):
                        return {
                            'success': True,
                            'ai_response': ai_response,
                            'session_id': stream_result['sessionId'],
                            'stream_info': stream_result['streamInfo']
                        }
                    else:
                        return {
                            'success': False,
                            'error': f"Streaming failed: {stream_result.get('error')}"
                        }

                except Exception as e:
                    logger.error(f"Chat and stream error: {str(e)}")
                    return {
                        'success': False,
                        'error': str(e)
                    }

            result = self._run_async_task(chat_and_stream)

            if result.get('success'):
                return Response(result)
            else:
                return Response(result, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            logger.error(f"Quick chat stream error: {str(e)}")
            return Response({
                'success': False,
                'error': 'Internal server error'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    async def _generate_ai_response(self, user_message, recent_messages):
        """
        Generate AI response to user message
        This is a simplified version - integrate with your actual AI service
        """
        try:
            # Format conversation context
            context = []
            for msg in recent_messages[-5:]:  # Last 5 messages
                role = "user" if msg.get('is_from_user') else "assistant"
                context.append({
                    "role": role,
                    "content": msg.get('text_content', '')
                })

            # Add current user message
            context.append({
                "role": "user",
                "content": user_message
            })

            # Generate response using your AI service
            response = await self.heygen_service.openrouter_service.generate_response(
                messages=context,
                temperature=0.7
            )

            if isinstance(response, dict) and response.get('choices'):
                return response['choices'][0]['message']['content']
            else:
                return "I understand your message. Let me help you with that."

        except Exception as e:
            logger.error(f"AI response generation error: {str(e)}")
            return "I'm here to help! Could you please rephrase your question?"