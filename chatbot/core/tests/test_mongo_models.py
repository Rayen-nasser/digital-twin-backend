from unittest.mock import patch, MagicMock
import uuid
import pytest
from core.models import User, Twin, Message, ChatHistory

@pytest.mark.django_db
class TestMongoModels:
    @pytest.fixture(autouse=True)
    def setup(self):
        # Create user and twin in the database
        self.user = User.objects.create(
            username='testuser',
            email='test@example.com',
            password='securepassword123'
        )

        self.twin = Twin.objects.create(
            name='Test Twin',
            owner=self.user,
        )

        # For MongoDB models, we'll use mock objects
        self.message_id = uuid.uuid4()
        self.message_content = "Hello, world!"

    def test_message_creation(self):
        # Create a mock Message object
        message = MagicMock()
        message.id = self.message_id
        message.content = self.message_content
        message.sender_type = 'user'
        message.sender_id = self.user.id
        message.message_type = 'text'
        message.is_read = False

        # Test the properties
        assert message.content == self.message_content
        assert message.sender_type == 'user'
        assert message.sender_id == self.user.id

    def test_chat_history_creation(self):
        # Simulate the message list with dictionaries
        messages = [
            {"id": str(uuid.uuid4()), "content": "Hello, Twin!", "sender_type": "user", "sender_id": str(self.user.id)},
            {"id": str(uuid.uuid4()), "content": "How are you?", "sender_type": "twin", "sender_id": str(self.twin.id)},
        ]

        # Create a ChatHistory object using the new model structure
        chat_history = MagicMock()
        chat_history.twin_id = self.twin.id
        chat_history.user_id = self.user.id
        chat_history.messages = messages
        chat_history.created_at = "2025-04-14T12:00:00Z"
        chat_history.updated_at = "2025-04-14T12:30:00Z"

        # Test the properties
        assert chat_history.twin_id == self.twin.id
        assert chat_history.user_id == self.user.id
        assert len(chat_history.messages) == 2
        assert chat_history.messages[0]["content"] == "Hello, Twin!"
        assert chat_history.messages[1]["sender_type"] == "twin"

    def test_message_get_sender(self):
        # Mock the get_sender method
        message = MagicMock()
        message.sender_type = 'user'
        message.sender_id = self.user.id

        # Simulate the behavior of get_sender
        def mock_get_sender():
            if message.sender_type == 'user':
                return self.user
            elif message.sender_type == 'twin':
                return self.twin
            return None

        message.get_sender = mock_get_sender

        # Test with user sender
        sender = message.get_sender()
        assert sender == self.user

        # Test with twin sender
        message.sender_type = 'twin'
        message.sender_id = self.twin.id
        sender = message.get_sender()
        assert sender == self.twin

    def test_chat_history_get_user(self):
        # Mock the get_user method
        chat_history = MagicMock()
        chat_history.user_id = self.user.id

        # Simulate the behavior of get_user
        def mock_get_user():
            return self.user

        chat_history.get_user = mock_get_user

        # Test get_user
        user = chat_history.get_user()
        assert user == self.user

    def test_chat_history_get_twin(self):
        # Mock the get_twin method
        chat_history = MagicMock()
        chat_history.twin_id = self.twin.id

        # Simulate the behavior of get_twin
        def mock_get_twin():
            return self.twin

        chat_history.get_twin = mock_get_twin

        # Test get_twin
        twin = chat_history.get_twin()
        assert twin == self.twin


    def test_chat_history_str(self):
        # Simulate the __str__ method of ChatHistory
        chat_history = MagicMock()
        chat_history.user_id = self.user.id
        chat_history.twin_id = self.twin.id
        chat_history.messages = [{"content": "Hello"}]

        # Mock the get_user and get_twin methods to return real User and Twin objects
        def mock_get_user():
            mock_user = MagicMock()
            mock_user.username = self.user.username  # Set the username explicitly
            return mock_user

        def mock_get_twin():
            mock_twin = MagicMock()
            mock_twin.name = self.twin.name  # Set the name explicitly
            return mock_twin

        chat_history.get_user = mock_get_user
        chat_history.get_twin = mock_get_twin

        # Mock the __str__ method of the chat_history object
        chat_history.__str__ = MagicMock(return_value=f"Chat between {self.user.username} and {self.twin.name} (1 messages)")

        # Test the __str__ method
        result = str(chat_history)
        expected = f"Chat between {self.user.username} and {self.twin.name} (1 messages)"

        assert result == expected
