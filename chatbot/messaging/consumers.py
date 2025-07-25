import json
import logging
from datetime import datetime
import re
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from core.models import UserTwinChat, Message, VoiceRecording
from messaging.services.openrouter_service import OpenRouterService
from messaging.services.message_history import MessageHistoryService


logger = logging.getLogger(__name__)

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope["user"]

        if self.user.is_anonymous:
            await self.close()
            return

        self.chat_id = self.scope['url_route']['kwargs']['chat_id']
        self.chat_group_name = f'chat_{self.chat_id}'

        # Log when joining group for debugging
        logger.info(f"User {self.user.id} joining chat group: {self.chat_group_name}")

        # Initialize services
        self.openrouter_service = OpenRouterService()
        self.history_service = MessageHistoryService()

        if not await self.user_has_chat_access(self.chat_id, self.user.id):
            await self.close()
            return

        # Get conversation context to enhance memory
        self.twin_data = await self.get_twin_data(self.chat_id)
        self.openrouter_service.twin_data = self.twin_data  # Pass to service

        # Get comprehensive conversation summary
        self.conversation_summary = await self.history_service.get_conversation_summary(self.chat_id)
        self.openrouter_service.conversation_summary = self.conversation_summary  # Pass to service

        # Ensure that data is serializable
        self.twin_data = self.serialize_data(self.twin_data)
        self.conversation_summary = self.serialize_data(self.conversation_summary)

        await self.channel_layer.group_add(
            self.chat_group_name,
            self.channel_name
        )

        await self.accept()

        # Send connection status to client with enhanced information
        await self.send(text_data=json.dumps({
            'type': 'connection_established',
            'chat_id': self.chat_id,
            'twin_name': self.twin_data.get('name', 'AI Assistant'),
            'conversation_summary': self.conversation_summary,
            'message_count': self.conversation_summary.get('total_messages', 0),
            'last_active': self.conversation_summary.get('last_active'),
        }))

    def serialize_data(self, data):
        """Ensure data is serializable for JSON transmission"""
        if isinstance(data, dict):
            for key, value in data.items():
                if isinstance(value, datetime):
                    data[key] = value.isoformat()
                elif isinstance(value, (dict, list)):
                    data[key] = self.serialize_data(value)
                elif hasattr(value, '__dict__'):
                    data[key] = str(value)
        elif isinstance(data, list):
            for index, value in enumerate(data):
                if isinstance(value, datetime):
                    data[index] = value.isoformat()
                elif isinstance(value, (dict, list)):
                    data[index] = self.serialize_data(value)
                elif hasattr(value, '__dict__'):
                    data[index] = str(value)
        return data

    async def disconnect(self, _):
        if hasattr(self, 'chat_id') and self.chat_id:
            await self.update_user_last_seen()

        if hasattr(self, 'chat_group_name') and self.chat_group_name:
            await self.channel_layer.group_discard(
                self.chat_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        try:
            # Handle empty messages
            if not text_data:
                logger.warning("Received empty message")
                return

            # Handle ping/pong messages
            if text_data.lower() in ['ping', 'heartbeat']:
                await self.send(text_data='pong')
                return

            # Parse JSON with proper error handling
            try:
                if isinstance(text_data, bytes):
                    text_data = text_data.decode('utf-8')

                data = json.loads(text_data)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.error(f"Invalid message received: {str(e)}")
                await self.send_error("Invalid message format - must be valid JSON")
                return

            logger.info(f"Received message: {data}")

            # Ensure we got a dictionary
            if not isinstance(data, dict):
                logger.error(f"Expected dictionary but got {type(data)}")
                await self.send_error("Message must be a JSON object")
                return

            # Now safely process the message
            message_type = data.get('type')
            content = data.get('content', '')

            # Get reply_to field with enhanced handling - check at both top level and data.reply_to
            reply_to = data.get('reply_to')

            # Debug log to help diagnose the issue
            if reply_to:
                logger.info(f"Message is replying to: {reply_to}")

            # Handle different message types
            if message_type == 'typing_indicator':
                await self.handle_typing_indicator(data)
                return

            if message_type == 'read_receipt':
                await self.handle_read_receipt(data)
                return

            if message_type == 'text' or message_type == 'message' or message_type == 'chat_message':
                # Normalize message type handling to ensure consistent behavior
                await self.handle_text_message(content, reply_to)
                return

            # Add handling for voice messages with reply functionality
            if message_type == 'voice':
                voice_id = data.get('voice_id')
                if not voice_id:
                    await self.send_error("Voice ID is required for voice messages")
                    return
                await self.handle_voice_message(voice_id, reply_to)
                return

            # Add handling for file messages
            if message_type == 'file':
                file_id = data.get('file_id')
                file_type = data.get('file_type')
                if not file_id:
                    await self.send_error("File ID is required for file messages")
                    return
                await self.handle_file_message(file_id, file_type, reply_to)
                return

            # Unknown message type
            logger.warning(f"Unknown message type: {message_type}")
            await self.send_error(f"Unknown message type: {message_type}")

        except Exception as e:
            logger.error(f"Error in receive: {str(e)}", exc_info=True)
            await self.send_error("Server error processing your message")

    async def send_error(self, message):
        """Helper to send error messages"""
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

    async def handle_voice_message(self, voice_id, reply_to=None):
        """
        Handle voice messages - expects the voice recording to be already uploaded
        Added support for replying to messages
        """
        try:
            # Verify the voice recording exists
            voice_note = await self.get_voice_recording(voice_id)

            if not voice_note:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'Voice recording not found',
                    'code': 'voice_note_not_found'
                }))
                return

            # Create message in database with voice note reference
            message = await self.save_user_message(
                chat_id=self.chat_id,
                content="",  # Will be updated when transcription completes
                message_type='voice',
                voice_note_id=voice_id,
                duration_seconds=voice_note.get('duration_seconds', 0),
                reply_to=reply_to  # Pass reply_to to save_user_message
            )

            # Broadcast to the group
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'chat_message',
                    'message': {
                        'id': str(message['id']),
                        'text_content': voice_note.get('transcription', ''),  # Empty until transcription is done
                        'message_type': 'voice',
                        'is_from_user': True,
                        'timestamp': message['timestamp'].isoformat(),
                        'status': 'sent',
                        'voice_id': voice_id,
                        'duration_seconds': voice_note.get('duration_seconds', 0),
                        'reply_to': reply_to  # Include reply_to in the message
                    }
                }
            )

            # Rest of the method remains the same...
            # Show typing indicator while waiting for transcription
            if not voice_note.get('is_processed'):
                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {
                        'type': 'typing_indicator',
                        'is_typing': True,
                        'user_id': 'twin'
                    }
                )

            # If transcription is already available, process it immediately
            elif voice_note.get('transcription'):
                # The transcription_completed event will be triggered by
                # VoiceRecordingViewSet._notify_twin_of_transcription
                pass

            # Acknowledge receipt
            await self.send(text_data=json.dumps({
                'type': 'voice_message_received',
                'message_id': str(message['id']),
                'voice_id': voice_id
            }))

        except Exception as e:
            logger.error(f"Error handling voice message: {str(e)}", exc_info=True)
            await self.send_error("Error processing voice message")

    async def handle_file_message(self, file_id, file_type, reply_to=None):
        """
        Handle file messages - expects the file to be already uploaded
        Added support for replying to messages and improved PDF handling
        """
        try:
            # Verify the file exists and get its details
            file_data = await self.get_file_data(file_id)

            if not file_data:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'message': 'File not found',
                    'code': 'file_not_found'
                }))
                return

            # Create message in database with file reference
            message = await self.save_user_message(
                chat_id=self.chat_id,
                content=f"📎 {file_data.get('original_name', 'File')}",
                message_type='file',
                file_id=file_id,
                reply_to=reply_to
            )

            # Broadcast to the group
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'chat_message',
                    'message': {
                        'id': str(message['id']),
                        'text_content': f"📎 {file_data.get('original_name', 'File')}",
                        'message_type': 'file',
                        'is_from_user': True,
                        'timestamp': message['timestamp'].isoformat(),
                        'status': 'sent',
                        'file_id': file_id,
                        'file_type': file_type,
                        'file_name': file_data.get('original_name'),
                        'reply_to': reply_to
                    }
                }
            )

            # For PDF files, we'll wait for the pdf_uploaded event
            if file_data.get('mime_type') == 'application/pdf':
                # Show typing indicator while waiting for PDF processing
                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {
                        'type': 'typing_indicator',
                        'is_typing': True,
                        'user_id': 'twin'
                    }
                )

                # Send acknowledgment that we're processing the PDF
                await self.send(text_data=json.dumps({
                    'type': 'file_message_received',
                    'message_id': str(message['id']),
                    'file_id': file_id,
                    'status': 'processing',
                    'message': 'PDF is being processed...'
                }))

                # The rest of the processing will happen when we receive the pdf_uploaded event
                return

            # For non-PDF files, process normally
            # Show typing indicator while processing file
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'typing_indicator',
                    'is_typing': True,
                    'user_id': 'twin'
                }
            )

            # Process the file with AI
            await self.process_file_with_twin(file_data, reply_to)

            # Acknowledge receipt
            await self.send(text_data=json.dumps({
                'type': 'file_message_received',
                'message_id': str(message['id']),
                'file_id': file_id
            }))

        except Exception as e:
            logger.error(f"Error handling file message: {str(e)}", exc_info=True)
            await self.send_error("Error processing file message")

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
        except Exception as e:
            logger.error(f"Error getting voice recording: {str(e)}", exc_info=True)
            return None

    async def transcription_completed(self, event):
        """
        Handle completed transcription notifications and process the voice message
        """
        try:
            voice_id = event.get('voice_id')
            transcription = event.get('transcription')
            chat_id = event.get('chat_id')

            logger.info(f"Received transcription for voice message {voice_id}: '{transcription[:30]}...'")

            # First, notify the frontend that the transcription is complete
            await self.send(text_data=json.dumps({
                'type': 'transcription_completed',
                'voice_id': voice_id,
                'transcription': transcription
            }))

            # Find the associated message if it exists
            message = await self.get_voice_message(voice_id)

            if not message:
                logger.warning(f"No message found for voice recording {voice_id}")
                return

            # Update the message with the transcription
            await self.update_voice_message_content(message['id'], transcription)

            # Show typing indicator
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'typing_indicator',
                    'is_typing': True,
                    'user_id': 'twin'
                }
            )

            # Get conversation history
            recent_messages = await self.history_service.get_recent_messages(self.chat_id, limit=15)

            # Generate AI response
            messages = await self.openrouter_service.get_conversation_context(recent_messages)

            openrouter_response = await self.openrouter_service.generate_response(
                messages=messages,
                temperature=0.7
            )

            # Process response
            twin_message_content = await self.generate_twin_response(
                openrouter_response,
                self.twin_data.get('persona_data', {}),
                transcription,  # Use the transcription as the user message
                False,  # Not first message
                recent_messages
            )

            # Hide typing indicator
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'typing_indicator',
                    'is_typing': False,
                    'user_id': 'twin'
                }
            )

            # Save and send twin response
            twin_message = await self.save_twin_message(
                chat_id=self.chat_id,
                content=twin_message_content
            )

            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'chat_message',
                    'message': {
                        'id': str(twin_message['id']),
                        'text_content': twin_message_content,
                        'message_type': 'text',
                        'is_from_user': False,
                        'timestamp': twin_message['timestamp'].isoformat(),
                        'status': 'sent',
                    }
                }
            )

        except Exception as e:
            logger.error(f"Error processing transcription: {str(e)}", exc_info=True)
            await self.send_error("Error processing voice message transcription")


    @database_sync_to_async
    def get_voice_message(self, voice_id):
        """Get message associated with a voice recording"""
        try:
            message = Message.objects.filter(voice_note_id=voice_id).first()
            if message:
                return {
                    'id': message.id,
                    'text_content': message.text_content,
                    'chat_id': message.chat_id
                }
            return None
        except Exception as e:
            logger.error(f"Error getting voice message: {str(e)}", exc_info=True)
            return None

    @database_sync_to_async
    def update_voice_message_content(self, message_id, transcription):
        """Update a voice message with its transcription"""
        try:
            message = Message.objects.get(id=message_id)
            message.text_content = transcription
            message.save(update_fields=['text_content'])
            logger.info(f"Updated message {message_id} with transcription")
            return True
        except Message.DoesNotExist:
            logger.error(f"Message {message_id} not found for updating transcription")
            return False
        except Exception as e:
            logger.error(f"Error updating voice message content: {str(e)}", exc_info=True)
            return False

    @database_sync_to_async
    def get_file_data(self, file_id):
        """Get file data by ID"""
        try:
            from core.models import MediaFile
            file_obj = MediaFile.objects.get(id=file_id)
            return {
                'id': file_obj.id,
                'original_name': file_obj.original_name,
                'mime_type': file_obj.mime_type,
                'storage_path': file_obj.storage_path,
                'file_category': file_obj.file_category,
                'size_bytes': file_obj.size_bytes
            }
        except Exception as e:
            logger.error(f"Error getting file data: {str(e)}", exc_info=True)
            return None

    async def process_file_with_twin(self, file_data, reply_to=None):
        """Process file content and generate AI response"""
        try:
            # Get conversation history
            recent_messages = await self.history_service.get_recent_messages(self.chat_id, limit=10)

            # Get file content for AI processing
            file_content = await self.extract_file_content(file_data)

            # Generate AI response with file context
            messages = await self.openrouter_service.get_conversation_context_with_file(
                recent_messages,
                file_data,
                file_content
            )

            openrouter_response = await self.openrouter_service.generate_response(
                messages=messages,
                temperature=0.7,
                model='meta-llama/llama-3.2-90b-vision-instruct'  # Use vision model for files
            )

            # Generate twin response
            twin_message_content = await self.generate_twin_response(
                openrouter_response,
                self.twin_data.get('persona_data', {}),
                f"File: {file_data.get('original_name')}",
                False,
                recent_messages
            )

            # Hide typing indicator
            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'typing_indicator',
                    'is_typing': False,
                    'user_id': 'twin'
                }
            )

            # Save and send twin response
            twin_message = await self.save_twin_message(
                chat_id=self.chat_id,
                content=twin_message_content,
                reply_to=reply_to
            )

            await self.channel_layer.group_send(
                self.chat_group_name,
                {
                    'type': 'chat_message',
                    'message': {
                        'id': str(twin_message['id']),
                        'text_content': twin_message_content,
                        'message_type': 'text',
                        'is_from_user': False,
                        'timestamp': twin_message['timestamp'].isoformat(),
                        'status': 'sent',
                    }
                }
            )

        except Exception as e:
            logger.error(f"Error processing file with twin: {str(e)}", exc_info=True)
            await self.send_error("Error processing file with AI")

    @database_sync_to_async
    def extract_file_content(self, file_data):
        """Extract content from file for AI processing"""
        try:
            from django.core.files.storage import default_storage
            import base64

            file_path = file_data['storage_path']
            mime_type = file_data['mime_type']

            # Read file content
            with default_storage.open(file_path, 'rb') as file:
                file_content = file.read()

            # For images and PDFs, encode as base64
            if mime_type.startswith('image/') or mime_type == 'application/pdf':
                encoded_content = base64.b64encode(file_content).decode('utf-8')
                return {
                    'type': 'base64',
                    'content': encoded_content,
                    'mime_type': mime_type
                }

            # For text files, try to decode as text
            elif mime_type.startswith('text/'):
                try:
                    text_content = file_content.decode('utf-8')
                    return {
                        'type': 'text',
                        'content': text_content,
                        'mime_type': mime_type
                    }
                except UnicodeDecodeError:
                    # If can't decode as text, treat as binary
                    encoded_content = base64.b64encode(file_content).decode('utf-8')
                    return {
                        'type': 'base64',
                        'content': encoded_content,
                        'mime_type': mime_type
                    }

            # For other file types, encode as base64
            else:
                encoded_content = base64.b64encode(file_content).decode('utf-8')
                return {
                    'type': 'base64',
                    'content': encoded_content,
                    'mime_type': mime_type
                }

        except Exception as e:
            logger.error(f"Error extracting file content: {str(e)}", exc_info=True)
            return None

    async def handle_text_message(self, content, reply_to=None):
        """Handle regular text messages with improved context management"""
        # Log the reply_to info for debugging
        if reply_to:
            logger.info(f"Processing text message replying to: {reply_to}")

        # Save user message
        message = await self.save_user_message(
            chat_id=self.chat_id,
            content=content,
            message_type='text',
            reply_to=reply_to
        )

        # Create a complete message object to send
        message_obj = {
            'id': str(message['id']),
            'text_content': content,
            'message_type': 'text',
            'is_from_user': True,
            'timestamp': message['timestamp'].isoformat(),
            'status': 'sent',
            'chat_id': str(self.chat_id)  # Include chat_id for message routing
        }

        # Only include reply_to if it exists to avoid null values
        if reply_to:
            message_obj['reply_to'] = reply_to

        # Broadcast to group with the complete message object
        await self.channel_layer.group_send(
            self.chat_group_name,
            {
                'type': 'chat_message',
                'message': message_obj
            }
        )

        # Process the user's message to get a response from twin
        await self.process_twin_response(content, message['is_first_message'], reply_to)

    async def process_twin_response(self, user_message_content, is_first_message, reply_to=None):
        """Common logic for processing messages and generating twin responses with reply support"""
        # Show typing indicator
        await self.channel_layer.group_send(
            self.chat_group_name,
            {
                'type': 'typing_indicator',
                'is_typing': True,
                'user_id': 'twin'
            }
        )

        # Get message history with enhanced context
        recent_messages = await self.history_service.get_recent_messages(self.chat_id, limit=15)

        # Generate AI response with improved context
        messages = await self.openrouter_service.get_conversation_context(recent_messages)

        # Don't need to manually append the latest message since it's already in recent_messages
        openrouter_response = await self.openrouter_service.generate_response(
            messages=messages,
            temperature=0.7
        )

        # Process response with enhanced context awareness
        twin_message_content = await self.generate_twin_response(
            openrouter_response,
            self.twin_data.get('persona_data', {}),
            user_message_content,
            is_first_message,
            recent_messages
        )

        # Hide typing indicator
        await self.channel_layer.group_send(
            self.chat_group_name,
            {
                'type': 'typing_indicator',
                'is_typing': False,
                'user_id': 'twin'
            }
        )

        # Save and send twin response - now with reply support
        twin_message = await self.save_twin_message(
            chat_id=self.chat_id,
            content=twin_message_content,
            reply_to=reply_to  # Pass the reply_to parameter
        )

        # Create the message object to send
        message_obj = {
            'id': str(twin_message['id']),
            'text_content': twin_message_content,
            'message_type': 'text',
            'is_from_user': False,
            'timestamp': twin_message['timestamp'].isoformat(),
            'status': 'sent'
        }

        # Only include reply_to if it exists
        if reply_to:
            message_obj['reply_to'] = reply_to

        await self.channel_layer.group_send(
            self.chat_group_name,
            {
                'type': 'chat_message',
                'message': message_obj
            }
        )

    async def chat_message(self, event):
            """Handle incoming messages with chat ID validation"""
            message = event['message']

            # Check if the message belongs to the current chat
            if 'chat_id' in message and message['chat_id'] != str(self.chat_id):
                logger.info(f"Ignoring message for chat {message['chat_id']} (current: {self.chat_id})")
                return

            await self.send(text_data=json.dumps({
                'type': 'message',
                'message': message
            }))

    async def disconnect(self, _):
        if hasattr(self, 'chat_id') and self.chat_id:
            await self.update_user_last_seen()

        if hasattr(self, 'chat_group_name') and self.chat_group_name:
            await self.channel_layer.group_discard(
                self.chat_group_name,
                self.channel_name
            )

        # Clear processed voice IDs set
        if hasattr(self, 'processed_voice_ids'):
            self.processed_voice_ids.clear()

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
        """Get enhanced twin data for personalization"""
        try:
            chat = UserTwinChat.objects.get(id=chat_id)
            twin = chat.twin

            # Parse persona_data with robust error handling
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
    def save_user_message(self, chat_id, content, message_type='text', voice_note_id=None, duration_seconds=None, file_id=None, reply_to=None):
        """Save user message to database with reply and file functionality"""
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

            # Add reply_to reference if provided
            if reply_to:
                try:
                    logger.info(f"Adding reply reference to message ID: {reply_to}")
                    reply_message = Message.objects.get(id=reply_to)
                    message_data['reply_to'] = reply_message
                except Message.DoesNotExist:
                    logger.error(f"Reply message with ID {reply_to} not found")
                except Exception as e:
                    logger.error(f"Error setting reply_to reference: {str(e)}")

            # Add voice note reference
            if voice_note_id and message_type == 'voice':
                try:
                    voice_note = VoiceRecording.objects.get(id=voice_note_id)
                    message_data['voice_note'] = voice_note
                    if duration_seconds:
                        message_data['duration_seconds'] = duration_seconds
                except VoiceRecording.DoesNotExist:
                    logger.error(f"Voice recording with ID {voice_note_id} not found")

            # Add file attachment reference if provided
            if file_id and message_type == 'file':
                try:
                    from core.models import MediaFile
                    file_attachment = MediaFile.objects.get(id=file_id)
                    message_data['file_attachment'] = file_attachment
                except MediaFile.DoesNotExist:
                    logger.error(f"File with ID {file_id} not found")

            message = Message.objects.create(**message_data)

            if reply_to:
                logger.info(f"Created message {message.id} as reply to {reply_to}")

            return {
                'id': message.id,
                'timestamp': message.created_at,
                'is_first_message': is_first_message
            }
        except UserTwinChat.DoesNotExist:
            logger.error(f"Chat with ID {chat_id} not found when saving user message.")
            raise

    @database_sync_to_async
    def save_twin_message(self, chat_id, content, message_type='text', reply_to=None):
        """Save twin message to database with reply functionality"""
        try:
            chat = UserTwinChat.objects.get(id=chat_id)

            message_data = {
                'chat': chat,
                'is_from_user': False,
                'message_type': message_type,
                'text_content': content,
                'status': 'sent'
            }

            # Add reply_to reference if provided
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
            logger.error(f"Chat with ID {chat_id} not found when saving twin message.")
            raise

    @database_sync_to_async
    def mark_messages_as_read(self, message_ids):
        """Mark specific messages as read"""
        try:
            Message.objects.filter(id__in=message_ids).update(status='read')
        except Exception as e:
            logger.error(f"Error marking messages as read: {e}")

    @database_sync_to_async
    def update_user_last_seen(self):
        """Update last seen timestamp for the chat"""
        try:
            chat = UserTwinChat.objects.get(id=self.chat_id)
            chat.last_active = timezone.now()
            chat.save(update_fields=['last_active'])
        except UserTwinChat.DoesNotExist:
            logger.warning(f"Chat not found for last seen update: {self.chat_id}")


    async def generate_twin_response(self, openrouter_data, persona_data, user_message, is_first_message, recent_messages=None):
            """
            Process OpenRouter response with enhanced context awareness
            and persona-based response customization
            """
            # Parse persona data safely
            if isinstance(persona_data, str):
                try:
                    persona_data = json.loads(persona_data)
                except json.JSONDecodeError:
                    persona_data = {}
            elif not isinstance(persona_data, dict):
                persona_data = {}

            # Handle API errors
            if isinstance(openrouter_data, dict) and 'error' in openrouter_data:
                logger.error(f"OpenRouter error: {openrouter_data.get('error')}")
                return "I'm having some technical difficulties. Could you try again?"

            # Generate personalized first message greeting
            if is_first_message:
                twin_name = "AI Assistant"
                try:
                    if isinstance(self.twin_data, str):
                        twin_data = json.loads(self.twin_data)
                    else:
                        twin_data = self.twin_data
                    twin_name = twin_data.get('name', twin_name)

                    persona_desc = twin_data.get('persona_data', {}).get('persona_description', '')

                    greeting = f"Hello! I'm {twin_name}."

                    if persona_desc:
                        short_desc = ' '.join(persona_desc.split()[:10])
                        if short_desc:
                            greeting += f" {short_desc}"

                    greeting += " How can I help you today?"
                    return greeting

                except (json.JSONDecodeError, AttributeError):
                    return f"Hello! I'm {twin_name}. How can I help you today?"

            # Process valid API response with persona-aware formatting
            if isinstance(openrouter_data, dict) and openrouter_data.get('choices'):
                try:
                    response_content = openrouter_data['choices'][0]['message']['content']

                    # Apply persona-based style adjustments
                    speaking_style = persona_data.get('speaking_style', '').lower()

                    if "cheerful" in speaking_style:
                        # Add exclamation points for cheerful personas
                        if not any(response_content.endswith(char) for char in ('!', '...', '?')):
                            response_content = response_content.rstrip('.') + '!'

                        # Add more enthusiastic language
                        for dull, lively in [
                            ('good', 'great'),
                            ('nice', 'wonderful'),
                            ('like', 'love'),
                            ('happy', 'thrilled')
                        ]:
                            # Replace whole words case-insensitively
                            response_content = re.sub(
                                rf'\b{re.escape(dull)}\b',
                                lively,
                                response_content,
                                flags=re.IGNORECASE
                            )

                    # Additional speaking style handlers
                    if "formal" in speaking_style:
                        formal_replacements = {
                            r"\bdon't\b": "do not",
                            r"\bcan't\b": "cannot",
                            r"\bwon't\b": "will not",
                            r"\bit's\b": "it is",
                            r"\bi'm\b": "I am"
                        }
                        for pattern, replacement in formal_replacements.items():
                            response_content = re.sub(
                                pattern,
                                replacement,
                                response_content,
                                flags=re.IGNORECASE
                            )

                    return response_content.strip()

                except (KeyError, IndexError, TypeError) as e:
                    logger.error(f"Error processing OpenRouter response: {str(e)}")
                    return "I encountered an issue processing that. Could you rephrase your question?"
            else:
                logger.error("Unexpected OpenRouter response format")
                return "I'm not sure how to respond to that. Let's try another topic."

    async def pdf_uploaded(self, event):
        """
        Handle PDF upload notifications from the external service
        """
        try:
            # First, notify the frontend that the PDF was processed
            await self.send(text_data=json.dumps({
                'type': 'pdf_uploaded',
                'file_id': event.get('file_id'),
                'message_id': event.get('message_id'),
                'status': event.get('status'),
                'twin_id': event.get('twin_id', None),
                'response': event.get('response', {})
            }))

            # If the PDF was successfully processed, generate a twin response
            if event.get('status') == 'success':
                # Show typing indicator
                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {
                        'type': 'typing_indicator',
                        'is_typing': True,
                        'user_id': 'twin'
                    }
                )

                # Get conversation history
                recent_messages = await self.history_service.get_recent_messages(self.chat_id, limit=10)

                # Get the PDF content from the response if available
                pdf_content = event.get('response', {}).get('content', 'PDF document')
                pdf_summary = event.get('response', {}).get('summary', 'PDF document')

                # Generate AI response with PDF context
                messages = await self.openrouter_service.get_conversation_context(recent_messages)

                # Add PDF context to the messages
                messages.append({
                    'role': 'system',
                    'content': f"The user has shared a PDF document. Here's a summary of its content: {pdf_summary}"
                })

                # Generate response
                openrouter_response = await self.openrouter_service.generate_response(
                    messages=messages,
                    temperature=0.7,
                    model='meta-llama/llama-3.2-90b-vision-instruct'  # Use vision model for PDFs
                )

                # Process response
                twin_message_content = await self.generate_twin_response(
                    openrouter_response,
                    self.twin_data.get('persona_data', {}),
                    f"I've shared a PDF document with you.",
                    False,
                    recent_messages
                )

                # Hide typing indicator
                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {
                        'type': 'typing_indicator',
                        'is_typing': False,
                        'user_id': 'twin'
                    }
                )

                # Save and send twin response
                twin_message = await self.save_twin_message(
                    chat_id=self.chat_id,
                    content=twin_message_content
                )

                await self.channel_layer.group_send(
                    self.chat_group_name,
                    {
                        'type': 'chat_message',
                        'message': {
                            'id': str(twin_message['id']),
                            'text_content': twin_message_content,
                            'message_type': 'text',
                            'is_from_user': False,
                            'timestamp': twin_message['timestamp'].isoformat(),
                            'status': 'sent',
                        }
                    }
                )

        except Exception as e:
            logger.error(f"Error handling PDF upload notification: {str(e)}", exc_info=True)
            await self.send_error("Error processing PDF document")
