import logging
from venv import logger
from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Count, Q
from django.contrib import messages
from django.shortcuts import render, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from datetime import timedelta
import json
from .models import Contact, MessageReport, Subscription, User, AuthToken, MediaFile, Twin, UserTwinChat, VoiceRecording, Message, TwinAccess



logger = logging.getLogger(__name__)



# Create custom admin templates and context processors for adding links
class CustomAdminSite(admin.AdminSite):
    """
    Custom admin site with additional navigation for platform monitoring views
    """

    def each_context(self, request):
        """Add custom links to the admin context"""
        context = super().each_context(request)

        # Add links to custom views
        context['custom_links'] = [
            {
                'name': 'Platform Monitor',
                'url': reverse('platform-monitor'),
                'icon': '📊'
            },
            {
                'name': 'Abuse Reports',
                'url': reverse('abuse-report'),
                'icon': '🚩'
            },
            {
                'name': 'Policy Enforcement',
                'url': reverse('policy-enforcement'),
                'icon': '⚖️'
            }
        ]

        return context

    def index(self, request, extra_context=None):
        """Customize the admin index page to include shortcuts to monitoring views"""
        extra_context = extra_context or {}

        # Add platform monitoring blocks to the index page
        monitor_blocks = [
            {
                'title': 'Platform Monitoring',
                'url': reverse('platform-monitor'),
                'description': 'View platform metrics, user activity, and content statistics'
            },
            {
                'title': 'Abuse Reports',
                'url': reverse('abuse-report'),
                'description': 'Review flagged content and user reports'
            },
            {
                'title': 'Policy Enforcement',
                'url': reverse('policy-enforcement'),
                'description': 'Take action on content moderation and user management'
            }
        ]

        extra_context['monitor_blocks'] = monitor_blocks

        return super().index(request, extra_context=extra_context)

# Create an instance of the custom admin site
custom_admin_site = CustomAdminSite(name='custom_admin')

# Replace the default admin site with our custom one
admin.site = custom_admin_site
# Custom Admin Site
class PlatformAdminSite(admin.AdminSite):
    site_header = "Platform Management"
    site_title = "Platform Admin"
    index_title = "Platform Control Panel"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        logger.info("PlatformAdminSite initialized")

# Create custom admin site
platform_admin = PlatformAdminSite(name='platformadmin')

# Common admin actions for policy enforcement
def suspend_user(modeladmin, request, queryset):
    queryset.update(is_active=False)
    messages.success(request, f"Suspended {queryset.count()} user(s)")
suspend_user.short_description = "🚫 Suspend selected users"

def mark_as_verified(modeladmin, request, queryset):
    queryset.update(is_verified=True)
    messages.success(request, f"Verified {queryset.count()} user(s)")
mark_as_verified.short_description = "✅ Mark users as verified"

def flag_content(modeladmin, request, queryset):
    for obj in queryset:
        obj.is_public = False
        obj.save()
    messages.warning(request, f"Flagged {queryset.count()} items for review")
flag_content.short_description = "🚩 Flag content for review"

def flag_message(modeladmin, request, queryset):
    queryset.update(status='flagged')
    messages.warning(request, f"Flagged {queryset.count()} messages for review")
flag_message.short_description = "🚩 Flag for content review"

# User Admin
class UserAdmin(admin.ModelAdmin):
    list_display = ('username', 'email', 'account_status', 'created_at', 'last_seen')
    list_filter = ('is_verified', 'is_active', 'created_at')
    search_fields = ('username', 'email')
    actions = [suspend_user, mark_as_verified]

    def account_status(self, obj):
        if not obj.is_active:
            return format_html('<span style="color: red; font-weight: bold;">⛔ Suspended</span>')
        elif obj.is_verified:
            return format_html('<span style="color: green;">✓ Verified</span>')
        else:
            return format_html('<span style="color: orange;">⚠ Unverified</span>')
    account_status.short_description = "Status"

# Contact Admin
class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'is_resolved', 'created_at')
    list_filter = ('is_resolved', 'created_at')
    search_fields = ('name', 'email')

    def is_resolved(self, obj):
        if obj.is_resolved:
            return format_html('<span style="color: green;">✓ Resolved</span>')
        else:
            return format_html('<span style="color: orange;">⚠ Unresolved</span>')
    is_resolved.short_description = "Status"

    actions = ['resolve_contacts']

    def resolve_contacts(self, request, queryset):
        queryset.update(is_resolved=True)
        messages.success(request, f"Resolved {queryset.count()} contact(s)")
    resolve_contacts.short_description = "✅ Resolve selected contacts"

# Subscriptions Admin
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('email', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('email',)
    readonly_fields = ('created_at',)




# Media File Admin
class MediaFileAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'file_category', 'uploader', 'privacy_status', 'uploaded_at')
    list_filter = ('file_category', 'is_public', 'uploaded_at')
    search_fields = ('original_name', 'uploader__username', 'uploader__email')
    actions = [flag_content]

    def privacy_status(self, obj):
        if obj.is_public:
            return format_html('<span style="color: green;">✓ Public</span>')
        else:
            return format_html('<span style="color: orange;">⚠ Private/Flagged</span>')
    privacy_status.short_description = "Privacy"

# Twin Admin
class TwinAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'privacy_setting', 'twin_status', 'created_at')
    list_filter = ('privacy_setting', 'is_active', 'created_at')
    search_fields = ('name', 'owner__username', 'owner__email')

    actions = ['activate_twins', 'deactivate_twins']

    def twin_status(self, obj):
        if obj.is_active:
            return format_html('<span style="color: green;">✓ Active</span>')
        else:
            return format_html('<span style="color: red;">⛔ Inactive</span>')
    twin_status.short_description = "Status"

    def activate_twins(self, request, queryset):
        queryset.update(is_active=True)
        messages.success(request, f"Activated {queryset.count()} twins")
    activate_twins.short_description = "✅ Activate twins"

    def deactivate_twins(self, request, queryset):
        queryset.update(is_active=False)
        messages.success(request, f"Deactivated {queryset.count()} twins")
    deactivate_twins.short_description = "🚫 Deactivate twins"

# Chat Admin
class UserTwinChatAdmin(admin.ModelAdmin):
    list_display = ('user', 'twin', 'created_at', 'last_active', 'access_status')
    list_filter = ('user_has_access', 'twin_is_active', 'created_at')
    search_fields = ('user__username', 'user__email', 'twin__name')

    actions = ['grant_access', 'revoke_access']

    def access_status(self, obj):
        if obj.user_has_access and obj.twin_is_active:
            return format_html('<span style="color: green;">✓ Active</span>')
        else:
            return format_html('<span style="color: red;">⛔ Restricted</span>')
    access_status.short_description = "Access"

    def grant_access(self, request, queryset):
        queryset.update(user_has_access=True, twin_is_active=True)
        messages.success(request, f"Granted access to {queryset.count()} chats")
    grant_access.short_description = "✅ Grant access"

    def revoke_access(self, request, queryset):
        queryset.update(user_has_access=False)
        messages.warning(request, f"Revoked access from {queryset.count()} chats")
    revoke_access.short_description = "🚫 Revoke access"

# Message Admin
class MessageAdmin(admin.ModelAdmin):
    list_display = ('chat_display', 'message_preview', 'message_status', 'created_at')
    list_filter = ('message_type', 'is_from_user', 'status', 'created_at')
    search_fields = ('text_content', 'chat__user__username', 'chat__twin__name')

    def chat_display(self, obj):
        return f"{obj.chat.user.username} - {obj.chat.twin.name}"
    chat_display.short_description = "Chat"

    def message_preview(self, obj):
        direction = "👤→🤖" if obj.is_from_user else "🤖→👤"
        if obj.message_type == 'text':
            content = obj.text_content[:30] + "..." if obj.text_content and len(obj.text_content) > 30 else obj.text_content
            return f"{direction} {content}"
        elif obj.message_type == 'voice':
            return f"{direction} 🎤 Voice ({obj.duration_seconds}s)"
        elif obj.message_type == 'file':
            file_name = obj.file_attachment.original_name if obj.file_attachment else "Unknown"
            return f"{direction} 📎 File: {file_name}"
        return f"{direction} {obj.get_message_type_display()}"
    message_preview.short_description = "Message"

    def message_status(self, obj):
        if obj.status == 'flagged':
            return format_html('<span style="color: red;">🚩 Flagged</span>')
        elif obj.status == 'read':
            return format_html('<span style="color: green;">✓ Read</span>')
        elif obj.status == 'delivered':
            return format_html('<span style="color: blue;">✓ Delivered</span>')
        else:
            return format_html('<span style="color: gray;">Sent</span>')
    message_status.short_description = "Status"

    def has_add_permission(self, request):
        return False


class MessageReportAdmin(admin.ModelAdmin):
    list_display = ('message_preview', 'reported_by', 'reason', 'review_status', 'created_at')
    list_filter = ('reason', 'is_reviewed', 'created_at')
    search_fields = ('message__text_content', 'reported_by__username', 'details')
    actions = ['mark_as_reviewed', 'flag_reported_message']

    def message_preview(self, obj):
        content = obj.message.text_content[:50] + "..." if obj.message.text_content and len(obj.message.text_content) > 50 else obj.message.text_content
        return f"{content}"
    message_preview.short_description = "Reported Message"

    def review_status(self, obj):
        if obj.is_reviewed:
            return format_html('<span style="color: green;">✓ Reviewed</span>')
        return format_html('<span style="color: orange;">⏳ Pending Review</span>')
    review_status.short_description = "Status"

    def mark_as_reviewed(self, request, queryset):
        queryset.update(is_reviewed=True, reviewed_at=timezone.now())
        messages.success(request, f"Marked {queryset.count()} reports as reviewed")
    mark_as_reviewed.short_description = "✅ Mark as reviewed"

    def flag_reported_message(self, request, queryset):
        for report in queryset:
            report.message.status = 'flagged'
            report.message.save()
        messages.warning(request, f"Flagged {queryset.count()} reported messages")
    flag_reported_message.short_description = "🚩 Flag reported messages"


# Voice Recording Admin
class VoiceRecordingAdmin(admin.ModelAdmin):
    list_display = ('id', 'duration_seconds', 'format', 'processing_status', 'created_at')
    list_filter = ('is_processed', 'format', 'created_at')
    search_fields = ('id', 'transcription')

    def processing_status(self, obj):
        if obj.is_processed:
            return format_html('<span style="color: green;">✓ Processed</span>')
        else:
            return format_html('<span style="color: orange;">⏳ Pending</span>')
    processing_status.short_description = "Processing"

# Twin Access Admin
class TwinAccessAdmin(admin.ModelAdmin):
    list_display = ('user', 'twin', 'granted_at', 'grant_expires', 'access_status')
    list_filter = ('granted_at',)
    search_fields = ('user__username', 'user__email', 'twin__name')

    def access_status(self, obj):
        import datetime
        if obj.grant_expires and obj.grant_expires < timezone.now():
            return format_html('<span style="color: red;">⛔ Expired</span>')
        return format_html('<span style="color: green;">✓ Active</span>')
    access_status.short_description = "Status"

# Create custom admin site
platform_admin = PlatformAdminSite(name='platformadmin')

# Register models with custom admin
platform_admin.register(User, UserAdmin)
platform_admin.register(AuthToken)
platform_admin.register(MediaFile, MediaFileAdmin)
platform_admin.register(Twin, TwinAdmin)
platform_admin.register(UserTwinChat, UserTwinChatAdmin)
platform_admin.register(VoiceRecording, VoiceRecordingAdmin)
platform_admin.register(Message, MessageAdmin)
platform_admin.register(TwinAccess, TwinAccessAdmin)

# Also register with default admin
admin.site.register(User, UserAdmin)
admin.site.register(AuthToken)
admin.site.register(MediaFile, MediaFileAdmin)
admin.site.register(Twin, TwinAdmin)
admin.site.register(UserTwinChat, UserTwinChatAdmin)
admin.site.register(VoiceRecording, VoiceRecordingAdmin)
admin.site.register(Message, MessageAdmin)
admin.site.register(MessageReport, MessageReportAdmin)
admin.site.register(TwinAccess, TwinAccessAdmin)
admin.site.register(Subscription, SubscriptionAdmin)
admin.site.register(Contact, ContactAdmin)
