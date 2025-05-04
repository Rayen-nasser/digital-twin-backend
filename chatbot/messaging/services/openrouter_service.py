import aiohttp
import json
import logging
import os
from typing import Dict, List, Optional, Any
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load environment variables from .env file
load_dotenv()

class OpenRouterService:
    """
    Service class for interacting with OpenRouter's API asynchronously
    """
    def __init__(self):
        self.api_key = os.getenv('OPENROUTER_API_KEY')
        self.base_url = 'https://openrouter.ai/api/v1/chat/completions'
        self.default_model = 'meta-llama/llama-3-8b-instruct'
        self.twin_data = None
        self.max_context_length = 15  # Increased from 5 to improve conversation memory

    async def generate_response(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 300
    ) -> Dict[str, Any]:
        """
        Generate a response using OpenRouter's API

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Optional model override
            temperature: Creativity parameter (0-1)
            max_tokens: Maximum length of response

        Returns:
            Dict containing the API response
        """
        if not self.api_key:
            logger.error("OpenRouter API key not configured")
            raise ValueError("OpenRouter API key not configured")

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'HTTP-Referer': 'http://localhost',  # Replace with your domain if deployed
            'Content-Type': 'application/json',
        }

        payload = {
            'model': model or self.default_model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        return await response.json()

                    error_text = await response.text()
                    logger.error(f"OpenRouter API error: {response.status}, {error_text}")
                    return {
                        'error': f"API returned status {response.status}",
                        'status_code': response.status
                    }
        except aiohttp.ClientError as e:
            logger.error(f"OpenRouter connection error: {str(e)}")
            return {'error': f"Connection error: {str(e)}"}

    async def get_conversation_context(self, message_history: List, limit: Optional[int] = None) -> List[Dict[str, str]]:
        """
        Format message history for the AI with improved context handling

        Args:
            message_history: List of message objects
            limit: Maximum number of messages to include (defaults to self.max_context_length)

        Returns:
            List of formatted messages for the AI
        """
        if limit is None:
            limit = self.max_context_length

        formatted_messages = []
        twin_data = self._parse_twin_data()

        # Add system prompt with twin personality
        system_prompt = self._create_system_prompt(twin_data)
        formatted_messages.append({
            'role': 'system',
            'content': system_prompt
        })

        # Add conversation summary if available
        if hasattr(self, 'conversation_summary') and self.conversation_summary:
            summary = self._format_conversation_summary()
            if summary:
                formatted_messages.append({
                    'role': 'system',
                    'content': summary
                })

        # Add recent message history
        for msg in message_history[-limit:]:
            formatted_messages.append({
                'role': 'user' if msg.is_from_user else 'assistant',
                'content': msg.text_content
            })

        return formatted_messages

    def _parse_twin_data(self) -> Dict:
        """Parse twin data ensuring it's a dictionary format"""
        twin_data = getattr(self, 'twin_data', None)
        if not twin_data:
            return {'name': 'AI Assistant', 'persona_data': {}}

        if isinstance(twin_data, str):
            try:
                twin_data = json.loads(twin_data)
            except json.JSONDecodeError:
                twin_data = {'name': 'AI Assistant', 'persona_data': {}}

        persona = twin_data.get('persona_data', {})
        if isinstance(persona, str):
            try:
                persona = json.loads(persona)
            except json.JSONDecodeError:
                persona = {}

        twin_data['persona_data'] = persona
        return twin_data

    def _create_system_prompt(self, twin_data: Dict) -> str:
        """Create detailed system prompt with personality traits"""
        persona = twin_data.get('persona_data', {})

        # Basic personality information
        name = twin_data.get('name', 'AI Assistant')
        persona_desc = persona.get('persona_description', '')
        speaking_style = persona.get('speaking_style', 'friendly')

        # Additional personality traits if available
        interests = persona.get('interests', '')
        background = persona.get('background', '')
        knowledge = persona.get('knowledge_areas', '')

        # Construct comprehensive system prompt
        prompt_parts = [
            f"You are {name}.",
            persona_desc if persona_desc else None,
            f"Your speaking style is {speaking_style}.",
            f"Your interests include: {interests}" if interests else None,
            f"Your background: {background}" if background else None,
            f"You have knowledge in: {knowledge}" if knowledge else None,
            "Remember past conversations with the user and maintain continuity.",
            (
                "Respond naturally to the user's messages. "
                "Vary your response length based on the context: "
                "Keep it short for greetings or thanks. Be helpful but concise. "
                "Offer detailed help only when the user's question needs it."
            ),
            (
                "Use friendly and relevant emojis to make responses feel warm and human. "
                "For example: 😊 for encouragement, 💪 for motivation, 🌙 for good night, 🙏 for gratitude, ❤️ for love. "
                "Don't overuse them—1 to 2 emojis is usually enough. Only include emojis where it feels natural."
            )
        ]

        # Filter out None values and join remaining parts
        return " ".join(filter(None, prompt_parts))

    def _format_conversation_summary(self) -> str:
        """Format conversation summary for better context"""
        if not hasattr(self, 'conversation_summary') or not self.conversation_summary:
            return ""

        summary = self.conversation_summary
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except json.JSONDecodeError:
                return ""

        # Create a brief summary of the conversation history
        return (
            "Conversation context: "
            f"This conversation with {summary.get('twin_name', 'the user')} "
            f"started on {summary.get('started_at', 'an unknown date')}. "
            f"You've exchanged {summary.get('total_messages', 0)} messages so far. "
            f"Remember to maintain continuity with your past responses."
        )