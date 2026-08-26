# customuser/models.py
from django.db import models
from django.contrib.auth.models import User
from PIL import Image
from django.core.files import File
from io import BytesIO
from django.conf import settings
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver


class CustomUserManager(BaseUserManager):
    def create_user(self, phone=None, password=None, **extra_fields):
        email = extra_fields.get("email")
        if not phone and not email:
            raise ValueError("Either Phone number or Email must be set")
        user = self.model(phone=phone, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(phone=phone, password=password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    phone       = models.CharField(max_length=20, unique=True, null=True, blank=True)
    email       = models.EmailField(unique=True, null=True, blank=True)
    pin         = models.CharField(max_length=128, null=True, blank=True)
    is_staff    = models.BooleanField(default=False)
    is_active   = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    # Phone verification field
    is_phone_verified = models.BooleanField(default=False)

    USERNAME_FIELD  = "phone"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def set_pin(self, raw_pin):
        from django.contrib.auth.hashers import make_password
        self.pin = make_password(str(raw_pin))
        self.save(update_fields=['pin'])

    def check_pin(self, raw_pin):
        if not self.pin:
            return False
        from django.contrib.auth.hashers import check_password
        return check_password(str(raw_pin), self.pin)

    @property
    def has_pin(self):
        return bool(self.pin)

    def __str__(self):
        return self.phone or str(self.id)

    class Meta:
        db_table = 'user_customuser'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

# Create your models here.

class Profile(models.Model):
    user =          models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    first_name    = models.CharField(max_length=255, blank=True)
    surname       = models.CharField(max_length=255, blank=True)
    email         = models.EmailField(unique=True, null=True, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    phone         = models.CharField(max_length=20, null=True, blank=True)
    profile_picture = models.ImageField(upload_to="profile_pictures/", blank=True)
    
    # Cultural/Religious Info
    cultural_background   = models.CharField(max_length=100, blank=True)
    religious_affiliation = models.CharField(max_length=100, blank=True)
    traditional_names = models.CharField(max_length=200, blank=True, help_text="Traditional/clan names")
    spiritual_beliefs = models.CharField(max_length=200, blank=True, help_text="Spiritual beliefs or practices")
    bio             = models.TextField(blank=True)

    is_complete   = models.BooleanField(default=False)
    is_deceased   = models.BooleanField(default=False)
    is_active     = models.BooleanField(default=True)
    is_verified   = models.BooleanField(default=False, help_text="Designates whether this user has a verified identity.")
    is_email_verified = models.BooleanField(default=False)

    # Additional useful fields
    date_of_death  = models.DateField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True,null=True, blank=True)
    updated_at     = models.DateTimeField(auto_now=True, null=True, blank=True)

    def __str__(self):
        return f"{self.first_name} {self.surname}"
    
    @property
    def full_name(self):
        if self.first_name and self.surname:
            return f"{self.first_name} {self.surname}"
        return self.first_name or self.surname or self.user.phone or str(self.user.id)
    
    def check_completion(self):
        """Check if profile has minimum required fields filled"""
        self.is_complete = bool(self.first_name and self.first_name.strip() and 
                               self.surname and self.surname.strip())
    def save(self, *args, **kwargs):
        if self.user and self.user.phone and not self.phone:
            self.phone = self.user.phone
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'user_profile'


@receiver(post_save, sender=CustomUser)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance, phone=instance.phone)
    elif hasattr(instance, 'profile') and instance.profile and instance.phone and instance.profile.phone != instance.phone:
        instance.profile.phone = instance.phone
        instance.profile.save(update_fields=['phone'])


class EmailVerificationToken(models.Model):
    """Token for email verification in custom signup flow."""
    user  = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_verification_token")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def __str__(self):
        return f"Email verification token for {self.user}"

    def is_valid(self):
        """Check if token is still valid (not expired)."""
        return timezone.now() < self.expires_at

class DeviceToken(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='device_tokens')
    token = models.CharField(max_length=255, unique=True, db_index=True)
    platform = models.CharField(max_length=50, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"Token for {self.user}"

    class Meta:
        verbose_name = 'Device Token'
        verbose_name_plural = 'Device Tokens'

class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=255)
    message = models.TextField()
    data = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    notification_type = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return f"{self.title} -> {self.recipient}"

    class Meta:
        ordering = ['-created_at']


class PhoneOTP(models.Model):
    phone = models.CharField(max_length=20, db_index=True)
    otp = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_verified = models.BooleanField(default=False)
    attempts = models.IntegerField(default=0)

    def is_valid(self):
        return timezone.now() < self.expires_at and not self.is_verified

    def __str__(self):
        return f"OTP {self.otp} for {self.phone}"

    class Meta:
        ordering = ['-created_at']