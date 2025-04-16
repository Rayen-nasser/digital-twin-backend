from rest_framework import serializers
from core.models import Twin, MediaFile, User
from rest_framework.exceptions import ValidationError

class TwinSerializer(serializers.ModelSerializer):
    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        default=serializers.CurrentUserDefault()
    )

    avatar_details = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Twin
        fields = [
            'id', 'name', 'owner', 'description', 'avatar', 'avatar_details',
            'communication_style', 'privacy_setting', 'created_at',
            'updated_at', 'is_active'
        ]
        read_only_fields = ['created_at', 'updated_at', 'avatar_details']
        extra_kwargs = {
            'avatar': {'required': False, 'allow_null': True},
            'description': {'required': False, 'allow_blank': True},
        }

    def get_avatar_details(self, obj):
        if not obj.avatar:
            return None
        return {
            'id': str(obj.avatar.id),
            'filename': obj.avatar.filename,
            'url': obj.avatar.path  # You might want to generate a proper URL here
        }

    def validate(self, data):
        # Ensure the owner can't be changed
        if self.instance and 'owner' in data and self.instance.owner != data['owner']:
            raise ValidationError("You cannot change the owner of a twin.")

        # Validate communication_style structure
        if 'communication_style' in data:
            style = data['communication_style']
            if not isinstance(style, dict):
                raise ValidationError("Communication style must be a JSON object.")

            # Add more specific validation if needed
            valid_keys = {'formality', 'humor_level', 'response_speed'}
            if not all(key in valid_keys for key in style.keys()):
                raise ValidationError("Invalid keys in communication style")

        return data

class TwinListSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(source='owner.username', read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = Twin
        fields = [
            'id', 'name', 'owner_username', 'description',
            'avatar_url', 'privacy_setting', 'created_at'
        ]

    def get_avatar_url(self, obj):
        if obj.avatar:
            return obj.avatar.path  # Again, use proper URL generation
        return None