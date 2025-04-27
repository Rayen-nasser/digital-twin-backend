from django.db.models.signals import post_save
from django.dispatch import receiver
from core.models import Message, UserTwinChat


@receiver(post_save, sender=Message)
def update_chat_last_active(sender, instance, created, **kwargs):
    """
    Update chat's last_active timestamp when a new message is created
    """
    if created:
        chat = instance.chat
        chat.last_active = instance.created_at
        chat.save(update_fields=['last_active'])


@receiver(post_save, sender=Message)
def process_voice_recording(sender, instance, created, **kwargs):
    """
    Process voice recordings for transcription
    """
    if created and instance.message_type == 'voice' and instance.voice_note:
        voice_note = instance.voice_note

        # This is where you would typically trigger an async task for transcription
        # For now, we'll just simulate the process
        if not voice_note.is_processed:
            # In a real implementation, this would be:
            # tasks.transcribe_voice_recording.delay(voice_note.id)
            pass