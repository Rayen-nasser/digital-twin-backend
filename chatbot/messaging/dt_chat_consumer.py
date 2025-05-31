import json
import logging
from datetime import datetime
import re
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from core.models import UserTwinChat, Message, VoiceRecording
import asyncio
import aiohttp

logger = logging.getLogger(__name__)

class DigitalTwinChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer that uses Digital Twin API instead of OpenRouter
    No message history or complex context management
    """

    async def connect(self):
        self.user = self.scope["user"]

        if self.user.is_anonymous:
            await self.close()
            return

        self.chat_id = self.scope['url_route']['kwargs']['chat_id']
        self.chat_group_name = f'chat_{self.chat_id}'

        logger.info(f"User {self.user.id} joining chat group: {self.chat_group_name}")

        # Verify user has access to this chat
        if not await self.user_has_chat_access(self.chat_id, self.user.id):
            await self.close()
            return

        # Get twin data for API calls
        self.twin_data = await self.get_twin_data(self.chat_id)

        # Initialize Digital Twin API configuration
        await self.initialize_digital_twin_service()

        await self.channel_layer.group_add(
            self.chat_group_name,
            self.channel_name
        )

        await self.accept()

        # Send connection status to client
        await self.send(text_data=json.dumps({
            'type': 'connection_established',  
            'chat_id': self.chat_id,
            'twin_name': self.twin_data.get('name', 'AI Assistant'),
            'twin_id': self.twin_data.get('id'),
        }))

    async def initialize_digital_twin_service(self):
        """Initialize Digital Twin API configuration"""
        # Configure your Digital Twin API base URL here
        # This should match your FastAPI server URL
        self.digital_twin_base_url = "https://your-ngrok-url.app"  # Replace with actual URL

        # Create aiohttp session for async requests
        self.http_session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
        )

    async def disconnect(self, _):
        """Clean up on disconnect"""
        if hasattr(self, 'chat_id') and self.chat_id:
            await self.update_user_last_seen()

        if hasattr(self, 'chat_group_name') and self.chat_group_name:
            await self.channel_layer.group_discard(
                self.chat_group_name,
                self.channel_name
            )

        # Close HTTP session
        if hasattr(self, 'http_session'):
            await self.http_session.close()

    async def receive(self, text_data):
        """Handle incoming WebSocket messages"""
        try:
            if not text_data:
                logger.warning("Received empty message")
                return

            # Handle ping/pong messages
            if text_data.lower() in ['ping', 'heartbeat']:
                await self.send(text_data='pong')
                return

            # Parse JSON
            try:
                if isinstance(text_data, bytes):
                    text_data = text_data.decode('utf-8')
                data = json.loads(text_data)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.error(f"Invalid message received: {str(e)}")
                await self.send_error("Invalid message format - must be valid JSON")
                return

            logger.info(f"Received message: {data}")

            if not isinstance(data, dict):
                logger.error(f"Expected dictionary but got {type(data)}")
                await self.send_error("Message must be a JSON object")
                return

            message_type = data.get('type')
            content = data.get('content', '')
            reply_to = data.get('reply_to')

            # Handle different message types
            if message_type == 'typing_indicator':
                await self.handle_typing_indicator(data)
                return

            if message_type == 'read_receipt':
                await self.handle_read_receipt(data)
                return

            if message_type in ['text', 'message', 'chat_message']:
                await self.handle_text_message(content, reply_to)
                return

            if message_type == 'voice':
                voice_id = data.get('voice_id')
                if not voice_id:
                    await self.send_error("Voice ID is required for voice messages")
                    return
                await self.handle_voice_message(voice_id, reply_to)
                return

            logger.warning(f"Unknown message type: {message_type}")
            await self.send_error(f"Unknown message type: {message_type}")

        except Exception as e:
            logger.error(f"Error in receive: {str(e)}", exc_info=True)
            await self.send_error("Server error processing your message")

    async def send_error(self, message):
        """Send error message to client"""
        await self.send(text_data=json.dumps({
            'type': 'error',
            'message': message,
            'timestamp': datetime.now().isoformat()
        }))

    async def handle_typing_indicator(self, data):
        """Handle typing indicator messages"""
        await self.channel_layer.group_send(
            self.chat_group_name,
            {
                'type': 'typing_indicator',
                'is_typing': data.get('is_typing', False),
                'user_id': str(self.user.id)
            }
        )

    async def handle_read_receipt(self, data):
        """Handle read receipt messages"""
        message_ids = data.get('message_ids', [])
        if message_ids:
            await self.mark_messages_as_read(message_ids)
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'read_receipt_update',
                    'message_ids': message_ids,
                    'user_id': str(self.user.id)
                }
            )

    async def handle_text_message(self, content, reply_to=None):
        """Handle text messages - simplified without message history"""
        if reply_to:
            logger.info(f"Processing text message replying to: {reply_to}")

        # Save user message to database
        message = await self.save_user_message(
            chat_id=self.chat_id,
            content=content,
            message_type='text',
            reply_to=reply_to
        )

        # Create message object to broadcast
        message_obj = {
            'id': str(message['id']),
            'text_content': content,
            'message_type': 'text',
            'is_from_user': True,
            'timestamp': message['timestamp'].isoformat(),
            'status': 'sent',
            'chat_id': str(self.chat_id)
        }

        if reply_to:
            message_obj['reply_to'] = reply_to

        # Broadcast user message
        await self.channel_layer.group_send(
            self.chat_group_name,
            {
                'type': 'chat_message',
                'message': message_obj
            }
        )

        # Get response from Digital Twin API
        await self.process_digital_twin_response(content, reply_to)

    async def handle_voice_message(self, voice_id, reply_to=None):
        """Handle voice messages"""
        try:
            voice_note = await self.get_voice_recording(voice_id)
            if not voice_note:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Voice recording not found',
                    'code': 'voice_note_not_found'
                }))
                return

            # Save voice message
            message = await self.save_user_message(
                chat_id=self.chat_id,
                content="",
                message_type='voice',
                voice_note_id=voice_id,
                duration_seconds=voice_note.get('duration_seconds', 0),
                reply_to=reply_to
            )

            # Broadcast voice message
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'chat_message',
                    'message': {
                        'id': str(message['id']),
                        'text_content': voice_note.get('transcription', ''),
                        'message_type': 'voice',
                        'is_from_user': True,
                        'timestamp': message['timestamp'].isoformat(),
                        'status': 'sent',
                        'voice_id': voice_id,
                        'duration_seconds': voice_note.get('duration_seconds', 0),
                        'reply_to': reply_to
                    }
                }
            )

            # If transcription is available, process it
            if voice_note.get('transcription'):
                await self.process_digital_twin_response(
                    voice_note['transcription'],
                    reply_to
                )

            await self.send(text_data=json.dumps({
                'type': 'voice_message_received',
                'message_id': str(message['id']),
                'voice_id': voice_id
            }))

        except Exception as e:
            logger.error(f"Error handling voice message: {str(e)}", exc_info=True)
            await self.send_error("Error processing voice message")

    async def process_digital_twin_response(self, user_message, reply_to=None):
        """Process user message with Digital Twin API - no message history"""
        try:
            # Show typing indicator
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'typing_indicator',
                    'is_typing': True,
                    'user_id': 'twin'
                }
            )

            # Call Digital Twin API
            twin_response = await self.call_digital_twin_api(user_message)

            # Hide typing indicator
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'typing_indicator',
                    'is_typing': False,
                    'user_id': 'twin'
                }
            )

            if twin_response['success']:
                # Save twin response
                twin_message = await self.save_twin_message(
                    chat_id=self.chat_id,
                    content=twin_response['content'],
                    reply_to=reply_to
                )

                # Create message object
                message_obj = {
                    'id': str(twin_message['id']),
                    'text_content': twin_response['content'],
                    'message_type': 'text',
                    'is_from_user': False,
                    'timestamp': twin_message['timestamp'].isoformat(),
                    'status': 'sent'
                }

                if reply_to:
                    message_obj['reply_to'] = reply_to

                # Broadcast twin response
                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {
                        'type': 'chat_message',
                        'message': message_obj
                    }
                )
            else:
                # Send error response
                await self.send_error(f"Twin API Error: {twin_response.get('error', 'Unknown error')}")

        except Exception as e:
            logger.error(f"Error processing digital twin response: {str(e)}", exc_info=True)
            await self.send_error("Error getting response from digital twin")

    async def call_digital_twin_api(self, question):
        """
        Call the Digital Twin API ask_twin endpoint

        Args:
            question: User's question/message

        Returns:
            dict: Response from Digital Twin API
        """
        try:
            twin_id = self.twin_data.get('id')
            if not twin_id:
                return {
                    'success': False,
                    'error': 'No twin ID found'
                }

            # Prepare API request
            url = f"{self.digital_twin_base_url}/ask_twin"
            payload = {
                'twin_id': twin_id,
                'user_input': question
            }

            logger.info(f"Calling Digital Twin API: {url}")
            logger.info(f"Payload: {payload}")

            # Make async HTTP request
            async with self.http_session.post(url, json=payload) as response:
                if response.status == 200:
                    response_data = await response.json()

                    # Extract content from response
                    content = self.extract_content_from_response(response_data)

                    return {
                        'success': True,
                        'content': content
                    }
                else:
                    error_text = await response.text()
                    logger.error(f"Digital Twin API error {response.status}: {error_text}")

                    return {
                        'success': False,
                        'error': f"API returned status {response.status}: {error_text}"
                    }

        except asyncio.TimeoutError:
            logger.error("Digital Twin API request timeout")
            return {
                'success': False,
                'error': 'Request timeout'
            }
        except Exception as e:
            logger.error(f"Digital Twin API call failed: {str(e)}")
            return {
                'success': False,
                'error': str(e)
            }

    def extract_content_from_response(self, response_data):
        """Extract content from Digital Twin API response"""
        if isinstance(response_data, str):
            return response_data

        if isinstance(response_data, dict):
            # Try common response keys
            content_keys = ['response', 'content', 'answer', 'message', 'text']

            for key in content_keys:
                if key in response_data:
                    content = response_data[key]
                    if isinstance(content, str):
                        return content
                    elif isinstance(content, dict) and 'text' in content:
                        return content['text']

            # Fallback to JSON string
            return json.dumps(response_data, indent=2)

        return str(response_data)

    async def transcription_completed(self, event):
        """Handle voice transcription completion"""
        try:
            voice_id = event.get('voice_id')
            transcription = event.get('transcription')

            logger.info(f"Transcription completed for voice {voice_id}: {transcription[:50]}...")

            # Notify frontend
            await self.send(text_data=json.dumps({
                'type': 'transcription_completed',
                'voice_id': voice_id,
                'transcription': transcription
            }))

            # Process with Digital Twin API
            await self.process_digital_twin_response(transcription)

        except Exception as e:
            logger.error(f"Error processing transcription: {str(e)}", exc_info=True)

    # WebSocket event handlers
    async def chat_message(self, event):
        """Handle chat message events"""
        message = event['message']
        if 'chat_id' in message and message['chat_id'] != str(self.chat_id):
            return

        await self.send(text_data=json.dumps({
            'type': 'message',
            'message': message
        }))

    async def typing_indicator(self, event):
        """Handle typing indicators"""
        await self.send(text_data=json.dumps({
            'type': 'typing_indicator',
            'is_typing': event['is_typing'],
            'user_id': event['user_id']
        }))

    async def read_receipt_update(self, event):
        """Handle read receipt updates"""
        await self.send(text_data=json.dumps({
            'type': 'read_receipt',
            'message_ids': event['message_ids'],
            'user_id': event['user_id']
        }))

    # Database helper methods (unchanged from original)
    @database_sync_to_async
    def user_has_chat_access(self, chat_id, user_id):
        """Check if user has access to the chat"""
        try:
            chat = UserTwinChat.objects.get(id=chat_id)
            return str(chat.user.id) == str(user_id) and chat.user_has_access and chat.twin_is_active
        except UserTwinChat.DoesNotExist:
            return False

    @database_sync_to_async
    def get_twin_data(self, chat_id):
        """Get twin data for API calls"""
        try:
            chat = UserTwinChat.objects.get(id=chat_id)
            twin = chat.twin

            persona_data = twin.persona_data
            if isinstance(persona_data, str):
                try:
                    persona_data = json.loads(persona_data)
                except json.JSONDecodeError:
                    persona_data = {}
            elif not isinstance(persona_data, dict):
                persona_data = {}

            return {
                'id': str(twin.id),
                'name': twin.name,
                'persona_data': persona_data,
                'created_at': twin.created_at,
                'updated_at': twin.updated_at
            }
        except UserTwinChat.DoesNotExist:
            return {
                'id': None,
                'name': 'AI Assistant',
                'persona_data': {}
            }

    @database_sync_to_async
    def get_voice_recording(self, voice_id):
        """Get voice recording by ID"""
        try:
            voice_recording = VoiceRecording.objects.get(id=voice_id)
            return {
                'id': voice_recording.id,
                'duration_seconds': voice_recording.duration_seconds,
                'is_processed': voice_recording.is_processed,
                'transcription': voice_recording.transcription
            }
        except VoiceRecording.DoesNotExist:
            logger.error(f"Voice recording {voice_id} not found")
            return None

    @database_sync_to_async
    def save_user_message(self, chat_id, content, message_type='text', voice_note_id=None, duration_seconds=None, reply_to=None):
        """Save user message to database"""
        try:
            chat = UserTwinChat.objects.get(id=chat_id)
            chat.last_active = timezone.now()
            chat.save(update_fields=['last_active'])

            is_first_message = not Message.objects.filter(chat=chat).exists()

            message_data = {
                'chat': chat,
                'is_from_user': True,
                'message_type': message_type,
                'text_content': content,
                'status': 'sent'
            }

            if reply_to:
                try:
                    reply_message = Message.objects.get(id=reply_to)
                    message_data['reply_to'] = reply_message
                except Message.DoesNotExist:
                    logger.error(f"Reply message with ID {reply_to} not found")

            if voice_note_id and message_type == 'voice':
                try:
                    voice_note = VoiceRecording.objects.get(id=voice_note_id)
                    message_data['voice_note'] = voice_note
                    if duration_seconds:
                        message_data['duration_seconds'] = duration_seconds
                except VoiceRecording.DoesNotExist:
                    logger.error(f"Voice recording with ID {voice_note_id} not found")

            message = Message.objects.create(**message_data)

            return {
                'id': message.id,
                'timestamp': message.created_at,
                'is_first_message': is_first_message
            }
        except UserTwinChat.DoesNotExist:
            logger.error(f"Chat with ID {chat_id} not found")
            raise

    @database_sync_to_async
    def save_twin_message(self, chat_id, content, message_type='text', reply_to=None):
        """Save twin message to database"""
        try:
            chat = UserTwinChat.objects.get(id=chat_id)

            message_data = {
                'chat': chat,
                'is_from_user': False,
                'message_type': message_type,
                'text_content': content,
                'status': 'sent'
            }

            if reply_to:
                try:
                    reply_message = Message.objects.get(id=reply_to)
                    message_data['reply_to'] = reply_message
                except Message.DoesNotExist:
                    logger.error(f"Reply message with ID {reply_to} not found")

            message = Message.objects.create(**message_data)

            return {
                'id': message.id,
                'timestamp': message.created_at
            }
        except UserTwinChat.DoesNotExist:
            logger.error(f"Chat with ID {chat_id} not found")
            raise

    @database_sync_to_async
    def mark_messages_as_read(self, message_ids):
        """Mark messages as read"""
        try:
            Message.objects.filter(id__in=message_ids).update(status='read')
        except Exception as e:
            logger.error(f"Error marking messages as read: {e}")

    @database_sync_to_async
    def update_user_last_seen(self):
        """Update user last seen timestamp"""
        try:
            chat = UserTwinChat.objects.get(id=self.chat_id)
            chat.last_active = timezone.now()
            chat.save(update_fields=['last_active'])
        except UserTwinChat.DoesNotExist:
            logger.warning(f"Chat not found for last seen update: {self.chat_id}")