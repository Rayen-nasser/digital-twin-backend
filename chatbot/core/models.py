# core/models.py
import json
import os
from django.db import models
from django.contrib.auth.models import AbstractUser
import uuid
from django.contrib.postgres.indexes import BTreeIndex, GinIndex
from jsonschema import ValidationError


def user_profile_image_path(instance, filename):
    # File will be uploaded to MEDIA_ROOT/user_profile_images/user_id/filename
    ext = filename.split('.')[-1]
    new_filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join('user_profile_images', str(instance.id), new_filename)


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_verified = models.BooleanField(default=False)
    profile_image = models.ImageField(
        upload_to=user_profile_image_path,
        null=True,
        blank=True,
        verbose_name='Profile Image'
    )
    last_seen = models.DateTimeField(null=True, blank=True)
    warning_count = models.IntegerField(default=0)

    class Meta:
        db_table = 'custom_users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.username} ({self.email})"


class AuthToken(models.Model):
    token = models.CharField(max_length=255, unique=True)
    expires_at = models.DateTimeField()
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='auth_tokens')

    class Meta:
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"Token for {self.user.email} (expires: {self.expires_at})"


class MediaFile(models.Model):
    """
    Unified file storage with type detection and previews
    """
    FILE_CATEGORIES = (
        ('image', 'Image'),
        ('document', 'Document'),
        ('audio', 'Audio'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    original_name = models.CharField(max_length=255)
    storage_path = models.CharField(max_length=512)
    file_category = models.CharField(max_length=10, choices=FILE_CATEGORIES)
    mime_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    uploader = models.ForeignKey(User, on_delete=models.CASCADE, related_name='media_files')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    is_public = models.BooleanField(default=False)

    # For images/videos
    thumbnail_path = models.CharField(max_length=512, null=True, blank=True)
    dimensions = models.CharField(max_length=20, null=True, blank=True)  # "1920x1080"

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Media File'
        verbose_name_plural = 'Media Files'
        indexes = [
            BTreeIndex(fields=['uploader', 'uploaded_at']),
        ]

    def __str__(self):
        return f"{self.original_name} ({self.get_file_category_display()})"


class Twin(models.Model):
    PRIVACY_CHOICES = [
        ('private', 'Private'),
        ('public', 'Public'),
        ('shared', 'Shared with selected users'),
    ]

    def get_default_persona_data():
        return {
            "persona_description": "",
            "conversations": []
        }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='twins')
    persona_data = models.JSONField(
        default=get_default_persona_data,
        blank=True,
        help_text="Structured persona data including description and conversation examples"
    )
    avatar = models.ForeignKey(MediaFile, on_delete=models.SET_NULL, null=True, blank=True)
    privacy_setting = models.CharField(max_length=10, choices=PRIVACY_CHOICES, default='private')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Digital Twin'
        verbose_name_plural = 'Digital Twins'
        indexes = [
            models.Index(fields=['owner']),
            models.Index(fields=['privacy_setting']),
        ]

    def __str__(self):
        return f"{self.name} (Owned by: {self.owner.email})"

    def clean(self):
        try:
            if isinstance(self.persona_data, str):
                self.persona_data = json.loads(self.persona_data)
            if not isinstance(self.persona_data, dict):
                raise ValidationError("Persona data must be a dictionary")
        except json.JSONDecodeError:
            raise ValidationError("Invalid JSON in persona_data")


class UserTwinChat(models.Model):
    """
    Core 1:1 chat channel between user and twin with access control
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='chats')
    twin = models.ForeignKey(Twin, on_delete=models.CASCADE, related_name='chats')
    created_at = models.DateTimeField(auto_now_add=True)
    last_active = models.DateTimeField(auto_now=True)

    # Access control (both must be True for messaging)
    user_has_access = models.BooleanField(default=True)
    twin_is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'twin'],
                name='unique_user_twin_pair'
            )
        ]
        indexes = [
            BTreeIndex(fields=['user', 'last_active']),
            BTreeIndex(fields=['twin', 'last_active']),
        ]


class VoiceRecording(models.Model):
    """
    Dedicated storage for voice messages with processing status
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    storage_path = models.CharField(max_length=512)  # S3/GCS path
    duration_seconds = models.FloatField()
    format = models.CharField(max_length=10, default='ogg')  # ogg/mp3
    sample_rate = models.PositiveIntegerField()  # 8000, 16000, etc.
    created_at = models.DateTimeField(auto_now_add=True)

    # Processing flags
    is_processed = models.BooleanField(default=False)
    transcription = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [
            BTreeIndex(fields=['created_at']),
        ]


class Message(models.Model):
    """
    Unified message structure for all media types with delivery tracking
    """
    MESSAGE_TYPES = (
        ('text', 'Text'),
        ('voice', 'Voice'),
        ('file', 'File'),
    )

    STATUS_CHOICES = (
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('read', 'Read'),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey(UserTwinChat, on_delete=models.CASCADE, related_name='messages')

    # Sender info (Boolean is faster than FK for direction)
    is_from_user = models.BooleanField()  # True=user, False=twin

    # Content storage
    message_type = models.CharField(max_length=10, choices=MESSAGE_TYPES)
    text_content = models.TextField(blank=True, null=True)
    voice_note = models.ForeignKey(
        VoiceRecording,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    file_attachment = models.ForeignKey(
        MediaFile,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='message_attachments'
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='sent')
    status_updated_at = models.DateTimeField(auto_now=True)

    # For voice messages
    duration_seconds = models.FloatField(null=True, blank=True)

    # For files
    file_preview_url = models.URLField(null=True, blank=True)
    report_count = models.IntegerField(default=0)

    class Meta:
        indexes = [
            BTreeIndex(fields=['chat', 'created_at']),  # Message history
            BTreeIndex(fields=['is_from_user', 'created_at']),  # Sent messages
            GinIndex(fields=["status"], name="status_gin_trgm", opclasses=["gin_trgm_ops"]),  # Fast status filtering
        ]
        ordering = ['created_at']


class TwinAccess(models.Model):
    """
    Gatekeeper for user-twin communication permissions
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='twin_accesses')
    twin = models.ForeignKey(Twin, on_delete=models.CASCADE, related_name='user_accesses')
    granted_at = models.DateTimeField(auto_now_add=True)
    grant_expires = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'twin'],
                name='unique_access_grant'
            )
        ]


class Contact(models.Model):
    """
    Contact form submissions storage
    """
    name = models.CharField(max_length=255)
    email = models.EmailField()
    subject = models.CharField(max_length=255)
    message = models.TextField()
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Contact'
        verbose_name_plural = 'Contacts'

    def __str__(self):
        return f"{self.name} - {self.subject}"


class Subscription(models.Model):
    """
    Subscription form submissions storage
    """
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Subscription'
        verbose_name_plural = 'Subscriptions'