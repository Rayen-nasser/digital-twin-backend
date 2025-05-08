from django.urls import path
from . import views

urlpatterns = [
    path('contacts/', views.ContactList.as_view()),
    path('contacts/<int:pk>/', views.ContactDetail.as_view()),
    path('subscriptions/', views.SubscriptionList.as_view()),
    path('subscriptions/<int:pk>/', views.SubscriptionDetail.as_view()),
]