# contact/views.py
from rest_framework import generics
from core.models import Contact, Subscription
from .serializers import ContactSerializer, SubscriptionSerializer

class ContactList(generics.ListCreateAPIView):
    authentication_classes = []
    permission_classes = []
    queryset = Contact.objects.all()
    serializer_class = ContactSerializer

class ContactDetail(generics.RetrieveAPIView):
    authentication_classes = []
    permission_classes = []
    queryset = Contact.objects.all()
    serializer_class = ContactSerializer

class SubscriptionList(generics.ListCreateAPIView):
    authentication_classes = []
    permission_classes = []
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializer

class SubscriptionDetail(generics.RetrieveAPIView):
    authentication_classes = []
    permission_classes = []
    queryset = Subscription.objects.all()
    serializer_class = SubscriptionSerializer
