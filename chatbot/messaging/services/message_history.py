from channels.db import database_sync_to_async
from core.models import Message, UserTwinChat, Twin
from django.utils import timezone
from django.db.models import F, Window, Count, Q
from django.db.models.functions import RowNumber
from datetime import timedelta
import logging
import json

logger = logging.getLogger(__name__)

class MessageHistoryService:
    """
    Service for managing message history for conversations
    """

    @staticmethod
    @database_sync_to_async
    def get_recent_messages(chat_id, limit=10):
        """
        Get recent messages for a chat

        Args:
            chat_id: The ID of the chat
            limit: Maximum number of messages to retrieve

        Returns:
            list: Recent messages ordered chronologically
        """
        try:
            # Note: We retrieve in reverse order with '-created_at' then reverse
            # the result to get chronological order
            messages = list(Message.objects.filter(
                chat_id=chat_id
            ).order_by('-created_at')[:limit])

            # Reverse to get chronological order (oldest first)
            messages.reverse()

            return messages
        except Exception as e:
            logger.error(f"Error retrieving message history: {e}")
            return []

    @staticmethod
    @database_sync_to_async
    def get_conversation_summary(chat_id):
        """
        Get a summary of the conversation

        Args:
            chat_id: The ID of the chat

        Returns:
            dict: Conversation summary data
        """
        try:
            # Get the chat object
            chat = UserTwinChat.objects.get(id=chat_id)

            # Get message counts
            total_count = Message.objects.filter(chat_id=chat_id).count()
            user_message_count = Message.objects.filter(
                chat_id=chat_id,
                is_from_user=True
            ).count()
            twin_message_count = Message.objects.filter(
                chat_id=chat_id,
                is_from_user=False
            ).count()

            # Get conversation duration
            first_message = Message.objects.filter(
                chat_id=chat_id
            ).order_by('created_at').first()

            # Get recent activity patterns
            recent_date = timezone.now() - timedelta(days=7)
            recent_activity = Message.objects.filter(
                chat_id=chat_id,
                created_at__gte=recent_date
            ).count()

            # Get twin persona data
            persona_description = ""
            try:
                persona_data = chat.twin.persona_data
                if isinstance(persona_data, str):
                    persona_data = json.loads(persona_data)

                persona_description = persona_data.get('persona_description', '')
            except (json.JSONDecodeError, AttributeError, KeyError):
                logger.warning(f"Could not parse persona data for twin {chat.twin.id}")

            return {
                'twin_name': chat.twin.name,
                'started_at': first_message.created_at if first_message else None,
                'total_messages': total_count,
                'user_messages': user_message_count,
                'twin_messages': twin_message_count,
                'recent_activity': recent_activity,
                'persona': persona_description
            }
        except Exception as e:
            logger.error(f"Error creating conversation summary: {e}", exc_info=True)
            return {}

    @staticmethod
    @database_sync_to_async
    def get_unread_message_count(chat_id, for_user=True):
        """
        Get count of unread messages in a chat

        Args:
            chat_id: The ID of the chat
            for_user: If True, count unread messages from twin to user
                     If False, count unread messages from user to twin

        Returns:
            int: Number of unread messages
        """
        try:
            return Message.objects.filter(
                chat_id=chat_id,
                is_from_user=not for_user,  # Invert based on whose unread messages we want
                status__in=['sent', 'delivered']  # Messages not marked as 'read'
            ).count()
        except Exception as e:
            logger.error(f"Error counting unread messages: {e}")
            return 0

    @staticmethod
    @database_sync_to_async
    def get_unread_counts_by_chat(user_id):
        """
        Get unread message counts for all chats of a user

        Args:
            user_id: The ID of the user

        Returns:
            dict: Chat ID mapped to unread count
        """
        try:
            user_chats = UserTwinChat.objects.filter(user_id=user_id)
            result = {}

            for chat in user_chats:
                unread_count = Message.objects.filter(
                    chat_id=chat.id,
                    is_from_user=False,  # From twin to user
                    status__in=['sent', 'delivered']
                ).count()
                result[str(chat.id)] = unread_count

            return result
        except Exception as e:
            logger.error(f"Error getting unread counts by chat: {e}")
            return {}

    @staticmethod
    @database_sync_to_async
    def search_messages(chat_id, query, limit=20):
        """
        Search messages in a chat for specific text

        Args:
            chat_id: The ID of the chat
            query: Search text
            limit: Maximum number of matches to return

        Returns:
            list: Messages matching the search query
        """
        try:
            return list(Message.objects.filter(
                chat_id=chat_id,
                text_content__icontains=query
            ).order_by('-created_at')[:limit])
        except Exception as e:
            logger.error(f"Error searching messages: {e}")
            return []

    @staticmethod
    @database_sync_to_async
    def get_recent_conversations(user_id, limit=10):
        """
        Get most recent message from each conversation

        Args:
            user_id: User ID to get conversations for
            limit: Maximum number of conversations to return

        Returns:
            list: List of most recent messages, one per conversation
        """
        try:
            user_chats = UserTwinChat.objects.filter(user_id=user_id)

            # Use window function to get most recent message per chat
            recent_messages = Message.objects.filter(
                chat__in=user_chats
            ).annotate(
                row_num=Window(
                    expression=RowNumber(),
                    partition_by=[F('chat')],
                    order_by=F('created_at').desc(),
                )
            ).filter(row_num=1).order_by('-created_at')[:limit]

            return list(recent_messages)
        except Exception as e:
            logger.error(f"Error getting recent conversations: {e}", exc_info=True)
            return []

    @staticmethod
    @database_sync_to_async
    def get_message_stats(twin_id, time_period_days=30):
        """
        Get message statistics for a specific digital twin

        Args:
            twin_id: The ID of the twin
            time_period_days: Time period in days for the stats

        Returns:
            dict: Message statistics
        """
        try:
            # Define the start date for the time period
            start_date = timezone.now() - timedelta(days=time_period_days)

            # Get all chats for this twin
            chats = UserTwinChat.objects.filter(twin_id=twin_id)
            chat_ids = [chat.id for chat in chats]

            # Count total messages in the time period
            total_messages = Message.objects.filter(
                chat_id__in=chat_ids,
                created_at__gte=start_date
            ).count()

            # Count messages by type
            text_messages = Message.objects.filter(
                chat_id__in=chat_ids,
                created_at__gte=start_date,
                message_type='text'
            ).count()

            voice_messages = Message.objects.filter(
                chat_id__in=chat_ids,
                created_at__gte=start_date,
                message_type='voice'
            ).count()

            file_messages = Message.objects.filter(
                chat_id__in=chat_ids,
                created_at__gte=start_date,
                message_type='file'
            ).count()

            # Count user vs twin messages
            user_messages = Message.objects.filter(
                chat_id__in=chat_ids,
                created_at__gte=start_date,
                is_from_user=True
            ).count()

            twin_messages = Message.objects.filter(
                chat_id__in=chat_ids,
                created_at__gte=start_date,
                is_from_user=False
            ).count()

            # Count distinct users who interacted with this twin
            distinct_users = UserTwinChat.objects.filter(
                id__in=chat_ids,
                messages__created_at__gte=start_date
            ).values('user').distinct().count()

            # Get average response time
            # This is complex and would require more detailed analysis in a real system

            return {
                'total_messages': total_messages,
                'user_messages': user_messages,
                'twin_messages': twin_messages,
                'text_messages': text_messages,
                'voice_messages': voice_messages,
                'file_messages': file_messages,
                'distinct_users': distinct_users,
                'time_period_days': time_period_days
            }
        except Exception as e:
            logger.error(f"Error getting message stats: {e}", exc_info=True)
            return {
                'total_messages': 0,
                'user_messages': 0,
                'twin_messages': 0,
                'text_messages': 0,
                'voice_messages': 0,
                'file_messages': 0,
                'distinct_users': 0,
                'time_period_days': time_period_days,
                'error': str(e)
            }

    @staticmethod
    @database_sync_to_async
    def mark_messages_as_read(chat_id, message_ids=None, is_from_user=False):
        """
        Mark messages as read

        Args:
            chat_id: The ID of the chat
            message_ids: Optional list of specific message IDs to mark as read
            is_from_user: Whether to mark messages from user (False) or twin (True)

        Returns:
            int: Number of messages marked as read
        """
        try:
            query = Message.objects.filter(
                chat_id=chat_id,
                is_from_user=is_from_user,
                status__in=['sent', 'delivered']
            )

            if message_ids:
                query = query.filter(id__in=message_ids)

            updated_count = query.update(
                status='read',
                status_updated_at=timezone.now()
            )

            return updated_count
        except Exception as e:
            logger.error(f"Error marking messages as read: {e}")
            return 0

    @staticmethod
    @database_sync_to_async
    def get_messages_with_media(chat_id, media_type=None, limit=20):
        """
        Get messages with media attachments

        Args:
            chat_id: The ID of the chat
            media_type: Optional filter by media type (image, document, audio)
            limit: Maximum number of messages to return

        Returns:
            list: Messages with media attachments
        """
        try:
            query = Message.objects.filter(chat_id=chat_id)

            # Filter messages with either voice notes or file attachments
            query = query.filter(
                (
                    ~Q(voice_note=None) |  # Has voice note
                    ~Q(file_attachment=None)  # Has file attachment
                )
            )

            if media_type:
                # Further filter by media type
                if media_type == 'voice':
                    query = query.filter(~Q(voice_note=None), message_type='voice')
                else:
                    query = query.filter(
                        Q(file_attachment__isnull=False),
                        message_type='file',
                        file_attachment__file_category=media_type
                    )

            return list(query.order_by('-created_at')[:limit])
        except Exception as e:
            logger.error(f"Error getting messages with media: {e}")
            return []