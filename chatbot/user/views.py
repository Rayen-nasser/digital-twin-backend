# user/views.py
from rest_framework import generics, status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils import timezone
import uuid
from datetime import timedelta

from core.models import User, AuthToken
from .serializers import (
    UserSerializer,
    CustomTokenObtainPairSerializer,
    ChangePasswordSerializer,
    EmailVerificationSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer
)

# User registration
class RegisterView(generics.CreateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        if serializer.is_valid():
            user = serializer.save()

            # Generate verification token
            token = str(uuid.uuid4())
            expires_at = timezone.now() + timedelta(hours=24)
            AuthToken.objects.create(
                user=user,
                token=token,
                expires_at=expires_at
            )

            # TODO: In a real app, you would send an email with the verification link here
            # For now, just return the token in the response

            return Response({
                'user': UserSerializer(user).data,
                'message': 'User registered successfully. Please verify your email.',
                'verification_token': token  # In production, you'd send this via email
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Custom login view
class LoginView(TokenObtainPairView):
    serializer_class = CustomTokenObtainPairSerializer


# User profile
class UserProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


# Email verification
class EmailVerificationView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = EmailVerificationSerializer(data=request.data)
        if serializer.is_valid():
            token = serializer.validated_data['token']

            # Find token in database
            auth_token = AuthToken.objects.filter(
                token=token,
                expires_at__gt=timezone.now()
            ).first()

            if not auth_token:
                return Response({'error': 'Invalid or expired token'}, status=status.HTTP_400_BAD_REQUEST)

            user = auth_token.user
            if not user.is_verified:
                user.is_verified = True
                user.save()

            # Delete the used token
            if auth_token and auth_token.id:
                auth_token.delete()

            return Response({'message': 'Email verified successfully'}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Password change
class ChangePasswordView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        user = request.user

        if serializer.is_valid():
            # Check old password
            if not user.check_password(serializer.validated_data['old_password']):
                return Response({'error': 'Wrong password'}, status=status.HTTP_400_BAD_REQUEST)

            # Set new password
            user.set_password(serializer.validated_data['new_password'])
            user.save()

            return Response({'message': 'Password changed successfully'}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Password reset request
class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)

        if serializer.is_valid():
            email = serializer.validated_data['email']
            user = User.objects.filter(email=email).first()

            if user:
                # Generate password reset token
                token = str(uuid.uuid4())
                expires_at = timezone.now() + timedelta(hours=1)

                # Delete any existing tokens for this user
                AuthToken.objects.filter(user=user).delete()

                # Create new token
                AuthToken.objects.create(
                    user=user,
                    token=token,
                    expires_at=expires_at
                )

                # In a real app, you would send an email with the reset link here
                # For now, just return the token in the response

                return Response({
                    'message': 'Password reset email sent',
                    'reset_token': token  # In production, you'd send this via email
                }, status=status.HTTP_200_OK)

            # Always return success even if email not found to prevent email enumeration
            return Response({
                'message': 'If a user with this email exists, a password reset link has been sent'
            }, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Password reset confirmation
class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)

        if serializer.is_valid():
            token = serializer.validated_data['token']
            new_password = serializer.validated_data['new_password']

            # Find token in database
            auth_token = AuthToken.objects.filter(
                token=token,
                expires_at__gt=timezone.now()
            ).first()

            if not auth_token:
                return Response({'error': 'Invalid or expired token'}, status=status.HTTP_400_BAD_REQUEST)

            user = auth_token.user
            user.set_password(new_password)
            user.save()

            # Delete the used token
            auth_token.delete()

            return Response({'message': 'Password reset successful'}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Logout view
class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response({"message": "Logout successful"}, status=status.HTTP_205_RESET_CONTENT)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)