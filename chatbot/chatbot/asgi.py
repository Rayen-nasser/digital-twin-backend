# chatbot/asgi.py
import os
import django

# Set up Django BEFORE any imports that might use Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chatbot.settings')
django.setup()  # This is crucial - it initializes Django before other imports

# Only import after Django is set up
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
import messaging.routing  # Import the entire module
from messaging.middleware import JwtAuthMiddleware

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': AllowedHostsOriginValidator(
        JwtAuthMiddleware(
            URLRouter(
                messaging.routing.websocket_urlpatterns
            )
        )
    ),
})