from rest_framework import permissions
from rest_framework.pagination import PageNumberPagination
from rest_framework.throttling import AnonRateThrottle


class StandardPagination(PageNumberPagination):
    page_size = 15
    page_size_query_param = 'page_size'
    max_page_size = 50


# -------------------------------------------------------------------------
# Custom throttle classes — scoped rates for OTP and PIN auth endpoints.
# -------------------------------------------------------------------------
class OTPRequestThrottle(AnonRateThrottle):
    """Max 5 OTP SMS requests per hour per IP — prevents SMS-flood attacks. Suspended in DEBUG mode."""
    scope = 'otp_request'

    def allow_request(self, request, view):
        from django.conf import settings
        if getattr(settings, 'DEBUG', False):
            return True
        return super().allow_request(request, view)


class OTPVerifyThrottle(AnonRateThrottle):
    """Max 10 OTP verification attempts per hour per IP — prevents brute-force. Suspended in DEBUG mode."""
    scope = 'otp_verify'

    def allow_request(self, request, view):
        from django.conf import settings
        if getattr(settings, 'DEBUG', False):
            return True
        return super().allow_request(request, view)


class PINVerifyThrottle(AnonRateThrottle):
    """Max 10 PIN verification attempts per hour per IP — prevents PIN brute-force. Suspended in DEBUG mode."""
    scope = 'pin_verify'

    def allow_request(self, request, view):
        from django.conf import settings
        if getattr(settings, 'DEBUG', False):
            return True
        return super().allow_request(request, view)


# Maximum number of OTP verification failures before the record is locked.
MAX_OTP_ATTEMPTS = 5




# -------------------------------------------------------------------------
# Permissions
# -------------------------------------------------------------------------
class IsAuthorOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of an object to edit it.
    """
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.author == request.user.profile

class IsPostImageAuthorOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of the post to delete its images.
    """
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.post.author == request.user.profile

