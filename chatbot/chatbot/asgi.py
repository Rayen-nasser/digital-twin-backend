# chatbot/asgi.py - ASGI Configuration Explained

"""
ASGI (Asynchronous Server Gateway Interface) Configuration
========================================================

This file configures how your Django application handles different types of connections:
- HTTP requests (traditional web requests)
- WebSocket connections (real-time chat functionality)

Think of it as the "traffic director" for your application.
"""

import os
import django

# STEP 1: Initialize Django Framework
# ===================================
# This MUST happen before importing any Django components
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chatbot.settings')
django.setup()  # Critical: Initializes Django's app registry and settings

# STEP 2: Import Components (only after Django is ready)
# =====================================================
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
import messaging.routing
from messaging.middleware import JwtAuthMiddleware

# STEP 3: Configure Protocol Router
# =================================
application = ProtocolTypeRouter({
    # Handle HTTP requests (normal web pages, API calls)
    'http': get_asgi_application(),

    # Handle WebSocket connections (real-time chat)
    'websocket': AllowedHostsOriginValidator(
        JwtAuthMiddleware(
            URLRouter(
                messaging.routing.websocket_urlpatterns
            )
        )
    ),
})

"""
COMPONENT BREAKDOWN:
==================

1. ProtocolTypeRouter
   - Routes different connection types to appropriate handlers
   - 'http': Traditional HTTP requests → Django's standard ASGI app
   - 'websocket': WebSocket connections → Custom WebSocket routing

2. AllowedHostsOriginValidator (Security Layer)
   - Validates WebSocket connections come from allowed origins
   - Prevents Cross-Site WebSocket Hijacking attacks
   - Only allows connections from hosts in ALLOWED_HOSTS setting

3. JwtAuthMiddleware (Authentication Layer)
   - Custom middleware for JWT token authentication
   - Authenticates users for WebSocket connections
   - Likely extracts user info from JWT tokens

4. URLRouter
   - Routes WebSocket connections to specific consumers
   - Uses patterns from messaging.routing.websocket_urlpatterns
   - Similar to Django's URL routing but for WebSockets

SECURITY LAYERS (Inside → Outside):
=================================
WebSocket Request → AllowedHostsOriginValidator → JwtAuthMiddleware → URLRouter → ChatConsumer

TYPICAL FLOW:
============
1. Client connects to WebSocket endpoint
2. AllowedHostsOriginValidator checks if origin is allowed
3. JwtAuthMiddleware authenticates the user via JWT
4. URLRouter matches the WebSocket URL to appropriate consumer
5. ChatConsumer handles the actual chat functionality
"""