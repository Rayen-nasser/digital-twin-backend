from django.urls import path
from . import views

urlpatterns = [
    path('platform-monitor/', views.platform_monitor_view, name='platform-monitor'),
    path('abuse-report/', views.abuse_report_view, name='abuse-report'),
    path('policy-enforcement/', views.policy_enforcement_view, name='policy-enforcement'),
]