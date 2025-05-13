import os
from pathlib import Path
from datetime import timedelta
from decouple import config, Csv
from dotenv import load_dotenv

# Load environment variables from a .env file, if it exists
load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent.parent

# ======================== Core Settings ======================== #
SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', cast=Csv(), default='*')

# ======================== Applications ======================== #
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',  # Add PostgreSQL specific features

    # Third-party
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',
    'django_filters',
    'corsheaders',
    'django_extensions',
    'channels',

    # Your apps
    'core',
]

# ======================== Middleware ======================== #
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'chatbot.urls'

# ======================== Templates ======================== #
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'chatbot.wsgi.application'

# ======================== PostgreSQL Database ======================== #
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': config('POSTGRES_DB_NAME'),
        'USER': config('POSTGRES_USER'),
        'PASSWORD': config('POSTGRES_PASSWORD'),
        'HOST': config('POSTGRES_HOST'),
        'PORT': config('POSTGRES_PORT'),
        'OPTIONS': {
            'connect_timeout': 5,
            'options': '-c search_path=public,chat_schema',  # Schema support
            'application_name': 'chatbot_app',  # Helpful for PG monitoring
        },
        'CONN_MAX_AGE': 60 * 5,  # 5 minutes connection persistence
    }
}

# Remove all MongoDB-related database routers
DATABASE_ROUTERS = []

# ======================== PostgreSQL Extensions ======================== #
POSTGRES_EXTENSIONS = [
    'pg_trgm',  # For text search
    'uuid-ossp',  # For UUID generation
    'pgcrypto',  # For encryption functions
]

# ======================== REST Framework ======================== #
REST_FRAMEWORK = {
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle'
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
    },
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
    ],
}

# ======================== JWT Configuration ======================== #
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=7),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=15),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': config('JWT_SIGNING_KEY', default=SECRET_KEY),
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# ======================== OpenAPI/Swagger ======================== #
SPECTACULAR_SETTINGS = {
    'TITLE': 'Chatbot API',
    'DESCRIPTION': 'API for 1:1 user-twin chat system',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SCHEMA_PATH_PREFIX': r'/api/v[0-9]',
}

# ======================== CORS Settings ======================== #
CORS_ALLOWED_ORIGINS = config('CORS_ALLOWED_ORIGINS', default='http://localhost:4200,http://127.0.0.1:8000', cast=Csv())

CORS_EXPOSE_HEADERS = ['Content-Disposition']

# ======================== Static & Media Files ======================== #
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ======================== File Storage ======================== #
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage' if config('USE_S3', default=False, cast=bool) else 'django.core.files.storage.FileSystemStorage'

if DEFAULT_FILE_STORAGE == 'storages.backends.s3boto3.S3Boto3Storage':
    AWS_ACCESS_KEY_ID = config('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY')
    AWS_STORAGE_BUCKET_NAME = config('AWS_STORAGE_BUCKET_NAME')
    AWS_S3_REGION_NAME = config('AWS_S3_REGION_NAME')
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = 'private'
    AWS_S3_SIGNATURE_VERSION = 's3v4'

# ======================== Authentication ======================== #
AUTH_USER_MODEL = 'core.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ======================== Email Configuration ======================== #
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.smtp.EmailBackend')
EMAIL_HOST = config('EMAIL_HOST')
EMAIL_PORT = config('EMAIL_PORT', cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL')
FRONTEND_URL = config('FRONTEND_URL')
ASSEMBLY_AI_API_KEY= config('ASSEMBLY_AI_API_KEY')
GOFILE_TOKEN = config('GOFILE_TOKEN')

# ======================== Internationalization ======================== #
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ======================== Default Auto Field ======================== #
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ======================== Custom Settings ======================== #
# Chat message retention policy (in days)
MESSAGE_RETENTION_DAYS = config('MESSAGE_RETENTION_DAYS', default=30, cast=int)

# File upload limits
MAX_FILE_UPLOAD_SIZE = 25 * 1024 * 1024  # 25MB
ALLOWED_FILE_TYPES = ['image/jpeg', 'image/png', 'application/pdf', 'audio/mpeg']

# ======================== PostgreSQL Optimization ======================== #
# Connection pool configuration
DATABASE_CONNECTION_POOL = {
    'max_connections': config('DB_MAX_CONNECTIONS', default=20, cast=int),
    'max_overflow': config('DB_MAX_OVERFLOW', default=10, cast=int),
    'recycle': config('DB_CONN_RECYCLE', default=300, cast=int),
}

# Configure database pool if needed
if config('USE_DB_POOL', default=False, cast=bool):
    DATABASES['default']['ENGINE'] = 'django_postgrespool2'


ASGI_APPLICATION = 'chatbot.asgi.application'

# Channel layers configuration
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [("127.0.0.1", 6379)],
        },
    },
}


# If using WhiteNoise in production, add this configuration
if not DEBUG:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'