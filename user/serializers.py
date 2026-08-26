from rest_framework import serializers
from .models import CustomUser, Profile

class ProfileSerializer(serializers.ModelSerializer):
    active_role = serializers.SerializerMethodField()
    phone = serializers.CharField(source='user.phone', read_only=True)

    class Meta:
        model = Profile
        fields = [
            'id', 'user', 'first_name', 'surname', 'full_name', 'email', 'is_email_verified',
            'date_of_birth', 'phone', 'profile_picture', 'cultural_background', 
            'religious_affiliation', 'traditional_names', 'spiritual_beliefs', 'bio', 
            'is_complete', 'is_deceased', 'is_active', 'date_of_death',
            'active_role', 'is_verified'
        ]
        read_only_fields = ['user', 'full_name', 'phone', 'is_complete', 'active_role', 'is_verified']

    def validate_email(self, value):
        """Convert empty string to None so unique=True doesn't fire for blank emails."""
        if not value or not value.strip():
            return None
        return value.strip()

    def get_active_role(self, obj):
        try:
            from chema.models import GroupMembership
            membership = GroupMembership.objects.filter(member=obj, is_active=True).first()
            return membership.role if membership else None
        except Exception:
            return None

class UserSerializer(serializers.ModelSerializer):
    profile = serializers.SerializerMethodField()
    active_role = serializers.SerializerMethodField()

    class Meta:
        model = CustomUser
        fields = ['id', 'phone', 'email', 'profile', 'date_joined', 'active_role']
        read_only_fields = ['date_joined']

    def get_profile(self, obj):
        if hasattr(obj, 'profile') and obj.profile:
            return ProfileSerializer(obj.profile, context=self.context).data
        return None

    def get_active_role(self, obj):
        try:
            from chema.models import GroupMembership
            membership = GroupMembership.objects.filter(member=obj.profile, is_active=True).first()
            return membership.role if membership else None
        except Exception:
            return None

class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = CustomUser
        fields = ['email', 'password']

    def create(self, validated_data):
        user = CustomUser.objects.create_user(
            email=validated_data['email'],
            password=validated_data['password']
        )
        user.is_active = True # Activating by default for mobile API for now
        user.save()
        return user

from .models import DeviceToken, Notification

class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ['token', 'platform']

class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ['id', 'recipient', 'title', 'message', 'data', 'is_read', 'created_at', 'notification_type']
        read_only_fields = ['id', 'recipient', 'created_at']

