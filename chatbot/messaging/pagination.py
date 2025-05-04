from django.db.models import Prefetch
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response

class MessagePagination(CursorPagination):
    """
    Cursor-based pagination optimized for chat message retrieval
    """
    page_size = 25
    max_page_size = 100
    page_size_query_param = 'page_size'
    ordering = '-created_at'  # Default to show newest messages first
    cursor_query_param = 'cursor'

    def paginate_queryset(self, queryset, request, view=None):
        """
        Store the request for later use in get_paginated_response
        """
        self.request = request  # Store the request
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        # Enhanced paginated response with additional chat context
        response = super().get_paginated_response(data)

        # If we have a chat_id and at least one message
        if hasattr(self, 'request') and self.request and data:
            chat_id = self.request.query_params.get('chat')
            if chat_id:
                try:
                    # Import locally to avoid circular imports
                    from core.models import UserTwinChat
                    from .serializers import UserTwinChatSerializer

                    chat = UserTwinChat.objects.select_related('twin').get(
                        id=chat_id,
                        user=self.request.user
                    )
                    # Add chat context data
                    serializer = UserTwinChatSerializer(
                        chat,
                        context={'request': self.request}
                    )
                    response.data['chat_context'] = {
                        'twin_name': chat.twin.name,
                        'avatar_url': serializer.get_twin(chat).get('avatar_url'),
                        'last_active': chat.last_active.isoformat() if chat.last_active else None
                    }
                except (UserTwinChat.DoesNotExist, ImportError):
                    pass

        return response