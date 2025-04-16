from rest_framework import viewsets, status, mixins
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from core import models
from core.models import Twin
from .serializers import TwinSerializer, TwinListSerializer
from .permissions import IsTwinOwnerOrReadOnly, CanCreateTwin
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.throttling import UserRateThrottle, AnonRateThrottle


class TwinCreateThrottle(UserRateThrottle):
    scope = 'twin_create'
    

class TwinViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows digital twins to be viewed or edited.
    """
    queryset = Twin.objects.all()
    serializer_class = TwinSerializer
    permission_classes = [IsAuthenticated, CanCreateTwin]
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ['privacy_setting', 'is_active']

    throttle_classes = [UserRateThrottle]

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ['update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsTwinOwnerOrReadOnly]
        else:
            permission_classes = self.permission_classes
        return [permission() for permission in permission_classes]

    def get_throttles(self):
        if self.action == 'create':
            self.throttle_classes = [TwinCreateThrottle]
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == 'list':
            return TwinListSerializer
        return super().get_serializer_class()

    def get_queryset(self):
        """
        This view should return a list of all twins:
        - For non-authenticated users: only public twins
        - For authenticated users: their own twins + public twins
        - For staff users: all twins
        """
        user = self.request.user

        if not user.is_authenticated:
            return Twin.objects.filter(privacy_setting='public', is_active=True)

        if user.is_staff:
            return Twin.objects.all()

        # Return user's own twins + public twins
        return Twin.objects.filter(
            models.Q(owner=user) |
            models.Q(privacy_setting='public', is_active=True)
        ).distinct()

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        twin = self.get_object()
        twin.is_active = True
        twin.save()
        return Response({'status': 'twin activated'})

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        twin = self.get_object()
        twin.is_active = False
        twin.save()
        return Response({'status': 'twin deactivated'})

    @action(detail=False, methods=['get'])
    def mine(self, request):
        """
        Get only the twins owned by the current user
        """
        twins = Twin.objects.filter(owner=request.user)
        serializer = self.get_serializer(twins, many=True)
        return Response(serializer.data)