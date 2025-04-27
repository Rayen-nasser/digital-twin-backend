from rest_framework import permissions
from core.models import UserTwinChat


class IsChatParticipant(permissions.BasePermission):
    """
    Custom permission to only allow participants to access a chat.
    """
    def has_object_permission(self, request, view, obj):
        # Check if the user is a participant in the chat
        if hasattr(obj, 'chat'):  # For Message objects
            return obj.chat.user == request.user

        # For UserTwinChat objects
        return obj.user == request.user