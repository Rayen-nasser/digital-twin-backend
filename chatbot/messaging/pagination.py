from rest_framework.pagination import CursorPagination


class MessagePagination(CursorPagination):
    """
    Cursor-based pagination for messages
    More efficient for continuous message loading in chat interfaces
    """
    page_size = 50
    ordering = '-created_at'  # From newest to oldest
    cursor_query_param = 'cursor'