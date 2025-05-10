from django.urls import path
from . import views

urlpatterns = [
    path('contacts/', views.ContactList.as_view()),
    path('contacts/<int:pk>/', views.ContactDetail.as_view()),
    path('subscribes/', views.SubscriptionList.as_view()),
    path('subscribes/<int:pk>/', views.SubscriptionDetail.as_view()),
]