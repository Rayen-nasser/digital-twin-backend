import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from core.models import Twin, User
from django.utils import timezone
from datetime import timedelta

pytestmark = pytest.mark.django_db

@pytest.fixture
def user():
    return User.objects.create_user(
        username='testuser',
        email='test@example.com',
        password='testpass123'
    )

@pytest.fixture
def another_user():
    return User.objects.create_user(
        username='anotheruser',
        email='another@example.com',
        password='testpass456'
    )

@pytest.fixture
def admin_user():
    return User.objects.create_user(
        username='adminuser',
        email='admin@example.com',
        password='adminpass456',
        is_staff=True
    )

@pytest.fixture
def twin(user):
    return Twin.objects.create(
        name='Test Twin',
        owner=user,
        privacy_setting='private',
        persona_data={
            'persona_description': 'This is a test persona',
            'conversations': [
                {'question': 'How are you?', 'answer': 'I am fine!'}
            ]
        }
    )

@pytest.fixture
def public_twin(admin_user):
    return Twin.objects.create(
        name='Public Twin',
        owner=admin_user,
        privacy_setting='public',
        is_active=True,  # Make sure it's active
        persona_data={
            'persona_description': 'This is a public test persona',
            'conversations': []
        }
    )

@pytest.fixture
def api_client():
    return APIClient()


class TestTwinViewSetPermissions:

    def test_anonymous_user_cannot_access_private_twin(self, api_client, twin):
        """Test anonymous user cannot access private twin"""
        url = reverse('twin-detail', kwargs={'pk': twin.id})

        response = api_client.get(url)

        # Should get 403 Forbidden rather than 401 Unauthorized since we're allowing
        # anonymous access but restricting based on object permissions
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_anonymous_user_can_access_public_endpoint(self, api_client, public_twin):
        """Test anonymous user can access public twins endpoint"""
        url = reverse('twin-public')

        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['results']) > 0
        # Verify the public twin is in the results
        twin_ids = [twin['id'] for twin in response.data['results']]
        assert str(public_twin.id) in twin_ids

    def test_anonymous_user_can_access_public_twin(self, api_client, public_twin):
        """Test anonymous user can access public twin directly"""
        url = reverse('twin-detail', kwargs={'pk': public_twin.id})

        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == str(public_twin.id)
        assert response.data['privacy_setting'] == 'public'

    def test_non_owner_cannot_access_private_twin(self, api_client, twin, another_user):
        """Test non-owner cannot access private twin"""
        url = reverse('twin-detail', kwargs={'pk': twin.id})
        api_client.force_authenticate(user=another_user)

        response = api_client.get(url)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_can_access_any_twin(self, api_client, twin, admin_user):
        """Test admin can access any twin"""
        url = reverse('twin-detail', kwargs={'pk': twin.id})
        api_client.force_authenticate(user=admin_user)

        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['id'] == str(twin.id)

    def test_non_owner_cannot_update_twin(self, api_client, twin, another_user):
        """Test non-owner cannot update twin"""
        url = reverse('twin-detail', kwargs={'pk': twin.id})
        api_client.force_authenticate(user=another_user)

        response = api_client.patch(url, {'name': 'Changed Name'})

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_admin_can_view_but_not_modify_other_users_twin(self, api_client, admin_user, twin):
        """Test admin can view but not modify another user's twin"""
        # Admin can view
        url = reverse('twin-detail', kwargs={'pk': twin.id})
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK

        # But cannot update
        update_data = {'name': 'Updated Name'}
        response = api_client.patch(url, update_data, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert "cannot modify" in str(response.data['detail'])

        # And cannot delete
        response = api_client.delete(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_anonymous_user_cannot_create_twin(self, api_client):
        """Test anonymous user cannot create a twin"""
        url = reverse('twin-list')

        data = {
            'name': 'New Twin',
            'privacy_setting': 'public',
            'persona_data': {
                'persona_description': 'New persona',
                'conversations': []
            }
        }

        response = api_client.post(url, data, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestTwinViewSetQueries:

    def test_list_twins_without_persona_data(self, api_client, user, twin, public_twin):
        """Test listing twins doesn't include persona_data"""
        url = reverse('twin-list')
        api_client.force_authenticate(user=user)

        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert 'results' in response.data
        for twin_data in response.data['results']:
            assert 'persona_data' not in twin_data

    def test_retrieve_twin_with_persona_data(self, api_client, user, twin):
        """Test retrieving single twin includes persona_data"""
        url = reverse('twin-detail', kwargs={'pk': twin.id})
        api_client.force_authenticate(user=user)

        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert 'persona_data' in response.data
        assert response.data['persona_data']['persona_description'] == 'This is a test persona'

    def test_mine_endpoint_shows_only_user_twins(self, api_client, user, twin, public_twin):
        """Test 'mine' endpoint filters by owner"""
        url = reverse('twin-mine')
        api_client.force_authenticate(user=user)

        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        twins_data = response.data['results'] if 'results' in response.data else response.data
        assert len(twins_data) == 1  # Should only see user's own twin
        assert str(twins_data[0]['owner']) == str(user.id)
        assert 'persona_data' not in twins_data[0]

    def test_public_endpoint_shows_only_public_twins(self, api_client, user, twin, public_twin):
        """Test 'public' endpoint filters by privacy setting"""
        url = reverse('twin-public')
        api_client.force_authenticate(user=user)

        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        twins_data = response.data['results'] if 'results' in response.data else response.data
        assert len(twins_data) == 1  # Should only see public twins
        assert twins_data[0]['privacy_setting'] == 'public'

    def test_anonymous_user_can_see_only_public_twins_in_list(self, api_client, twin, public_twin):
        """Test anonymous user can only see public twins in list view"""
        url = reverse('twin-list')

        response = api_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        twins_data = response.data['results'] if 'results' in response.data else response.data
        assert len(twins_data) == 1  # Should only see public twins
        assert twins_data[0]['privacy_setting'] == 'public'


class TestTwinViewSetActions:

    def test_update_persona_data(self, api_client, user, twin):
        """Test updating persona data"""
        url = reverse('twin-update-persona', kwargs={'pk': twin.id})
        api_client.force_authenticate(user=user)

        new_data = {
            'persona_description': 'Updated description',
            'conversations': [{'question': 'New?', 'answer': 'Yes!'}]
        }

        response = api_client.patch(url, new_data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['persona_data']['persona_description'] == 'Updated description'
        assert len(response.data['persona_data']['conversations']) == 1

    def test_partial_update_persona_data(self, api_client, user, twin):
        """Test updating only part of persona data"""
        url = reverse('twin-update-persona', kwargs={'pk': twin.id})
        api_client.force_authenticate(user=user)

        # Update only the description
        new_data = {
            'persona_description': 'Only description updated'
        }

        response = api_client.patch(url, new_data, format='json')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['persona_data']['persona_description'] == 'Only description updated'
        # Conversations should remain unchanged
        assert len(response.data['persona_data']['conversations']) == 1
        assert response.data['persona_data']['conversations'][0]['question'] == 'How are you?'

    def test_toggle_active_status(self, api_client, user, twin):
        """Test toggling active status"""
        url = reverse('twin-toggle-active', kwargs={'pk': twin.id})
        api_client.force_authenticate(user=user)

        # First toggle - should deactivate
        initial_state = twin.is_active
        response = api_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['is_active'] != initial_state

        # Second toggle - should reactivate
        response = api_client.post(url)

        assert response.status_code == status.HTTP_200_OK
        assert response.data['is_active'] == initial_state

    def test_duplicate_twin(self, api_client, user, twin):
        """Test duplicating a twin"""
        url = reverse('twin-duplicate', kwargs={'pk': twin.id})
        api_client.force_authenticate(user=user)

        response = api_client.post(url)

        assert response.status_code == status.HTTP_201_CREATED
        assert 'Copy of' in response.data['name']
        assert response.data['privacy_setting'] == 'private'
        assert response.data['persona_data']['persona_description'] == twin.persona_data['persona_description']

        # Check that a new database record was created
        assert Twin.objects.count() == 2

    def test_stats_endpoint_admin_only(self, api_client, user, admin_user):
        """Test stats endpoint is accessible only to admins"""
        url = reverse('twin-stats')

        # Regular user cannot access
        api_client.force_authenticate(user=user)
        response = api_client.get(url)
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Admin can access
        api_client.force_authenticate(user=admin_user)
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert 'total_twins' in response.data


class TestTwinCreationLimits:

    def test_creation_limit_enforced(self, api_client, user, settings):
        """Test twin creation limit is enforced"""
        # Set a low limit for testing
        settings.MAX_TWINS_PER_USER = 2

        url = reverse('twin-list')
        api_client.force_authenticate(user=user)

        # Create twins up to the limit
        for i in range(settings.MAX_TWINS_PER_USER):
            data = {
                'name': f'Twin {i}',
                'privacy_setting': 'private',
                'persona_data': {
                    'persona_description': f'Description {i}',
                    'conversations': []
                }
            }
            response = api_client.post(url, data, format='json')
            assert response.status_code == status.HTTP_201_CREATED

        # Try to create one more twin beyond the limit
        data = {
            'name': 'One Too Many',
            'privacy_setting': 'private',
            'persona_data': {
                'persona_description': 'Extra twin',
                'conversations': []
            }
        }
        response = api_client.post(url, data, format='json')
        assert response.status_code == status.HTTP_403_FORBIDDEN

        # Verify the total count
        assert Twin.objects.filter(owner=user).count() == settings.MAX_TWINS_PER_USER