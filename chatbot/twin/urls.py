from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import TwinViewSet

router = DefaultRouter()
router.register(r'', TwinViewSet, basename='twin')

urlpatterns = [
    path('', include(router.urls)),
]
