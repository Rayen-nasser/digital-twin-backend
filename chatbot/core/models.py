# core/models.py
from djongo import models as mongo_models
from django.db import models as django_models
from django.contrib.auth.models import AbstractUser
import uuid
from django.core.validators import MaxValueValidator, MinValueValidator

# ========================== PostgreSQL Models ========================== #

class User(AbstractUser):
    id = django_models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = django_models.EmailField(unique=True)
    created_at = django_models.DateTimeField(auto_now_add=True)
    is_verified = django_models.BooleanField(default=False)

    # AI interaction settings - Fix: use callable as default
    def get_default_ai_settings():
        return {
            'temperature': 0.7,
            'max_response_length': 500
        }

    ai_settings = django_models.JSONField(default=get_default_ai_settings)

    class Meta:
        db_table = 'custom_users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.username} ({self.email})"

class AuthToken(django_models.Model):
    token = django_models.CharField(max_length=255, unique=True)
    expires_at = django_models.DateTimeField()
    user = django_models.ForeignKey(User, on_delete=django_models.CASCADE, related_name='auth_tokens')

    class Meta:
        indexes = [
            django_models.Index(fields=['token']),
            django_models.Index(fields=['expires_at']),
        ]

    def __str__(self):
        return f"Token for {self.user.email} (expires: {self.expires_at})"


class MediaFile(django_models.Model):
    FILE_TYPES = [
        ('image', 'Image'),
        ('audio', 'Audio'),
        ('document', 'Document'),
    ]

    id = django_models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = django_models.CharField(max_length=255)
    file_type = django_models.CharField(max_length=50, choices=FILE_TYPES)
    uploaded_by = django_models.ForeignKey(User, on_delete=django_models.CASCADE, related_name='media_files')
    uploaded_at = django_models.DateTimeField(auto_now_add=True)
    size_mb = django_models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(50)])  # Max 50MB
    path = django_models.CharField(max_length=512)
    is_public = django_models.BooleanField(default=False)

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Media File'
        verbose_name_plural = 'Media Files'

    def __str__(self):
        return f"{self.filename} ({self.get_file_type_display()})"


class Twin(django_models.Model):
    PRIVACY_CHOICES = [
        ('private', 'Private'),
        ('public', 'Public'),
        ('shared', 'Shared with selected users'),
    ]

    id = django_models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = django_models.CharField(max_length=255)
    owner = django_models.ForeignKey(User, on_delete=django_models.CASCADE, related_name='twins')
    description = django_models.TextField(blank=True)
    avatar = django_models.ForeignKey(MediaFile, on_delete=django_models.SET_NULL, null=True, blank=True, )

    # Fix: use callable as default
    def get_default_communication_style():
        return {
            'formality': 'neutral',
            'humor_level': 3,
            'response_speed': 'normal'
        }

    communication_style = django_models.JSONField(default=get_default_communication_style)
    privacy_setting = django_models.CharField(max_length=10, choices=PRIVACY_CHOICES, default='private')
    created_at = django_models.DateTimeField(auto_now_add=True)
    updated_at = django_models.DateTimeField(auto_now=True)
    is_active = django_models.BooleanField(default=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Digital Twin'
        verbose_name_plural = 'Digital Twins'
        indexes = [
            django_models.Index(fields=['owner']),
            django_models.Index(fields=['privacy_setting']),
        ]

    def __str__(self):
        return f"{self.name} (Owned by: {self.owner.email})"

# ========================== MongoDB Models ========================== #
class Message(mongo_models.Model):
    MESSAGE_TYPES = [
        ('text', 'Text'),
        ('image', 'Image'),
        ('audio', 'Audio'),
    ]

    SENDER_TYPES = [
        ('user', 'User'),
        ('twin', 'Twin'),
    ]

    id = mongo_models.UUIDField(default=uuid.uuid4, primary_key=True)
    content = mongo_models.TextField()
    message_type = mongo_models.CharField(max_length=10, choices=MESSAGE_TYPES, default='text')
    sender_type = mongo_models.CharField(max_length=10, choices=SENDER_TYPES)
    sender_id = mongo_models.UUIDField()
    created_at = mongo_models.DateTimeField(auto_now_add=True)
    is_read = mongo_models.BooleanField(default=False)

    # Fix: use callable as default
    def get_default_metadata():
        return {}

    metadata = mongo_models.JSONField(default=get_default_metadata)

    class Meta:
        db_table = 'messages'
        ordering = ['-created_at']
        indexes = [
            mongo_models.Index(fields=['sender_type', 'sender_id']),
            mongo_models.Index(fields=['created_at']),
        ]

    def get_sender(self):
        if self.sender_type == 'user':
            return User.objects.filter(id=self.sender_id).first()
        elif self.sender_type == 'twin':
            return Twin.objects.filter(id=self.sender_id).first()
        return None

    def __str__(self):
        sender = self.get_sender()
        sender_name = getattr(sender, 'email', None) if self.sender_type == 'user' else getattr(sender, 'name', None) if self.sender_type == 'twin' else "Unknown"
        return f"{self.message_type} message from {sender_name} at {self.created_at}"


class ChatHistory(mongo_models.Model):
    _id = mongo_models.ObjectIdField()
    twin_id = mongo_models.UUIDField()  # Reference to the twin
    user_id = mongo_models.UUIDField()  # Reference to the user

    # Directly store message objects
    messages = mongo_models.JSONField()  # Store list of message objects (e.g., content, sender, etc.)
    created_at = mongo_models.DateTimeField(auto_now_add=True)
    updated_at = mongo_models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'chat_histories'
        indexes = [
            mongo_models.Index(fields=['twin_id'], name='twin_idx'),
            mongo_models.Index(fields=['user_id'], name='user_idx'),
            mongo_models.Index(fields=['updated_at'], name='updated_at_idx'),
        ]
        ordering = ['-updated_at']

    def get_user(self):
        return User.objects.filter(id=self.user_id).first()

    def get_twin(self):
        return Twin.objects.filter(id=self.twin_id).first()

    def __str__(self):
        user = self.get_user()
        twin = self.get_twin()
        user_name = user.username if user else "Unknown User"
        twin_name = twin.name if twin else "Unknown Twin"
        return f"Chat between {user_name} and {twin_name} ({len(self.messages)} messages)"
