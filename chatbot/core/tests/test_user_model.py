# core/tests/test_user_model.py
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta
import uuid
from core.models import User, AuthToken, MediaFile, Twin

class UserModelTests(TestCase):
    def setUp(self):
        self.user_data = {
            'username': 'Test User',
            'email': 'test@example.com',
            'password': 'securepassword123'
        }

    def test_create_user(self):
        user = User.objects.create_user(**self.user_data)
        self.assertEqual(user.email, 'test@example.com')
        self.assertFalse(user.is_verified)
        self.assertIsNotNone(user.id)
        self.assertIsInstance(user.id, uuid.UUID)

    def test_user_string_representation(self):
        user = User.objects.create_user(**self.user_data)
        self.assertEqual(str(user), "Test User (test@example.com)")

    def test_ai_settings_default(self):
        user = User.objects.create_user(**self.user_data)
        self.assertEqual(user.ai_settings['temperature'], 0.7)
        self.assertEqual(user.ai_settings['max_response_length'], 500)

    def test_update_ai_settings(self):
        user = User.objects.create_user(**self.user_data)
        user.ai_settings = {'temperature': 0.5, 'max_response_length': 1000}
        user.save()
        updated_user = User.objects.get(id=user.id)
        self.assertEqual(updated_user.ai_settings['temperature'], 0.5)
        self.assertEqual(updated_user.ai_settings['max_response_length'], 1000)


class AuthTokenModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='securepassword123'
        )
        self.expires_at = timezone.now() + timedelta(days=1)

    def test_create_auth_token(self):
        token = AuthToken.objects.create(
            token='test-token-123',
            expires_at=self.expires_at,
            user=self.user
        )
        self.assertEqual(token.token, 'test-token-123')
        self.assertEqual(token.user, self.user)

    def test_auth_token_string_representation(self):
        token = AuthToken.objects.create(
            token='test-token-123',
            expires_at=self.expires_at,
            user=self.user
        )
        expected_str = f"Token for {self.user.email} (expires: {self.expires_at})"
        self.assertEqual(str(token), expected_str)


class MediaFileModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='securepassword123'
        )

    def test_create_media_file(self):
        media_file = MediaFile.objects.create(
            filename='test_image.jpg',
            file_type='image',
            uploaded_by=self.user,
            size_mb=2.5,
            path='/storage/test_image.jpg',
            is_public=False
        )
        self.assertEqual(media_file.filename, 'test_image.jpg')
        self.assertEqual(media_file.file_type, 'image')
        self.assertEqual(media_file.uploaded_by, self.user)
        self.assertEqual(media_file.size_mb, 2.5)
        self.assertFalse(media_file.is_public)
        self.assertIsNotNone(media_file.id)
        self.assertIsInstance(media_file.id, uuid.UUID)

    def test_media_file_string_representation(self):
        media_file = MediaFile.objects.create(
            filename='test_image.jpg',
            file_type='image',
            uploaded_by=self.user,
            size_mb=2.5,
            path='/storage/test_image.jpg'
        )
        self.assertEqual(str(media_file), "test_image.jpg (Image)")

    def test_size_mb_validators(self):
        # Test size exceeding the maximum validator
        media_file = MediaFile(
            filename='large_file.pdf',
            file_type='document',
            uploaded_by=self.user,
            size_mb=51.0,  # Max is 50MB
            path='/storage/large_file.pdf'
        )

        with self.assertRaises(ValidationError):
            media_file.full_clean()

        # Test negative size (minimum validator)
        media_file = MediaFile(
            filename='invalid_file.pdf',
            file_type='document',
            uploaded_by=self.user,
            size_mb=-1.0,  # Must be non-negative
            path='/storage/invalid_file.pdf'
        )

        with self.assertRaises(ValidationError):
            media_file.full_clean()


class TwinModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='securepassword123'
        )
        self.media_file = MediaFile.objects.create(
            filename='avatar.jpg',
            file_type='image',
            uploaded_by=self.user,
            size_mb=1.0,
            path='/storage/avatar.jpg'
        )

    def test_create_twin(self):
        twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user,
            avatar=self.media_file,
            privacy_setting='public'
        )
        self.assertEqual(twin.name, 'Test Twin')
        self.assertEqual(twin.owner, self.user)
        self.assertEqual(twin.avatar, self.media_file)
        self.assertEqual(twin.privacy_setting, 'public')
        self.assertTrue(twin.is_active)
        self.assertIsNotNone(twin.id)
        self.assertIsInstance(twin.id, uuid.UUID)
        # Check persona_data default structure
        self.assertIn('persona_description', twin.persona_data)
        self.assertIn('conversations', twin.persona_data)
        self.assertEqual(twin.persona_data['persona_description'], '')
        self.assertEqual(twin.persona_data['conversations'], [])

    def test_twin_string_representation(self):
        twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )
        self.assertEqual(str(twin), f"Test Twin (Owned by: {self.user.email})")

    def test_update_persona_data(self):
        twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )
        twin.persona_data = {
            'persona_description': 'This is a cool AI twin.',
            'conversations': [
                {'question': 'Who are you?', 'answer': 'I am your digital twin!'}
            ]
        }
        twin.save()
        updated = Twin.objects.get(id=twin.id)
        self.assertEqual(updated.persona_data['persona_description'], 'This is a cool AI twin.')
        self.assertEqual(len(updated.persona_data['conversations']), 1)
        self.assertEqual(updated.persona_data['conversations'][0]['answer'], 'I am your digital twin!')
