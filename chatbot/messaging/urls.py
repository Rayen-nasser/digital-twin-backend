from django.urls import path, include, re_path
from rest_framework.routers import DefaultRouter

from messaging import dt_chat_consumer
from .views import MessageViewSet, UserTwinChatViewSet, VoiceRecordingViewSet

router = DefaultRouter()
router.register(r'chats', UserTwinChatViewSet, basename='chat')
router.register(r'messages', MessageViewSet, basename='message')
router.register(r'voice-recordings', VoiceRecordingViewSet, basename='voice-recording')

urlpatterns = [
    path('', include(router.urls)),
    re_path(r'ws/chat/(?P<chat_id>[^/]+)/$', dt_chat_consumer.DigitalTwinChatConsumer.as_asgi()),
]
