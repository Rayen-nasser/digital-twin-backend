# core/tests/test_auth_api.py
import pytest
import uuid
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from datetime import timedelta

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

# Tests
@pytest.mark.django_db
class TestAuthAPI:
    """Tests for authentication API endpoints."""

    def test_user_registration(self, api_client):
        """Test registering a new user."""
        url = reverse('register')
        payload = {
            'username': 'newuser',
            'email': 'new@example.com',
            'password': 'NewUserPass123!'
        }
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_201_CREATED
        assert 'user' in response.data
        # assert 'verification_token' in response.data

        # Check user exists
        user = User.objects.filter(email=payload['email']).first()
        assert user is not None
        assert user.is_verified is False

    def test_email_verification(self, api_client, create_user, auth_token):
        """Test email verification with a token."""
        user = create_user()
        token_obj = auth_token(user=user)

        url = reverse('verify_email')
        payload = {'token': token_obj.token}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK

        # Verify user is now verified
        user.refresh_from_db()
        assert user.is_verified is True

        # Token should be deleted
        assert not AuthToken.objects.filter(token=token_obj.token).exists()

    def test_user_login(self, api_client, auth_user):
        """Test user login and token retrieval."""
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
        assert response.data['user']['email'] == 'auth@example.com'

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

        # Verify update in database
        auth_user.refresh_from_db()
        assert auth_user.username == 'updateduserprofile'

    def test_change_password(self, api_client, auth_user, get_tokens):
        """Test changing user password."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('change_password')
        payload = {
            'old_password': 'Testpass123!',
            'new_password': 'NewPassword123!'
        }
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK

        # Try login with new password
        login_url = reverse('login')
        login_data = {
            'username': 'authuser',
            'password': 'NewPassword123!'
        }
        login_response = api_client.post(login_url, login_data)
        assert login_response.status_code == status.HTTP_200_OK

    def test_password_reset_request(self, api_client, auth_user):
        """Test requesting a password reset."""
        url = reverse('password_reset')
        payload = {'email': 'auth@example.com'}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK

        # Check token was created
        assert AuthToken.objects.filter(user=auth_user).exists()

    def test_password_reset_confirm(self, api_client, auth_user, auth_token):
        """Test confirming a password reset."""
        token_obj = auth_token(user=auth_user)

        url = reverse('password_reset_confirm')
        payload = {
            'token': token_obj.token,
            'new_password': 'ResetPassword123!'
        }
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK

        # Try login with new password
        login_url = reverse('login')
        login_data = {
            'username': 'authuser',
            'password': 'ResetPassword123!'
        }
        login_response = api_client.post(login_url, login_data)
        assert login_response.status_code == status.HTTP_200_OK

        # Token should be deleted
        assert not AuthToken.objects.filter(token=token_obj.token).exists()

    def test_token_refresh(self, api_client, auth_user, get_tokens):
        """Test refreshing the JWT token."""
        tokens = get_tokens('authuser', 'Testpass123!')

        url = reverse('token_refresh')
        payload = {'refresh': tokens['refresh']}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_200_OK
        assert 'access' in response.data
        assert response.data['access'] != tokens['access']

    def test_logout(self, api_client, auth_user, get_tokens):
        """Test user logout and token blacklisting."""
        tokens = get_tokens('authuser', 'Testpass123!')
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")

        url = reverse('logout')
        payload = {'refresh': tokens['refresh']}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_205_RESET_CONTENT

        # Try to refresh the blacklisted token (should fail)
        refresh_url = reverse('token_refresh')
        refresh_payload = {'refresh': tokens['refresh']}
        refresh_response = api_client.post(refresh_url, refresh_payload)

        assert refresh_response.status_code != status.HTTP_200_OK

    def test_expired_verification_token(self, api_client, create_user, auth_token):
        """Test using an expired verification token."""
        user = create_user()
        # Create expired token (negative hours)
        token_obj = auth_token(user=user, expires_in_hours=-1)

        url = reverse('verify_email')
        payload = {'token': token_obj.token}
        response = api_client.post(url, payload)

        assert response.status_code == status.HTTP_400_BAD_REQUEST

        # User should still be unverified
        user.refresh_from_db()
        assert user.is_verified is False