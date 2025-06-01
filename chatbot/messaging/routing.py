# messaging/routing.py

from django.urls import re_path
from . import consumers
from messaging import consumers, dt_chat_consumer

websocket_urlpatterns = [
    re_path(r'ws/chat/(?P<chat_id>[^/]+)/$', dt_chat_consumer.DigitalTwinChatConsumer.as_asgi()),
    re_path(r'api/v1/messaging/ws/chat/(?P<chat_id>[^/]+)/$', dt_chat_consumer.DigitalTwinChatConsumer.as_asgi()),
]