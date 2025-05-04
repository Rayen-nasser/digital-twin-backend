
from urllib.parse import parse_qs
from channels.db import database_sync_to_async
from rest_framework_simplejwt.tokens import UntypedToken
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from django.contrib.auth.models import AnonymousUser
import jwt
import os


class JwtAuthMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        # Extract token from query string
        query_string = scope['query_string'].decode()
        query_params = parse_qs(query_string)
        token = query_params.get('token')

        if token:
            token = token[0]  # Get the first value
            try:
                decoded_data = jwt.decode(
                                    token,
                                    os.environ.get('JWT_SIGNING_KEY'),  # Make sure to use the same key
                                    algorithms=["HS256"],
                                )
                user = await self.get_user(decoded_data['user_id'])
                scope['user'] = user
            except (InvalidToken, TokenError, jwt.ExpiredSignatureError, jwt.DecodeError) as e:
                scope['user'] = AnonymousUser()

        return await self.inner(scope, receive, send)

    @database_sync_to_async
    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return AnonymousUser()
