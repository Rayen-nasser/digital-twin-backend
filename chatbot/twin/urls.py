from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register(r'twins', views.TwinViewSet, basename='twin')

urlpatterns = [
    path('', include(router.urls)),
]
