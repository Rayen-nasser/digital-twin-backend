# core/db_routers.py
class AuthRouter:
    """Route auth-related models to PostgreSQL"""

    def db_for_read(self, model, **hints):
        if model._meta.app_label == 'auth' or model._meta.model_name == 'user':
            return 'default'  # PostgreSQL
        return None

    def db_for_write(self, model, **hints):
        if model._meta.app_label == 'auth' or model._meta.model_name == 'user':
            return 'default'  # PostgreSQL
        return None

class MongoRouter:
    """Route certain models to MongoDB"""

    def db_for_read(self, model, **hints):
        if model._meta.model_name in ['message', 'chathistory']:
            return 'mongodb'
        return None

    def db_for_write(self, model, **hints):
        if model._meta.model_name in ['message', 'chathistory']:
            return 'mongodb'
        return None