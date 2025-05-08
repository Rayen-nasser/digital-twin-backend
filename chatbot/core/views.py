from django.shortcuts import render, redirect
from django.utils import timezone
from django.db.models import Count, Q
from django.contrib import messages
from datetime import timedelta
import json
from core.models import User, MediaFile, Twin, Message, UserTwinChat

from django.shortcuts import render, redirect
from django.utils import timezone
from django.db.models import Count, Q
from django.contrib import messages
from datetime import timedelta
import json
from core.models import User, MediaFile, Twin, Message, UserTwinChat

from django.shortcuts import render, redirect
from django.utils import timezone
from django.db.models import Count, Q
from django.contrib import messages
from datetime import timedelta
import json
from django.contrib.admin.views.decorators import staff_member_required
from core.models import User, MediaFile, Twin, Message, UserTwinChat

@staff_member_required
def platform_monitor_view(request):
    # Time periods for analysis
    today = timezone.now()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=7)
    month_ago = today - timedelta(days=30)

    # User metrics
    total_users = User.objects.count()
    active_users_today = User.objects.filter(last_seen__gte=yesterday).count()
    active_users_week = User.objects.filter(last_seen__gte=week_ago).count()
    active_users_month = User.objects.filter(last_seen__gte=month_ago).count()
    new_users_today = User.objects.filter(created_at__gte=yesterday).count()
    new_users_week = User.objects.filter(created_at__gte=week_ago).count()
    new_users_month = User.objects.filter(created_at__gte=month_ago).count()

    # Content metrics
    total_messages = Message.objects.count()
    messages_today = Message.objects.filter(created_at__gte=yesterday).count()
    messages_week = Message.objects.filter(created_at__gte=week_ago).count()
    messages_month = Message.objects.filter(created_at__gte=month_ago).count()

    # Twin metrics
    total_twins = Twin.objects.count()
    active_twins = Twin.objects.filter(is_active=True).count()
    inactive_twins = total_twins - active_twins

    # Media metrics
    total_media = MediaFile.objects.count()
    media_by_type = MediaFile.objects.values('file_category').annotate(count=Count('id'))

    # Calculate percentages for media types
    if total_media > 0:
        for item in media_by_type:
            item['percentage'] = (item['count'] / total_media) * 100

    # Platform health indicators
    suspended_users = User.objects.filter(is_active=False).count()
    private_content = MediaFile.objects.filter(is_public=False).count()

    # Create graph data for messages per day (limited to 7 days for better readability)
    message_trend = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_count = Message.objects.filter(
            created_at__date=day.date()
        ).count()
        message_trend.append({
            'date': day.strftime('%m-%d'),
            'count': day_count
        })

    # Create graph data for user registrations (limited to 7 days for better readability)
    user_reg_trend = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_count = User.objects.filter(
            created_at__date=day.date()
        ).count()
        user_reg_trend.append({
            'date': day.strftime('%m-%d'),
            'count': day_count
        })

    # Calculate rates
    active_users_today_rate = (active_users_today / total_users * 100) if total_users > 0 else 0
    active_users_week_rate = (active_users_week / total_users * 100) if total_users > 0 else 0
    active_users_month_rate = (active_users_month / total_users * 100) if total_users > 0 else 0
    active_twins_rate = (active_twins / total_twins * 100) if total_twins > 0 else 0
    suspended_users_rate = (suspended_users / total_users * 100) if total_users > 0 else 0

    context = {
        'title': 'Platform Monitoring',
        # User stats
        'total_users': total_users,
        'active_users_today': active_users_today,
        'active_users_week': active_users_week,
        'active_users_month': active_users_month,
        'new_users_today': new_users_today,
        'new_users_week': new_users_week,
        'new_users_month': new_users_month,

        # Content stats
        'total_messages': total_messages,
        'messages_today': messages_today,
        'messages_week': messages_week,
        'messages_month': messages_month,

        # Twin stats
        'total_twins': total_twins,
        'active_twins': active_twins,
        'inactive_twins': inactive_twins,
        'active_twins_rate': active_twins_rate,

        # Media stats
        'total_media': total_media,
        'media_by_type': list(media_by_type),

        # System health
        'suspended_users': suspended_users,
        'private_content': private_content,
        'suspended_users_rate': suspended_users_rate,

        # Graph data (JSON for charts)
        'message_trend_json': json.dumps(message_trend),
        'user_reg_trend_json': json.dumps(user_reg_trend),

        # Engagement rates
        'active_users_today_rate': active_users_today_rate,
        'active_users_week_rate': active_users_week_rate,
        'active_users_month_rate': active_users_month_rate,
    }

    return render(request, 'admin/platform_monitor.html', context)

@staff_member_required
def abuse_report_view(request):
    # Get flagged content
    flagged_messages = Message.objects.filter(
        Q(status='flagged') | Q(report_count__gt=0)
    ).order_by('-created_at')[:10]

    flagged_media = MediaFile.objects.filter(
        is_public=False
    ).order_by('-uploaded_at')[:10]

    # Users under review
    problematic_users = User.objects.filter(
        Q(is_active=False) | Q(warning_count__gt=0)
    ).order_by('-created_at')[:10]

    context = {
        'title': 'Abuse Reports',
        'flagged_messages': flagged_messages,
        'flagged_media': flagged_media,
        'problematic_users': problematic_users,
    }

    return render(request, 'admin/abuse_report.html', context)

@staff_member_required
def policy_enforcement_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'suspend_users':
            user_ids = request.POST.getlist('user_ids')
            User.objects.filter(id__in=user_ids).update(is_active=False)
            messages.success(request, f"Successfully suspended {len(user_ids)} users")

        elif action == 'remove_content':
            media_ids = request.POST.getlist('media_ids')
            MediaFile.objects.filter(id__in=media_ids).update(is_public=False)
            messages.success(request, f"Successfully restricted {len(media_ids)} media files")

        elif action == 'deactivate_twins':
            twin_ids = request.POST.getlist('twin_ids')
            Twin.objects.filter(id__in=twin_ids).update(is_active=False)
            messages.success(request, f"Successfully deactivated {len(twin_ids)} twins")

    # Get users for potential enforcement
    recent_users = User.objects.all().order_by('-created_at')[:20]
    recent_media = MediaFile.objects.all().order_by('-uploaded_at')[:20]
    recent_twins = Twin.objects.all().order_by('-created_at')[:20]

    context = {
        'title': 'Policy Enforcement',
        'recent_users': recent_users,
        'recent_media': recent_media,
        'recent_twins': recent_twins,
    }

    return render(request, 'admin/policy_enforcement.html', context)

def abuse_report_view(request):
    # Get flagged content
    flagged_messages = Message.objects.filter(
        Q(status='flagged') | Q(report_count__gt=0)
    ).order_by('-created_at')[:10]

    flagged_media = MediaFile.objects.filter(
        is_public=False
    ).order_by('-uploaded_at')[:10]

    # Users under review
    problematic_users = User.objects.filter(
        Q(is_active=False) | Q(warning_count__gt=0)
    ).order_by('-created_at')[:10]

    context = {
        'title': 'Abuse Reports',
        'flagged_messages': flagged_messages,
        'flagged_media': flagged_media,
        'problematic_users': problematic_users,
    }

    return render(request, 'admin/abuse_report.html', context)

def policy_enforcement_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'suspend_users':
            user_ids = request.POST.getlist('user_ids')
            User.objects.filter(id__in=user_ids).update(is_active=False)
            messages.success(request, f"Successfully suspended {len(user_ids)} users")

        elif action == 'remove_content':
            media_ids = request.POST.getlist('media_ids')
            MediaFile.objects.filter(id__in=media_ids).update(is_public=False)
            messages.success(request, f"Successfully restricted {len(media_ids)} media files")

        elif action == 'deactivate_twins':
            twin_ids = request.POST.getlist('twin_ids')
            Twin.objects.filter(id__in=twin_ids).update(is_active=False)
            messages.success(request, f"Successfully deactivated {len(twin_ids)} twins")

    # Get users for potential enforcement
    recent_users = User.objects.all().order_by('-created_at')[:20]
    recent_media = MediaFile.objects.all().order_by('-uploaded_at')[:20]
    recent_twins = Twin.objects.all().order_by('-created_at')[:20]

    context = {
        'title': 'Policy Enforcement',
        'recent_users': recent_users,
        'recent_media': recent_media,
        'recent_twins': recent_twins,
    }

    return render(request, 'admin/policy_enforcement.html', context)
def abuse_report_view(request):
    # Get flagged content
    flagged_messages = Message.objects.filter(
        Q(status='flagged') | Q(report_count__gt=0)
    ).order_by('-created_at')[:10]

    flagged_media = MediaFile.objects.filter(
        is_public=False
    ).order_by('-uploaded_at')[:10]

    # Users under review
    problematic_users = User.objects.filter(
        Q(is_active=False) | Q(warning_count__gt=0)
    ).order_by('-created_at')[:10]

    context = {
        'title': 'Abuse Reports',
        'flagged_messages': flagged_messages,
        'flagged_media': flagged_media,
        'problematic_users': problematic_users,
    }

    return render(request, 'admin/abuse_report.html', context)

def policy_enforcement_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'suspend_users':
            user_ids = request.POST.getlist('user_ids')
            User.objects.filter(id__in=user_ids).update(is_active=False)
            messages.success(request, f"Successfully suspended {len(user_ids)} users")

        elif action == 'remove_content':
            media_ids = request.POST.getlist('media_ids')
            MediaFile.objects.filter(id__in=media_ids).update(is_public=False)
            messages.success(request, f"Successfully restricted {len(media_ids)} media files")

        elif action == 'deactivate_twins':
            twin_ids = request.POST.getlist('twin_ids')
            Twin.objects.filter(id__in=twin_ids).update(is_active=False)
            messages.success(request, f"Successfully deactivated {len(twin_ids)} twins")

    # Get users for potential enforcement
    recent_users = User.objects.all().order_by('-created_at')[:20]
    recent_media = MediaFile.objects.all().order_by('-uploaded_at')[:20]
    recent_twins = Twin.objects.all().order_by('-created_at')[:20]

    context = {
        'title': 'Policy Enforcement',
        'recent_users': recent_users,
        'recent_media': recent_media,
        'recent_twins': recent_twins,
    }

    return render(request, 'admin/policy_enforcement.html', context)