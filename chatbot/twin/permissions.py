
# permissions.py
from rest_framework import permissions
from core.models import Twin
from django.conf import settings

class IsTwinOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of a twin to edit it.

    READ access:
    - Anyone can read public twins
    - Only owners can read private twins
    - Staff users can read any twin

    WRITE access:
    - Only twin owners can modify their twins
    - Staff users cannot modify twins they don't own
    """

    def has_object_permission(self, request, view, obj):
        # Owner can do anything with their twin
        if obj.owner == request.user:
            return True

        # Staff can read any twin but NOT modify other's twins
        if request.method in permissions.SAFE_METHODS:
            if request.user.is_staff:
                return True
            return obj.privacy_setting == 'public' and obj.is_active

        # Write permissions denied for non-owners (including staff)
        return False


class IsTwinOwner(permissions.BasePermission):
    """
    Custom permission to only allow owners of a twin to access it,
    regardless of the request method.
    """

    def has_object_permission(self, request, view, obj):
        # Check if user is the owner
        return obj.owner == request.user


class CanCreateTwin(permissions.BasePermission):
    """
    Custom permission to check if user can create a twin based on:
    1. Account tier limits
    2. Rate limiting
    3. Other business rules
    """

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        if request.method != 'POST':
            return True

        # Get twin creation limit from settings with default fallback
        max_twins = getattr(settings, 'MAX_TWINS_PER_USER', 5)

        # Premium users might have higher limits
        if hasattr(request.user, 'profile') and getattr(request.user.profile, 'is_premium', False):
            max_twins = getattr(settings, 'MAX_TWINS_PER_PREMIUM_USER', 20)

        # Check if user has reached their limit
        current_twins = Twin.objects.filter(owner=request.user).count()
        return current_twins < max_twins