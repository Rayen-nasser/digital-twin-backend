# chatbot/test_settings.py
from .settings import *

# Override database settings for testing
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    },
}

# Disable the router during tests
DATABASE_ROUTERS = []