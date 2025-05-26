# core/tests/test_auth_api.py
import pytest
import uuid
from django.urls import reverse
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient
from datetime import timedelta
from PIL import Image
import io

from core.models import User, AuthToken

# Fixtures for testing
@pytest.fixture
def api_client():
    """Return an API client for testing."""
    return APIClient()

@pytest.fixture
def create_user():
    """Fixture to create a user."""
    def _create_user(username='testuser', email='test@example.com', password='Testpass123!', is_verified=False):
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password
        )
        user.is_verified = is_verified
        user.save()
        return user
    return _create_user

@pytest.fixture
def auth_user(create_user):
    """Create a verified user for authentication tests."""
    return create_user(username='authuser', email='auth@example.com', is_verified=True)

@pytest.fixture
def get_tokens(api_client):
    """Get authentication tokens for a user."""
    def _get_tokens(username, password):
        login_data = {'username': username, 'password': password}
        response = api_client.post(reverse('login'), login_data)
        return response.data if response.status_code == 200 else None
    return _get_tokens

@pytest.fixture
def auth_token(auth_user):
    """Create an auth token for a user."""
    def _create_token(user=auth_user, expires_in_hours=24):
        token = str(uuid.uuid4())
        expires_at = timezone.now() + timedelta(hours=expires_in_hours)
        return AuthToken.objects.create(
            user=user,
            token=token,
            expires_at=expires_at
        )
    return _create_token

@pytest.fixture
def sample_image():
    """Create a sample image for testing profile image upload."""
    def _create_image(format='JPEG', size=(100, 100)):
        image = Image.new('RGB', size, color='red')
        image_file = io.BytesIO()
        image.save(image_file, format=format)
        image_file.seek(0)
        return SimpleUploadedFile(
            name=f'test_image.{format.lower()}',
            content=image_file.read(),
            content_type=f'image/{format.lower()}'
        )
    return _create_image

# Tests
@pytest.mark.django_db
class TestAuthAPI:
    """Tests for authentication API endpoints."""

    # Registration Tests
    def test_user_registration_success(self, api_client):
        """Test successful user registration."""
        url = reverse('register')
        payload = {
            'username': 'newuser',
            'email': 'new@example.com',
            'password': 'NewUserPass123!'
        }
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_201_CREATED
        assert 'user' in response.data
        assert 'message' in response.data

        user = User.objects.filter(email=payload['email']).first()
        assert user is not None
        assert user.is_verified is False
        assert AuthToken.objects.filter(user=user).exists()

    def test_user_registration_with_profile_image(self, api_client, sample_image):
        """Test user registration with profile image."""
        url = reverse('register')
        image = sample_image()
        payload = {
            'username': 'newuser',
            'email': 'new@example.com',
            'password': 'NewUserPass123!',
            'profile_image': image
        }
        response = api_client.post(url, payload, format='multipart')

        assert response.status_code == status.HTTP_201_CREATED
        user = User.objects.filter(email='new@example.com').first()
        assert user.profile_image is not None

    def test_registration_duplicate_username(self, api_client, create_user):
        """Test registration with duplicate username."""
        create_user(username='existing', email='existing@example.com')

        url = reverse('register')
        payload = {
            'username': 'existing',
            'email': 'different@example.com',
            'password': 'NewUserPass123!'
        }
        response = api_client.post(url, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_registration_duplicate_email(self, api_client, create_user):
        """Test registration with duplicate email."""
        create_user(username='existing', email='existing@example.com')

        url = reverse('register')
        payload = {
            'username': 'different',
            'email': 'existing@example.com',
            'password': 'NewUserPass123!'
        }
        response = api_client.post(url, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_registration_invalid_image_size(self, api_client):
        """Test registration with oversized image."""
        # Create a large mock file
        large_image = SimpleUploadedFile(
            name='large_image.jpg',
            content=b'x' * (6 * 1024 * 1024),  # 6MB
            content_type='image/jpeg'
        )

        url = reverse('register')
        payload = {
            'username': 'newuser',
            'email': 'new@example.com',
            'password': 'NewUserPass123!',
            'profile_image': large_image
        }
        response = api_client.post(url, payload, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # Email Verification Tests
    def test_email_verification_success(self, api_client, create_user, auth_token):
        """Test successful email verification."""
        user = create_user()
        token_obj = auth_token(user=user)

        url = reverse('verify_email')
        payload = {'token': token_obj.token}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK
        assert 'tokens' in response.data
        assert 'access' in response.data['tokens']
        assert 'refresh' in response.data['tokens']

        user.refresh_from_db()
        assert user.is_verified is True
        assert not AuthToken.objects.filter(token=token_obj.token).exists()

    def test_email_verification_invalid_token(self, api_client):
        """Test email verification with invalid token."""
        url = reverse('verify_email')
        payload = {'token': 'invalid-token'}
        response = api_client.post(url, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_email_verification_expired_token(self, api_client, create_user, auth_token):
        """Test email verification with expired token."""
        user = create_user()
        token_obj = auth_token(user=user, expires_in_hours=-1)

        url = reverse('verify_email')
        payload = {'token': token_obj.token}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        user.refresh_from_db()
        assert user.is_verified is False

    # Login Tests
    def test_user_login_success(self, api_client, auth_user):
        """Test successful user login."""
        url = reverse('login')
        payload = {
            'username': 'authuser',
            'password': 'Testpass123!'
        }
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK
        assert 'access' in response.data
        assert 'refresh' in response.data
        assert 'user' in response.data

    def test_login_unverified_user(self, api_client, create_user):
        """Test login with unverified user."""
        user = create_user(is_verified=False)
        url = reverse('login')
        payload = {
            'username': user.username,
            'password': 'Testpass123!'
        }
        response = api_client.post(url, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_invalid_credentials(self, api_client, auth_user):
        """Test login with invalid credentials."""
        url = reverse('login')
        payload = {
            'username': 'authuser',
            'password': 'wrongpassword'
        }
        response = api_client.post(url, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # Profile Tests
    def test_get_user_profile(self, api_client, auth_user, get_tokens):
        """Test retrieving user profile."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('user_profile')
        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['email'] == 'auth@example.com'

    def test_update_user_profile(self, api_client, auth_user, get_tokens):
        """Test updating user profile."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('user_profile')
        payload = {'username': 'updateduserprofile'}
        response = api_client.patch(url, payload)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['username'] == 'updateduserprofile'

    def test_delete_user_account(self, api_client, auth_user, get_tokens):
        """Test deleting user account."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('user_profile')
        response = api_client.delete(url)

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not User.objects.filter(id=auth_user.id).exists()

    # Profile Image Tests
    def test_upload_profile_image(self, api_client, auth_user, get_tokens, sample_image):
        """Test uploading profile image."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('profile-image')
        image = sample_image()
        response = api_client.put(url, {'profile_image': image}, format='multipart')

        assert response.status_code == status.HTTP_200_OK
        auth_user.refresh_from_db()
        assert auth_user.profile_image is not None

    def test_upload_profile_image_invalid_format(self, api_client, auth_user, get_tokens):
        """Test uploading invalid image format."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('profile-image')
        invalid_file = SimpleUploadedFile(
            name='test.txt',
            content=b'not an image',
            content_type='text/plain'
        )
        response = api_client.put(url, {'profile_image': invalid_file}, format='multipart')
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_delete_profile_image(self, api_client, auth_user, get_tokens, sample_image):
        """Test deleting profile image."""
        # First upload an image
        auth_user.profile_image = sample_image()
        auth_user.save()

        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('profile-image')
        response = api_client.delete(url)

        assert response.status_code == status.HTTP_200_OK
        auth_user.refresh_from_db()
        assert not auth_user.profile_image

    # Password Change Tests
    def test_change_password_success(self, api_client, auth_user, get_tokens):
        """Test successful password change."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('change_password')
        payload = {
            'old_password': 'Testpass123!',
            'new_password': 'NewPassword123!'
        }
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK

        # Verify new password works
        login_url = reverse('login')
        login_data = {
            'username': 'authuser',
            'password': 'NewPassword123!'
        }
        login_response = api_client.post(login_url, login_data)
        assert login_response.status_code == status.HTTP_200_OK

    def test_change_password_wrong_old_password(self, api_client, auth_user, get_tokens):
        """Test password change with wrong old password."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('change_password')
        payload = {
            'old_password': 'wrongpassword',
            'new_password': 'NewPassword123!'
        }
        response = api_client.post(url, payload)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    # Password Reset Tests
    def test_password_reset_request_success(self, api_client, auth_user):
        """Test successful password reset request."""
        User.objects.create_user('authuser', 'auth@example.com', 'Testpass123!')
        url = reverse('password_reset')
        payload = {'email': 'auth@example.com'}
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer fake_token")  # Requires auth
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK
        assert AuthToken.objects.filter(user=auth_user).exists()

    def test_forgot_password_success(self, api_client, auth_user):
        """Test forgot password (no auth required)."""
        url = reverse('forgot_password')
        payload = {'email': 'auth@example.com'}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK
        assert AuthToken.objects.filter(user=auth_user).exists()

    def test_forgot_password_nonexistent_email(self, api_client):
        """Test forgot password with non-existent email."""
        User.objects.create_user('authuser', 'nonexistent@example.com', 'Testpass123!')
        url = reverse('forgot_password')
        payload = {'email': 'nonexistent@example.com'}
        response = api_client.post(url, payload)

        # Should still return success for security
        assert response.status_code == status.HTTP_200_OK

    def test_verify_reset_token_success(self, api_client, auth_user, auth_token):
        """Test verifying reset token."""
        token_obj = auth_token(user=auth_user)

        url = reverse('verify-token')
        payload = {'token': token_obj.token}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK
        assert 'email' in response.data

    def test_verify_reset_token_invalid(self, api_client):
        """Test verifying invalid reset token."""
        url = reverse('verify-token')
        payload = {'token': 'invalid-token'}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_password_reset_confirm_success(self, api_client, auth_user, auth_token):
        """Test confirming password reset."""
        token_obj = auth_token(user=auth_user)

        url = reverse('password_reset_confirm')
        payload = {
            'token': token_obj.token,
            'new_password': 'ResetPassword123!'
        }
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK

        # Verify new password works
        login_url = reverse('login')
        login_data = {
            'username': 'authuser',
            'password': 'ResetPassword123!'
        }
        login_response = api_client.post(login_url, login_data)
        assert login_response.status_code == status.HTTP_200_OK

        # Token should be deleted
        assert not AuthToken.objects.filter(token=token_obj.token).exists()

    # Token Refresh Tests
    def test_token_refresh_success(self, api_client, auth_user, get_tokens):
        """Test refreshing JWT token."""
        tokens = get_tokens('authuser', 'Testpass123!')

        url = reverse('token_refresh')
        payload = {'refresh': tokens['refresh']}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK
        assert 'access' in response.data
        assert response.data['access'] != tokens['access']

    def test_token_refresh_invalid_token(self, api_client):
        """Test token refresh with invalid token."""
        url = reverse('token_refresh')
        payload = {'refresh': 'invalid_token'}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    # Logout Tests
    def test_logout_success(self, api_client, auth_user, get_tokens):
        """Test successful logout."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('logout')
        payload = {'refresh': tokens['refresh']}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_205_RESET_CONTENT

        # Token should be blacklisted
        refresh_url = reverse('token_refresh')
        refresh_response = api_client.post(refresh_url, {'refresh': tokens['refresh']})
        assert refresh_response.status_code != status.HTTP_200_OK

    # Resend Verification Tests
    def test_resend_verification_success(self, api_client, create_user):
        """Test resending verification email."""
        user = create_user(is_verified=False)

        url = reverse('resend_verification')
        payload = {'email': user.email}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK
        assert AuthToken.objects.filter(user=user).exists()

    def test_resend_verification_already_verified(self, api_client, auth_user):
        """Test resending verification for already verified user."""
        url = reverse('resend_verification')
        payload = {'email': auth_user.email}
        response = api_client.post(url, payload)

        # Should return success but not create token
        assert response.status_code == status.HTTP_200_OK

    # Authentication Required Tests
    def test_protected_endpoint_without_auth(self, api_client):
        """Test accessing protected endpoint without authentication."""
        url = reverse('user_profile')
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_protected_endpoint_invalid_token(self, api_client):
        """Test accessing protected endpoint with invalid token."""
        api_client.credentials(HTTP_AUTHORIZATION="Bearer invalid_token")
        url = reverse('user_profile')
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED