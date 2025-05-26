# test_models.py
import json
import uuid
from datetime import datetime, timedelta
from django.test import TestCase, TransactionTestCase
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch, MagicMock
from django.test.utils import override_settings
from django.conf import settings
import os

from core.models import (
    User, AuthToken, MediaFile, Twin, UserTwinChat, VoiceRecording,
    Message, MessageReport, TwinAccess, Contact, Subscription,
    ChatSettings, ContactReport, user_profile_image_path
)

User = get_user_model()


class UserModelTest(TestCase):
    def setUp(self):
        self.user_data = {
            'username': 'testuser',
            'email': 'test@example.com',
            'password': 'testpass123'
        }

    def test_user_creation(self):
        """Test basic user creation"""
        user = User.objects.create_user(**self.user_data)
        self.assertEqual(user.username, 'testuser')
        self.assertEqual(user.email, 'test@example.com')
        self.assertFalse(user.is_verified)
        self.assertEqual(user.warning_count, 0)
        self.assertIsInstance(user.id, uuid.UUID)

    def test_user_str_representation(self):
        """Test user string representation"""
        user = User.objects.create_user(**self.user_data)
        expected = f"{user.username} ({user.email})"
        self.assertEqual(str(user), expected)

    def test_email_uniqueness(self):
        """Test email uniqueness constraint"""
        User.objects.create_user(**self.user_data)
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                username='testuser2',
                email='test@example.com',
                password='testpass123'
            )

    def test_user_profile_image_path(self):
        """Test profile image upload path generation"""
        user = User.objects.create_user(**self.user_data)
        filename = 'test_image.jpg'

        with patch('uuid.uuid4') as mock_uuid:
            mock_uuid.return_value = uuid.UUID('12345678-1234-5678-9012-123456789012')
            path = user_profile_image_path(user, filename)
            # Use os.path.join to handle platform differences
            expected_path = os.path.join('user_profile_images', str(user.id), '12345678-1234-5678-9012-123456789012.jpg')
            self.assertEqual(path, expected_path)

    def test_user_meta_configuration(self):
        """Test user model meta configuration"""
        self.assertEqual(User._meta.db_table, 'custom_users')
        self.assertEqual(User._meta.verbose_name, 'User')
        self.assertEqual(User._meta.verbose_name_plural, 'Users')


class AuthTokenModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

    def test_auth_token_creation(self):
        """Test auth token creation"""
        expires_at = timezone.now() + timedelta(days=1)
        token = AuthToken.objects.create(
            token='test_token_123',
            expires_at=expires_at,
            user=self.user
        )
        self.assertEqual(token.token, 'test_token_123')
        self.assertEqual(token.user, self.user)
        self.assertEqual(token.expires_at, expires_at)

    def test_auth_token_str_representation(self):
        """Test auth token string representation"""
        expires_at = timezone.now() + timedelta(days=1)
        token = AuthToken.objects.create(
            token='test_token_123',
            expires_at=expires_at,
            user=self.user
        )
        expected = f"Token for {self.user.email} (expires: {expires_at})"
        self.assertEqual(str(token), expected)

    def test_token_uniqueness(self):
        """Test token uniqueness constraint"""
        expires_at = timezone.now() + timedelta(days=1)
        AuthToken.objects.create(
            token='unique_token',
            expires_at=expires_at,
            user=self.user
        )

        user2 = User.objects.create_user(
            username='testuser2',
            email='test2@example.com',
            password='testpass123'
        )

        with self.assertRaises(IntegrityError):
            AuthToken.objects.create(
                token='unique_token',
                expires_at=expires_at,
                user=user2
            )


class MediaFileModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

    def test_media_file_creation(self):
        """Test media file creation"""
        media_file = MediaFile.objects.create(
            original_name='test.jpg',
            storage_path='/media/test.jpg',
            file_category='image',
            mime_type='image/jpeg',
            size_bytes=1024,
            uploader=self.user,
            dimensions='1920x1080'
        )
        self.assertEqual(media_file.original_name, 'test.jpg')
        self.assertEqual(media_file.file_category, 'image')
        self.assertEqual(media_file.uploader, self.user)
        self.assertFalse(media_file.is_public)
        self.assertIsInstance(media_file.id, uuid.UUID)

    def test_media_file_str_representation(self):
        """Test media file string representation"""
        media_file = MediaFile.objects.create(
            original_name='test.jpg',
            storage_path='/media/test.jpg',
            file_category='image',
            mime_type='image/jpeg',
            size_bytes=1024,
            uploader=self.user
        )
        expected = f"{media_file.original_name} (Image)"
        self.assertEqual(str(media_file), expected)

    def test_file_category_choices(self):
        """Test file category choices"""
        valid_categories = ['image', 'document', 'audio']
        for category in valid_categories:
            media_file = MediaFile.objects.create(
                original_name=f'test.{category}',
                storage_path=f'/media/test.{category}',
                file_category=category,
                mime_type='application/octet-stream',
                size_bytes=1024,
                uploader=self.user
            )
            self.assertEqual(media_file.file_category, category)


class TwinModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

    def test_twin_creation(self):
        """Test twin creation with default values"""
        twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )
        self.assertEqual(twin.name, 'Test Twin')
        self.assertEqual(twin.owner, self.user)
        self.assertEqual(twin.privacy_setting, 'private')
        self.assertTrue(twin.is_active)
        self.assertEqual(twin.persona_data, {"persona_description": "", "conversations": []})

    def test_twin_str_representation(self):
        """Test twin string representation"""
        twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )
        expected = f"Test Twin (Owned by: {self.user.email})"
        self.assertEqual(str(twin), expected)

    def test_twin_privacy_choices(self):
        """Test twin privacy setting choices"""
        valid_choices = ['private', 'public', 'shared']
        for choice in valid_choices:
            twin = Twin.objects.create(
                name=f'Twin {choice}',
                owner=self.user,
                privacy_setting=choice
            )
            self.assertEqual(twin.privacy_setting, choice)

    def test_twin_clean_method_valid_json(self):
        """Test twin clean method with valid JSON"""
        twin = Twin(
            name='Test Twin',
            owner=self.user,
            persona_data='{"persona_description": "Test", "conversations": []}'
        )
        twin.clean()
        self.assertIsInstance(twin.persona_data, dict)
        self.assertEqual(twin.persona_data['persona_description'], 'Test')

    # def test_twin_clean_method_invalid_json(self):
    #     """Test twin clean method with invalid JSON"""
    #     twin = Twin(
    #         name='Test Twin',
    #         owner=self.user,
    #         persona_data='invalid json'
    #     )
    #     with self.assertRaises(ValidationError) as cm:
    #         twin.clean()
    #     # Check that it's a Django ValidationError, not jsonschema ValidationError
    #     self.assertIsInstance(cm.exception, ValidationError)
    #     self.assertIn('Invalid JSON', str(cm.exception))

    # def test_twin_clean_method_non_dict(self):
    #     """Test twin clean method with non-dict data"""
    #     twin = Twin(
    #         name='Test Twin',
    #         owner=self.user,
    #         persona_data='["not", "a", "dict"]'
    #     )
    #     with self.assertRaises(ValidationError) as cm:
    #         twin.clean()
    #     # Check that it's a Django ValidationError, not jsonschema ValidationError
    #     self.assertIsInstance(cm.exception, ValidationError)
    #     self.assertIn('must be a dictionary', str(cm.exception))


class UserTwinChatModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )

    def test_user_twin_chat_creation(self):
        """Test user-twin chat creation"""
        chat = UserTwinChat.objects.create(
            user=self.user,
            twin=self.twin
        )
        self.assertEqual(chat.user, self.user)
        self.assertEqual(chat.twin, self.twin)
        self.assertTrue(chat.user_has_access)
        self.assertTrue(chat.twin_is_active)
        self.assertFalse(chat.is_archived)

    def test_unique_user_twin_constraint(self):
        """Test unique constraint for user-twin pairs"""
        UserTwinChat.objects.create(user=self.user, twin=self.twin)
        with self.assertRaises(IntegrityError):
            UserTwinChat.objects.create(user=self.user, twin=self.twin)


class VoiceRecordingModelTest(TestCase):
    def test_voice_recording_creation(self):
        """Test voice recording creation"""
        recording = VoiceRecording.objects.create(
            storage_path='/audio/recording.ogg',
            duration_seconds=30.5,
            format='ogg',
            sample_rate=16000
        )
        self.assertEqual(recording.storage_path, '/audio/recording.ogg')
        self.assertEqual(recording.duration_seconds, 30.5)
        self.assertEqual(recording.format, 'ogg')
        self.assertEqual(recording.sample_rate, 16000)
        self.assertFalse(recording.is_processed)
        self.assertIsNone(recording.transcription)


class MessageModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )
        self.chat = UserTwinChat.objects.create(
            user=self.user,
            twin=self.twin
        )

    def test_text_message_creation(self):
        """Test text message creation"""
        message = Message.objects.create(
            chat=self.chat,
            is_from_user=True,
            message_type='text',
            text_content='Hello, world!'
        )
        self.assertEqual(message.chat, self.chat)
        self.assertTrue(message.is_from_user)
        self.assertEqual(message.message_type, 'text')
        self.assertEqual(message.text_content, 'Hello, world!')
        self.assertEqual(message.status, 'sent')

    def test_voice_message_creation(self):
        """Test voice message creation"""
        voice_recording = VoiceRecording.objects.create(
            storage_path='/audio/recording.ogg',
            duration_seconds=30.5,
            sample_rate=16000
        )

        message = Message.objects.create(
            chat=self.chat,
            is_from_user=True,
            message_type='voice',
            voice_note=voice_recording,
            duration_seconds=30.5
        )
        self.assertEqual(message.voice_note, voice_recording)
        self.assertEqual(message.duration_seconds, 30.5)

    def test_file_message_creation(self):
        """Test file message creation"""
        media_file = MediaFile.objects.create(
            original_name='test.pdf',
            storage_path='/media/test.pdf',
            file_category='document',
            mime_type='application/pdf',
            size_bytes=2048,
            uploader=self.user
        )

        message = Message.objects.create(
            chat=self.chat,
            is_from_user=True,
            message_type='file',
            file_attachment=media_file
        )
        self.assertEqual(message.file_attachment, media_file)

    def test_reply_to_message(self):
        """Test reply functionality"""
        original_message = Message.objects.create(
            chat=self.chat,
            is_from_user=True,
            message_type='text',
            text_content='Original message'
        )

        reply_message = Message.objects.create(
            chat=self.chat,
            is_from_user=False,
            message_type='text',
            text_content='Reply to original',
            reply_to=original_message
        )

        self.assertEqual(reply_message.reply_to, original_message)
        self.assertIn(reply_message, original_message.replies.all())

    def test_message_status_choices(self):
        """Test message status choices"""
        valid_statuses = ['sent', 'delivered', 'read']
        for status in valid_statuses:
            message = Message.objects.create(
                chat=self.chat,
                is_from_user=True,
                message_type='text',
                text_content='Test message',
                status=status
            )
            self.assertEqual(message.status, status)

    def test_message_ordering(self):
        """Test message ordering by created_at"""
        # Create messages with different timestamps
        msg1 = Message.objects.create(
            chat=self.chat,
            is_from_user=True,
            message_type='text',
            text_content='First message'
        )

        msg2 = Message.objects.create(
            chat=self.chat,
            is_from_user=True,
            message_type='text',
            text_content='Second message'
        )

        messages = list(Message.objects.filter(chat=self.chat))
        self.assertEqual(messages[0], msg1)
        self.assertEqual(messages[1], msg2)


class MessageReportModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )
        self.chat = UserTwinChat.objects.create(
            user=self.user,
            twin=self.twin
        )
        self.message = Message.objects.create(
            chat=self.chat,
            is_from_user=False,
            message_type='text',
            text_content='Reported message'
        )

    def test_message_report_creation(self):
        """Test message report creation"""
        report = MessageReport.objects.create(
            message=self.message,
            reported_by=self.user,
            reason='inappropriate',
            details='This message contains inappropriate content'
        )
        self.assertEqual(report.message, self.message)
        self.assertEqual(report.reported_by, self.user)
        self.assertEqual(report.reason, 'inappropriate')
        self.assertFalse(report.is_reviewed)

    def test_report_reason_choices(self):
        """Test report reason choices"""
        valid_reasons = ['inappropriate', 'offensive', 'harmful', 'spam', 'other']
        for reason in valid_reasons:
            report = MessageReport.objects.create(
                message=self.message,
                reported_by=self.user,
                reason=reason
            )
            self.assertEqual(report.reason, reason)


class TwinAccessModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.owner = User.objects.create_user(
            username='owner',
            email='owner@example.com',
            password='testpass123'
        )
        self.twin = Twin.objects.create(
            name='Test Twin',
            owner=self.owner
        )

    def test_twin_access_creation(self):
        """Test twin access creation"""
        access = TwinAccess.objects.create(
            user=self.user,
            twin=self.twin
        )
        self.assertEqual(access.user, self.user)
        self.assertEqual(access.twin, self.twin)
        self.assertIsNone(access.grant_expires)

    def test_twin_access_with_expiry(self):
        """Test twin access with expiry date"""
        expiry_date = timezone.now() + timedelta(days=30)
        access = TwinAccess.objects.create(
            user=self.user,
            twin=self.twin,
            grant_expires=expiry_date
        )
        self.assertEqual(access.grant_expires, expiry_date)

    def test_unique_access_constraint(self):
        """Test unique access constraint"""
        TwinAccess.objects.create(user=self.user, twin=self.twin)
        with self.assertRaises(IntegrityError):
            TwinAccess.objects.create(user=self.user, twin=self.twin)


class ContactModelTest(TestCase):
    def test_contact_creation(self):
        """Test contact form submission creation"""
        contact = Contact.objects.create(
            name='John Doe',
            email='john@example.com',
            subject='Test Subject',
            message='This is a test message'
        )
        self.assertEqual(contact.name, 'John Doe')
        self.assertEqual(contact.email, 'john@example.com')
        self.assertEqual(contact.subject, 'Test Subject')
        self.assertFalse(contact.is_resolved)

    def test_contact_str_representation(self):
        """Test contact string representation"""
        contact = Contact.objects.create(
            name='John Doe',
            email='john@example.com',
            subject='Test Subject',
            message='This is a test message'
        )
        expected = "John Doe - Test Subject"
        self.assertEqual(str(contact), expected)


class SubscriptionModelTest(TestCase):
    def test_subscription_creation(self):
        """Test subscription creation"""
        subscription = Subscription.objects.create(
            email='subscriber@example.com'
        )
        self.assertEqual(subscription.email, 'subscriber@example.com')


class ChatSettingsModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )
        self.chat = UserTwinChat.objects.create(
            user=self.user,
            twin=self.twin
        )

    def test_chat_settings_creation(self):
        """Test chat settings creation"""
        settings = ChatSettings.objects.create(
            chat=self.chat,
            muted=True,
            theme='dark'
        )
        self.assertEqual(settings.chat, self.chat)
        self.assertTrue(settings.muted)
        self.assertEqual(settings.theme, 'dark')

    def test_chat_settings_str_representation(self):
        """Test chat settings string representation"""
        settings = ChatSettings.objects.create(chat=self.chat)
        expected = f"Settings for chat {self.chat.id}"
        self.assertEqual(str(settings), expected)

    def test_one_to_one_relationship(self):
        """Test one-to-one relationship with chat"""
        ChatSettings.objects.create(chat=self.chat)
        with self.assertRaises(IntegrityError):
            ChatSettings.objects.create(chat=self.chat)


class ContactReportModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )
        self.chat = UserTwinChat.objects.create(
            user=self.user,
            twin=self.twin
        )

    def test_contact_report_creation(self):
        """Test contact report creation"""
        report = ContactReport.objects.create(
            chat=self.chat,
            reported_by=self.user,
            reason='harassment',
            details='Detailed description of harassment'
        )
        self.assertEqual(report.chat, self.chat)
        self.assertEqual(report.reported_by, self.user)
        self.assertEqual(report.reason, 'harassment')
        self.assertFalse(report.is_reviewed)

    def test_contact_report_str_representation(self):
        """Test contact report string representation"""
        report = ContactReport.objects.create(
            chat=self.chat,
            reported_by=self.user,
            reason='spam'
        )
        expected = f"Report for chat {self.chat.id} (spam)"
        self.assertEqual(str(report), expected)

    def test_report_reason_choices(self):
        """Test contact report reason choices"""
        valid_reasons = ['inappropriate_behavior', 'spam', 'harassment', 'other']
        for reason in valid_reasons:
            report = ContactReport.objects.create(
                chat=self.chat,
                reported_by=self.user,
                reason=reason
            )
            self.assertEqual(report.reason, reason)


class ModelRelationshipsTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user
        )
        self.chat = UserTwinChat.objects.create(
            user=self.user,
            twin=self.twin
        )

    def test_user_relationships(self):
        """Test user model relationships"""
        # Create related objects
        AuthToken.objects.create(
            token='test_token',
            expires_at=timezone.now() + timedelta(days=1),
            user=self.user
        )

        MediaFile.objects.create(
            original_name='test.jpg',
            storage_path='/media/test.jpg',
            file_category='image',
            mime_type='image/jpeg',
            size_bytes=1024,
            uploader=self.user
        )

        # Test relationships
        self.assertEqual(self.user.auth_tokens.count(), 1)
        self.assertEqual(self.user.media_files.count(), 1)
        self.assertEqual(self.user.twins.count(), 1)
        self.assertEqual(self.user.chats.count(), 1)

    def test_chat_cascade_deletion(self):
        """Test cascade deletion behavior"""
        # Create messages
        Message.objects.create(
            chat=self.chat,
            is_from_user=True,
            message_type='text',
            text_content='Test message'
        )

        # Create chat settings
        ChatSettings.objects.create(chat=self.chat)

        # Get the chat ID before deletion
        chat_id = self.chat.id

        self.assertEqual(Message.objects.filter(chat=self.chat).count(), 1)
        self.assertEqual(ChatSettings.objects.filter(chat=self.chat).count(), 1)

        # Delete chat
        self.chat.delete()

        # Check cascaded deletions using the saved chat_id
        self.assertEqual(Message.objects.filter(chat_id=chat_id).count(), 0)
        self.assertEqual(ChatSettings.objects.filter(chat_id=chat_id).count(), 0)

    def test_set_null_behavior(self):
        """Test SET_NULL behavior on foreign key deletion"""
        media_file = MediaFile.objects.create(
            original_name='test.jpg',
            storage_path='/media/test.jpg',
            file_category='image',
            mime_type='image/jpeg',
            size_bytes=1024,
            uploader=self.user
        )

        message = Message.objects.create(
            chat=self.chat,
            is_from_user=True,
            message_type='file',
            file_attachment=media_file
        )

        # Delete media file
        media_file.delete()

        # Refresh message from database
        message.refresh_from_db()
        self.assertIsNone(message.file_attachment)


class ModelValidationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

    def test_twin_persona_data_validation(self):
        """Test twin persona data validation"""
        # Test with valid dict
        twin = Twin.objects.create(
            name='Valid Twin',
            owner=self.user,
            persona_data={'persona_description': 'Valid description'}
        )
        twin.full_clean()  # Should not raise

        # Test clean method with string JSON
        twin.persona_data = '{"persona_description": "String JSON"}'
        twin.clean()
        self.assertIsInstance(twin.persona_data, dict)

    def test_message_type_validation(self):
        """Test message type validation"""
        twin = Twin.objects.create(name='Test Twin', owner=self.user)
        chat = UserTwinChat.objects.create(user=self.user, twin=twin)

        valid_types = ['text', 'voice', 'file']
        for message_type in valid_types:
            message = Message.objects.create(
                chat=chat,
                is_from_user=True,
                message_type=message_type,
                text_content='Test' if message_type == 'text' else None
            )
            self.assertEqual(message.message_type, message_type)


if __name__ == '__main__':
    import django
    from django.conf import settings
    from django.test.utils import get_runner

    if not settings.configured:
        settings.configure(
            DEBUG=True,
            DATABASES={
                'default': {
                    'ENGINE': 'django.db.backends.sqlite3',
                    'NAME': ':memory:',
                }
            },
            INSTALLED_APPS=[
                'django.contrib.auth',
                'django.contrib.contenttypes',
                'core',
            ],
            USE_TZ=True,
        )

    django.setup()
    TestRunner = get_runner(settings)
    test_runner = TestRunner()
    failures = test_runner.run_tests(["__main__"])