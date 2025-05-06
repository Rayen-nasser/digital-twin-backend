from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import SharedWithMeViewSet, TwinViewSet

router = DefaultRouter()
router.register(r'', TwinViewSet, basename='twin')
router.register(r'shared-twins', SharedWithMeViewSet, basename='shared-twin')

urlpatterns = [
    path('', include(router.urls)),
]
