from datetime import timedelta
import json
from django.utils import timezone
import requests
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework import permissions
from core.models import MediaFile, Twin, TwinAccess, UserTwinChat
from .serializers import TwinAccessSerializer, TwinSerializer, TwinListSerializer, PersonaDataUpdateSerializer
from .permissions import IsTwinOwnerOrReadOnly, CanCreateTwin, IsTwinOwner
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.throttling import UserRateThrottle
from rest_framework.pagination import PageNumberPagination
import logging
from django.db.models import Q, Count
from rest_framework.exceptions import PermissionDenied
from drf_spectacular.utils import extend_schema, extend_schema_view
from django.shortcuts import get_object_or_404
import os
from PIL import Image
import uuid
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.conf import settings


logger = logging.getLogger(__name__)

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100

class TwinCreateThrottle(UserRateThrottle):
    scope = 'twin_create'
    rate = '10/day'  # Configurable in settings

@extend_schema_view(
    list=extend_schema(
        summary="List accessible twins",
        description="List twins accessible to the current user. Public twins are visible to all, private twins only to their owners.",
        tags=["Twin Management"]
    ),
    retrieve=extend_schema(
        summary="Retrieve a twin",
        description="Retrieve a specific twin. Public twins are visible to all, private twins only to their owners.",
        tags=["Twin Management"]
    ),
    create=extend_schema(
        summary="Create a twin",
        description="Create a new twin. Requires authentication and respects creation limits.",
        tags=["Twin Management"]
    ),
    update=extend_schema(
        summary="Full update a twin",
        description="Update all fields of an existing twin. Only the owner can perform this action.",
        tags=["Twin Management"]
    ),
    partial_update=extend_schema(
        summary="Partial update a twin",
        description="Update specific fields of an existing twin. Only the owner can perform this action.",
        tags=["Twin Management"]
    ),
    destroy=extend_schema(
        summary="Delete a twin",
        description="Delete a twin. Only the owner can perform this action.",
        tags=["Twin Management"]
    ),
)
class TwinViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows digital twins to be viewed or edited.

    ## Search capabilities:
    - Search by twin name with ?search=keyword
    - Search by persona description with ?search=keyword

    ## Filtering options:
    - Filter by privacy_setting with ?privacy_setting=public|private|shared
    - Filter by active status with ?is_active=true|false
    - Filter by owner with ?owner=user_id

    ## Ordering options:
    - Order by created_at, updated_at or name with ?ordering=field_name
    - Use -field_name for descending order

    ## Custom actions:
    - GET /twins/mine/ - List only the current user's twins
    - GET /twins/public/ - List only public twins
    - POST /twins/{id}/toggle_active/ - Toggle the active status
    - PATCH /twins/{id}/update_persona/ - Update just the persona data
    - POST /twins/{id}/duplicate/ - Create a copy of this twin
    - GET /twins/stats/ - Get statistics about twins (admin only)

    ## Authentication:
    - Public twins can be accessed without authentication
    - Private twins require authentication
    - Creating, updating, and deleting twins requires authentication
    """
    queryset = Twin.objects.all()
    permission_classes = [IsAuthenticated, CanCreateTwin]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['privacy_setting', 'is_active', 'owner']
    search_fields = ['name', 'persona_data__persona_description']
    ordering_fields = ['created_at', 'updated_at', 'name']
    ordering = ['-created_at']
    pagination_class = StandardResultsSetPagination
    throttle_classes = [UserRateThrottle]

    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action in ['list', 'mine', 'public']:
            return TwinListSerializer
        return TwinSerializer

    def get_permissions(self):
        """
        Set permissions based on the action:
        - list: Allow anonymous access but filter results
        - retrieve: Check permissions in get_object
        - create: CanCreateTwin
        - mine, toggle_active, update_persona, duplicate: IsTwinOwner
        - update, partial_update, destroy: IsTwinOwnerOrReadOnly
        - public: AllowAny
        - stats: IsAdminUser
        """
        if self.action in ['list', 'retrieve', 'public']:
            # Allow anonymous access for list, retrieve, and public endpoints
            # Actual object visibility will be controlled in get_queryset and get_object
            permission_classes = [AllowAny]
        elif self.action in ['update', 'partial_update', 'destroy']:
            permission_classes = [IsAuthenticated, IsTwinOwnerOrReadOnly]
        elif self.action in ['mine', 'toggle_active', 'update_persona', 'duplicate']:
            permission_classes = [IsAuthenticated, IsTwinOwner]
        elif self.action == 'stats':
            permission_classes = [permissions.IsAdminUser]
        elif self.action == 'create':
            permission_classes = [IsAuthenticated, CanCreateTwin]
        else:
            permission_classes = self.permission_classes
        return [permission() for permission in permission_classes]

    def get_throttles(self):
        """Apply specific throttling to create action"""
        if self.action == 'create':
            self.throttle_classes = [TwinCreateThrottle]
        return super().get_throttles()

    def get_queryset(self):
        """
        Return appropriate queryset based on user and action:
        - For 'list' and related views, filter twins based on user access levels
        - For individual object access (retrieve, update, etc.), don't filter the queryset
          as permissions will be checked in get_object
        """
        queryset = Twin.objects.select_related('owner', 'avatar')
        user = self.request.user

        # Optimize queries by deferring persona_data for list views
        if self.action in ['list', 'mine', 'public']:
            queryset = queryset.defer('persona_data')

            # For anonymous users, return only public active twins
            if not user.is_authenticated:
                return queryset.filter(privacy_setting='public', is_active=True)

            # For staff/admin, return all twins
            if user.is_staff:
                return queryset

            # For authenticated users, return their own twins plus public active ones
            return queryset.filter(
                Q(owner=user) |
                Q(privacy_setting='public', is_active=True)
            ).distinct()

        # For individual object access, return the full queryset
        # (permissions will be checked in get_object)
        return queryset

    def get_object(self):
        """
        Retrieve the object with proper permission checking.
        Admins can view but not modify other users' twins.
        """
        obj = get_object_or_404(Twin, pk=self.kwargs['pk'])
        user = self.request.user
        action = self.action

        # Staff can view any twin
        if user.is_authenticated and user.is_staff:
            if action in ['update', 'partial_update', 'destroy', 'toggle_active', 'update_persona', 'duplicate']:
                # But can't modify unless they're the owner
                if obj.owner != user:
                    raise PermissionDenied("Admins cannot modify other users' twins.")
            return obj

        # Owner can access and modify their own twin
        if user.is_authenticated and obj.owner == user:
            return obj

        # Anyone can view public active twins
        if obj.privacy_setting == 'public' and obj.is_active and action in ['retrieve']:
            return obj

        raise PermissionDenied("You do not have permission to access this twin.")

    def perform_create(self, serializer):
        """
        Set the owner to the current user when creating.
        Then, send data to an external server and update with the returned twin_id.
        """
        # Step 1: Save the Twin instance locally first
        instance = serializer.save(owner=self.request.user)
        logger.info(f"Twin '{instance.name}' (ID: {instance.id}) created locally by user {self.request.user.email}.")

        # Step 2: Prepare data for the external API call
        persona_description = ""
        if isinstance(instance.persona_data, dict):
            persona_description = instance.persona_data.get('persona_description', '')
        elif isinstance(instance.persona_data, str):
            try:
                data = json.loads(instance.persona_data)
                persona_description = data.get('persona_description', '')
            except json.JSONDecodeError:
                logger.warning(f"Could not parse persona_data for Twin ID {instance.id}")

        payload = {
            "name": instance.name,
            "description": persona_description,
            "sentiment": instance.sentiment, # Assuming sentiment is part of the initial creation data
                                            # or has a default.
        }

        # Step 3: Make the POST request to the external server
        external_api_url = getattr(settings, 'EXTERNAL_TWIN_CREATION_API_URL', None)

        if not external_api_url:
            logger.error("EXTERNAL_TWIN_CREATION_API_URL not configured in settings. Skipping external call.")
            # Decide how to handle this:
            # - Let the twin be created without external_twin_id (current behavior)
            # - Raise an exception or return an error response to the client
            return

        headers = {
            "Content-Type": "application/json",
            # No Authorization header needed if no token
        }

        try:
            logger.info(f"Sending data to external API for Twin ID {instance.id}: {payload}")
            response = requests.post(external_api_url, json=payload, headers=headers, timeout=10) # 10 second timeout
            response.raise_for_status()  # This will raise an HTTPError for bad responses (4XX or 5XX)

            # Step 4: Process the response and update the local Twin
            response_data = response.json()
            external_twin_id = response_data.get('twin_id') # Adjust key if different

            if external_twin_id:
                instance.twin_id = external_twin_id
                instance.save(update_fields=['twin_id'])
                logger.info(f"Twin ID {instance.id} updated with external_twin_id: {external_twin_id}")
            else:
                logger.warning(f"External API call successful for Twin ID {instance.id}, but no 'twin_id' received in response: {response_data}")

        except requests.exceptions.Timeout:
            logger.error(f"Timeout occurred when calling external twin API for Twin ID {instance.id}.")
            # Handle timeout: maybe schedule a retry later (e.g., with Celery)
        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error occurred when calling external twin API for Twin ID {instance.id}: {http_err} - Response: {response.text}")
            # Handle HTTP errors from the external server
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling external twin API for Twin ID {instance.id}: {e}")
            # Handle other network errors or issues with the request itself
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            logger.error(f"Error parsing response or missing key from external twin API for Twin ID {instance.id}: {e}. Response content: {response.text if 'response' in locals() else 'N/A'}")

    @extend_schema(
        summary="Toggle twin status",
        description="Toggle the active status of a twin. Only the owner can perform this action.",
        tags=["Twin Status"]
    )
    @action(detail=True, methods=['post'])
    def toggle_active(self, request, pk=None):
        """Toggle the active status of a twin"""
        twin = self.get_object()
        twin.is_active = not twin.is_active
        twin.save()
        status_msg = 'activated' if twin.is_active else 'deactivated'
        logger.info(f"Twin {twin.id} {status_msg} by user {request.user.id}")
        return Response({'status': f'Twin {status_msg}', 'is_active': twin.is_active},
                        status=status.HTTP_200_OK)

    @extend_schema(
        summary="List user's twins",
        description="List all twins owned by the current authenticated user.",
        tags=["User Twins"]
    )
    @action(detail=False, methods=['get'])
    def mine(self, request):
        """Get only the current user's twins"""
        twins = Twin.objects.filter(owner=request.user).select_related('owner', 'avatar').defer('persona_data')
        page = self.paginate_queryset(twins)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(twins, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="List public twins",
        description="List all public and active twins, accessible to anyone including anonymous users.",
        tags=["Public Twins"]
    )
    @action(detail=False, methods=['get'], permission_classes=[AllowAny])
    def public(self, request):
        """Get only public and active twins - accessible to everyone including anonymous users"""
        twins = Twin.objects.filter(privacy_setting='public', is_active=True).select_related('owner', 'avatar').defer('persona_data')
        page = self.paginate_queryset(twins)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(twins, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Update persona data",
        description="Update just the persona data of a twin. Only the owner can perform this action.",
        tags=["Persona Management"]
    )
    @action(detail=True, methods=['patch'])
    def update_persona(self, request, pk=None):
        """Update just the persona data of a twin"""
        twin = self.get_object()
        serializer = PersonaDataUpdateSerializer(data=request.data)

        if serializer.is_valid():
            # Initialize persona_data if it doesn't exist
            current_data = twin.persona_data.copy() if twin.persona_data else {
                'persona_description': '',
                'conversations': []
            }

            # Update only the fields that were provided
            if 'persona_description' in serializer.validated_data:
                current_data['persona_description'] = serializer.validated_data['persona_description']

            if 'conversations' in serializer.validated_data:
                current_data['conversations'] = serializer.validated_data['conversations']

            twin.persona_data = current_data
            twin.save()
            logger.info(f"Persona data updated for twin {twin.id} by user {request.user.id}")
            return Response(TwinSerializer(twin, context={'request': request}).data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="Duplicate a twin",
        description="Create a copy of an existing twin. The new twin will belong to the current user.",
        tags=["Twin Management"]
    )
    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        """Create a copy of this twin for the current user"""
        original_twin = self.get_object()

        # Create a new twin with the same data but different name
        new_twin = Twin.objects.create(
            name=f"Copy of {original_twin.name}",
            owner=request.user,
            privacy_setting='private',  # Default to private for duplicated twins
            persona_data=original_twin.persona_data,
            avatar=original_twin.avatar,  # Same avatar reference
            is_active=True
        )

        logger.info(f"Twin {original_twin.id} duplicated as {new_twin.id} by user {request.user.id}")
        return Response(
            TwinSerializer(new_twin, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )

    @extend_schema(
        summary="Upload twin avatar",
        description="Upload an image file to be used as the twin's avatar. Only the owner can perform this action.",
        tags=["Twin Management"]
    )
    @action(detail=True, methods=['post'])
    def upload_avatar(self, request, pk=None):
        """Upload a new avatar image for this twin, replacing any existing one"""
        twin = self.get_object()

        if 'image' not in request.FILES:
            return Response({'error': 'No image file provided'}, status=status.HTTP_400_BAD_REQUEST)

        image_file = request.FILES['image']

        # Validate file type
        allowed_types = ['image/jpeg', 'image/png', 'image/gif']
        if image_file.content_type not in allowed_types:
            return Response({
                'error': f'Unsupported file type. Allowed types: {", ".join(allowed_types)}'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Validate file size (max 5MB)
        max_size = 5 * 1024 * 1024  # 5MB in bytes
        if image_file.size > max_size:
            return Response({
                'error': f'File too large. Maximum size: 5MB'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Store reference to old avatar
            old_avatar = twin.avatar

            # Process image with Pillow
            img = Image.open(image_file)

            # Resize if needed (optional)
            max_dimension = 500
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension))

            # Generate a unique filename
            filename = f"{uuid.uuid4()}-{image_file.name}"

            # Save to temporary location
            temp_path = f"temp_{filename}"
            img.save(temp_path)

            # Open file and create MediaFile record
            with open(temp_path, 'rb') as f:
                # Save file to storage
                file_path = f"avatars/{filename}"
                path = default_storage.save(file_path, ContentFile(f.read()))

                # Get file size in MB
                size_mb = os.path.getsize(temp_path) / (1024 * 1024)

                # Create MediaFile record
                media_file = MediaFile.objects.create(
                    filename=filename,
                    file_type='image',
                    uploaded_by=request.user,
                    size_mb=size_mb,
                    path=path,
                    is_public=True  # Avatar images are typically public
                )

                # Remove temporary file
                os.remove(temp_path)

                # Update twin's avatar
                twin.avatar = media_file
                twin.save()

                # Handle the old avatar after successfully setting the new one
                if old_avatar:
                    try:
                        # Delete the old file from storage
                        default_storage.delete(old_avatar.path)
                        logger.info(f"Deleted old avatar file: {old_avatar.path}")

                        # Delete the old MediaFile record
                        old_avatar.delete()
                        logger.info(f"Deleted old avatar record: {old_avatar.id}")
                    except Exception as e:
                        # Log but don't fail if cleanup fails
                        logger.warning(f"Failed to clean up old avatar: {str(e)}")

                logger.info(f"Avatar updated for twin {twin.id} by user {request.user.id}")
                return Response({
                    'message': 'Avatar updated successfully',
                    'avatar_url': f"/media/{media_file.path}",
                    'avatar': str(media_file.id)
                }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Avatar upload failed: {str(e)}")
            return Response({
                'error': f'Failed to process image: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def perform_create(self, serializer):
        """Set the owner to the current user when creating"""
        # Simply pass the request user as owner
        # The avatar handling is now in the serializer's create method
        serializer.save(owner=self.request.user)
        logger.info(f"Twin created: {serializer.instance.id} by user {self.request.user.id}")

    @extend_schema(
        summary=" Delete twin avatar ",
        description=" Delete an image file to be used as the twin's avatar. Only the owner can perform this action.",
        tags=["Twin Management"]
    )
    @action(detail=True, methods=['delete'])
    def delete_avatar(self, request, pk=None):
        """Remove the avatar from this twin"""
        twin = self.get_object()

        if not twin.avatar:
            return Response({'message': 'This twin does not have an avatar'},
                            status=status.HTTP_404_NOT_FOUND)

        try:
            # Store reference to the avatar
            old_avatar = twin.avatar

            # Remove reference from twin
            twin.avatar = None
            twin.save()

            # Delete the file from storage
            default_storage.delete(old_avatar.path)

            # Delete the MediaFile record
            old_avatar.delete()

            logger.info(f"Avatar deleted for twin {twin.id} by user {request.user.id}")
            return Response({'message': 'Avatar deleted successfully'}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Avatar deletion failed: {str(e)}")
            return Response({
                'error': f'Failed to delete avatar: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @extend_schema(
        summary="Get twin statistics",
        description="Get statistics about twins. Admin-only endpoint.",
        tags=["Admin"]
    )
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get statistics about twins (admin only)"""
        # Count twins by privacy setting
        privacy_counts = Twin.objects.values('privacy_setting').annotate(count=Count('id'))

        # Count active vs inactive twins
        active_count = Twin.objects.filter(is_active=True).count()
        inactive_count = Twin.objects.filter(is_active=False).count()

        # Count twins by creation date (last 30 days)
        last_30_days = timezone.now() - timedelta(days=30)
        recent_twins = Twin.objects.filter(created_at__gte=last_30_days).count()

        return Response({
            'total_twins': Twin.objects.count(),
            'privacy_distribution': privacy_counts,
            'active_twins': active_count,
            'inactive_twins': inactive_count,
            'created_last_30_days': recent_twins
        })

    @action(detail=True, methods=['post'], url_path='share')
    def share_twin(self, request, pk=None):
        """Share a twin with another user by email"""
        twin = self.get_object()

        # Only twin owner can share it
        if twin.owner != request.user:
            raise PermissionDenied("Only the twin owner can share access")

        # Set twin to shared mode if it's private
        if twin.privacy_setting == 'private':
            twin.privacy_setting = 'shared'
            twin.save()

        serializer = TwinAccessSerializer(data=request.data)
        if serializer.is_valid():
            # Get user from email
            user_email = serializer.validated_data['user_email']
            User = get_user_model()
            target_user = User.objects.get(email=user_email)

            # Don't allow sharing with yourself
            if target_user == request.user:
                return Response(
                    {"detail": "You already own this twin"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Calculate expiration date
            expires_in_days = serializer.validated_data.get('expires_in_days', 30)
            expiration_date = timezone.now() + timedelta(days=expires_in_days)

            # Create or update access permission
            access, created = TwinAccess.objects.update_or_create(
                user=target_user,
                twin=twin,
                defaults={'grant_expires': expiration_date}
            )

            # Create a new chat for the target user with this twin
            chat = UserTwinChat.objects.create(
                user=target_user,
                twin=twin,
            )

            # Send email notification
            subject = f"Digital Twin Access Shared: {twin.name}"
            message = f"""
            Hello {target_user.get_full_name() or target_user.username},

            {request.user.get_full_name() or request.user.username} has shared their digital twin "{twin.name}" with you.
            You now have access until {expiration_date.strftime('%Y-%m-%d')}.
            A chat has been created for you to interact with this twin.

            You can access the twin through your dashboard.

            Best regards,
            Digital Twin Team
            """

            try:
                send_mail(
                    subject,
                    message,
                    settings.EMAIL_HOST_USER,
                    [user_email],
                    fail_silently=False,
                )
                logger.info(f"Share notification email sent to {user_email} for twin {twin.id}")
            except Exception as e:
                logger.error(f"Failed to send share notification email: {str(e)}")
                # Continue execution even if email fails

            return Response({
                "detail": "Access granted successfully",
                "access": TwinAccessSerializer(access).data,
                "chat_id": chat.id
            }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['get'], url_path='access-list')
    def access_list(self, request, pk=None):
        """List all users with access to this twin"""
        twin = self.get_object()

        # Only twin owner can view the access list
        if twin.owner != request.user:
            raise PermissionDenied("Only the twin owner can view access permissions")

        # Get all active access grants
        accesses = TwinAccess.objects.filter(
            twin=twin,
            grant_expires__gt=timezone.now()
        ).select_related('user')

        serializer = TwinAccessSerializer(accesses, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['delete'], url_path='revoke-access/(?P<user_id>[^/.]+)')
    def revoke_access(self, request, pk=None, user_id=None):
        """Revoke access for a specific user"""
        twin = self.get_object()

        # Only twin owner can revoke access
        if twin.owner != request.user:
            raise PermissionDenied("Only the twin owner can revoke access")

        # Find and delete the access
        try:
            access = TwinAccess.objects.get(twin=twin, user__id=user_id)
            access.delete()

            # If no more accesses exist, optionally revert to private
            remaining_accesses = TwinAccess.objects.filter(twin=twin).exists()
            if not remaining_accesses and twin.privacy_setting == 'shared':
                twin.privacy_setting = 'private'
                twin.save()

            return Response({"detail": "Access revoked successfully"}, status=status.HTTP_200_OK)
        except TwinAccess.DoesNotExist:
            return Response({"detail": "Access not found"}, status=status.HTTP_404_NOT_FOUND)



@extend_schema_view(
    list=extend_schema(
        summary="List shared twins",
        description="List all twins that have been shared with the current user and are still valid (not expired).",
        tags=["Shared Twins"]
    ),
    retrieve=extend_schema(
        summary="Retrieve shared twin",
        description="Retrieve details of a specific twin that has been shared with the current user.",
        tags=["Shared Twins"]
    )
)
class SharedWithMeViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet for twins shared with the current user"""
    serializer_class = TwinSerializer  # Use your existing Twin serializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Return twins that are shared with the current user"""
        return Twin.objects.filter(
            user_accesses__user=self.request.user,
            user_accesses__grant_expires__gt=timezone.now(),
            privacy_setting='shared'
        ).select_related('owner', 'avatar')