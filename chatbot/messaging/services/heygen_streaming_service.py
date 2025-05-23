# messaging/services/heygen_streaming_service.py
import requests
import json
import logging
from django.conf import settings
from typing import Optional, Dict, Any, List
from messaging.services.openrouter_service import OpenRouterService
from messaging.services.message_history import MessageHistoryService

logger = logging.getLogger(__name__)

class HeyGenStreamingService:
    """
    Service to integrate HeyGen streaming with Django chat system
    """

    def __init__(self):
        self.streaming_base_url = getattr(
            settings,
            'STREAMING_MICROSERVICE_URL',
            'http://localhost:3001'
        )
        self.timeout = getattr(settings, 'STREAMING_TIMEOUT', 30)
        self.openrouter_service = OpenRouterService()
        self.history_service = MessageHistoryService()

    async def generate_streaming_script(
        self,
        chat_id: str,
        context_messages: int = 10,
        script_type: str = 'summary'
    ) -> Dict[str, Any]:
        """
        Generate a script based on chat conversation for HeyGen streaming
        """
        try:
            # Get recent messages from chat
            recent_messages = await self.history_service.get_recent_messages(
                chat_id,
                limit=context_messages
            )

            if not recent_messages:
                return {
                    'success': False,
                    'error': 'No messages found in chat',
                    'script': None
                }

            # Generate script based on conversation
            script = await self._generate_script_content(
                recent_messages,
                script_type
            )

            return {
                'success': True,
                'script': script,
                'message_count': len(recent_messages),
                'script_type': script_type,
                'chat_id': chat_id
            }

        except Exception as e:
            logger.error(f"Error generating streaming script: {str(e)}")
            return {
                'success': False,
                'error': str(e),
                'script': None
            }

    async def _generate_script_content(
        self,
        messages: List[Dict],
        script_type: str
    ) -> str:
        """
        Generate script content using AI
        """
        try:
            # Format conversation for script generation
            conversation_text = self._format_messages_for_script(messages)

            # Create script generation prompt
            script_prompts = {
                'summary': f"""
                Create a natural, engaging 2-3 minute video script that summarizes this conversation.
                Make it conversational and personable, as if speaking directly to the user.

                Conversation:
                {conversation_text}

                Requirements:
                - Summarize key topics and insights
                - Maintain a warm, helpful tone
                - Include natural pauses and transitions
                - Be suitable for avatar video presentation
                """,

                'continuation': f"""
                Create a 2-3 minute video script that continues this conversation naturally.
                Address open questions and provide additional insights.

                Conversation:
                {conversation_text}

                Requirements:
                - Continue the natural conversation flow
                - Address unanswered questions
                - Provide additional helpful information
                - Feel like a natural follow-up
                """,

                'introduction': f"""
                Create a welcoming 1-2 minute introduction script based on this conversation context.

                Conversation context:
                {conversation_text}

                Requirements:
                - Introduce the AI assistant warmly
                - Set expectations for the discussion
                - Reference upcoming topics
                - Create engagement and rapport
                """
            }

            prompt = script_prompts.get(script_type, script_prompts['summary'])

            # Generate script using AI service
            messages_for_ai = [
                {
                    "role": "system",
                    "content": "You are a professional script writer for AI assistant videos. Create natural, engaging scripts that feel conversational and personal."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ]

            response = await self.openrouter_service.generate_response(
                messages=messages_for_ai,
                temperature=0.7
            )

            if isinstance(response, dict) and response.get('choices'):
                script = response['choices'][0]['message']['content']
                return script.strip()
            else:
                logger.error("Unexpected response format from AI service")
                return self._get_fallback_script(script_type)

        except Exception as e:
            logger.error(f"Error generating script content: {str(e)}")
            return self._get_fallback_script(script_type)

    def _format_messages_for_script(self, messages: List[Dict]) -> str:
        """
        Format messages for script generation
        """
        formatted_messages = []

        for msg in messages[-10:]:  # Use last 10 messages
            sender = "User" if msg.get('is_from_user') else "Assistant"
            content = msg.get('text_content', '').strip()

            if content:
                formatted_messages.append(f"{sender}: {content}")

        return "\n".join(formatted_messages)

    def _get_fallback_script(self, script_type: str) -> str:
        """
        Provide fallback scripts when AI generation fails
        """
        fallback_scripts = {
            'summary': "Hello! I'm your AI assistant, and I'm here to help summarize our conversation and provide you with key insights from our discussion.",
            'continuation': "Let's continue our conversation. I'm here to answer any additional questions you might have and provide further assistance.",
            'introduction': "Hello! I'm your AI assistant, ready to help you with your questions and provide personalized assistance based on your needs."
        }
        return fallback_scripts.get(script_type, fallback_scripts['summary'])

    async def start_streaming_session(
        self,
        script: str,
        avatar_id: str,
        voice_id: str,
        language: str = 'en',
        quality: str = 'high'
    ) -> Dict[str, Any]:
        """
        Start HeyGen streaming session with generated script
        """
        url = f"{self.streaming_base_url}/api/streaming/start"

        payload = {
            'avatar_id': avatar_id,
            'voice_id': voice_id,
            'text': script,
            'language': language,
            'quality': quality
        }

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self.timeout,
                headers={'Content-Type': 'application/json'}
            )
            response.raise_for_status()

            result = response.json()

            if result.get('success'):
                logger.info(f"HeyGen streaming started: {result.get('sessionId')}")
                return {
                    'success': True,
                    'session_id': result.get('sessionId'),
                    'livekit_url': result.get('livekit_url'),
                    'livekit_token': result.get('livekit_token'),
                    'message': 'Streaming session started successfully'
                }
            else:
                return {
                    'success': False,
                    'error': result.get('error', 'Failed to start streaming')
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to start HeyGen streaming: {str(e)}")
            return {
                'success': False,
                'error': f"Failed to start streaming: {str(e)}"
            }

    def send_text_to_stream(self, session_id: str, text: str) -> Dict[str, Any]:
        """
        Send additional text to existing streaming session
        """
        url = f"{self.streaming_base_url}/api/streaming/speak"

        payload = {
            'sessionId': session_id,
            'text': text
        }

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self.timeout,
                headers={'Content-Type': 'application/json'}
            )
            response.raise_for_status()

            result = response.json()

            if result.get('success'):
                logger.info(f"Text sent to HeyGen stream {session_id}")
                return {
                    'success': True,
                    'message': 'Text sent successfully',
                    'task_id': result.get('task_id')
                }
            else:
                return {
                    'success': False,
                    'error': result.get('error', 'Failed to send text')
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send text to HeyGen stream: {str(e)}")
            return {
                'success': False,
                'error': f"Failed to send text: {str(e)}"
            }

    def stop_streaming_session(self, session_id: str) -> Dict[str, Any]:
        """
        Stop HeyGen streaming session
        """
        url = f"{self.streaming_base_url}/api/streaming/stop"

        payload = {
            'sessionId': session_id
        }

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=self.timeout,
                headers={'Content-Type': 'application/json'}
            )
            response.raise_for_status()

            result = response.json()

            if result.get('success'):
                logger.info(f"HeyGen streaming session stopped: {session_id}")
                return {
                    'success': True,
                    'message': 'Session stopped successfully'
                }
            else:
                return {
                    'success': False,
                    'error': result.get('error', 'Failed to stop session')
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to stop HeyGen streaming: {str(e)}")
            return {
                'success': False,
                'error': f"Failed to stop session: {str(e)}"
            }

    def get_active_sessions(self) -> Dict[str, Any]:
        """
        Get active HeyGen streaming sessions
        """
        url = f"{self.streaming_base_url}/api/streaming/sessions"

        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            result = response.json()

            if result.get('success'):
                return {
                    'success': True,
                    'sessions': result.get('sessions', []),
                    'total': result.get('total', 0)
                }
            else:
                return {
                    'success': False,
                    'error': 'Failed to get sessions'
                }

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get active sessions: {str(e)}")
            return {
                'success': False,
                'error': f"Failed to get sessions: {str(e)}"
            }

    def health_check(self) -> Dict[str, Any]:
        """
        Check if streaming microservice is healthy
        """
        url = f"{self.streaming_base_url}/health"

        try:
            response = requests.get(url, timeout=5)
            result = response.json()

            return {
                'healthy': response.status_code == 200,
                'service_url': self.streaming_base_url,
                'status': result.get('status', 'unknown'),
                'timestamp': result.get('timestamp')
            }
        except Exception as e:
            return {
                'healthy': False,
                'service_url': self.streaming_base_url,
                'error': str(e)
            }


class HeyGenStreamingError(Exception):
    """Custom exception for HeyGen streaming errors"""
    pass