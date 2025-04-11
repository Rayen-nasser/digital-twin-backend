# core/models.py
from djongo import models as mongo_models
from django.db import models as django_models
from django.contrib.auth.models import AbstractUser
from djongo.models.fields import ObjectIdField

# PostgreSQL Model
class User(AbstractUser):
    chat_preferences = django_models.JSONField(default=dict)



# MongoDB Model
class Conversation(mongo_models.Model):
    _id = ObjectIdField()
    user_id = mongo_models.IntegerField()
    messages = mongo_models.JSONField(default=list)

    class Meta:
        db_table = 'conversations'