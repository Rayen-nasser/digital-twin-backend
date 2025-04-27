from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Admin Panel
    path('admin/', admin.site.urls),

    # API URLs
    path('api/v1/auth/', include('user.urls')),   # Auth routes (register, login, etc.)
    path('api/v1/twin/', include('twin.urls')),   # Twin-related routes
    path('api/v1/messaging/', include('messaging.urls')),

    # Schema and Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),  # OpenAPI schema (YAML/JSON)
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),  # Swagger UI
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),        # ReDoc UI
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)