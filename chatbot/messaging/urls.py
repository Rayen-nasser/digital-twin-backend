from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import MessageViewSet, UserTwinChatViewSet, VoiceRecordingViewSet

router = DefaultRouter()
router.register(r'chats', UserTwinChatViewSet, basename='chat')
router.register(r'messages', MessageViewSet, basename='message')
router.register(r'voice-recordings', VoiceRecordingViewSet, basename='voice-recording')

urlpatterns = [
    path('', include(router.urls)),
]