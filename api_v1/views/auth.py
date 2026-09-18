import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone

from rest_framework import permissions, serializers as drf_serializers, status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user.models import CustomUser, PhoneOTP, Profile
from user.serializers import ProfileSerializer, SignupSerializer, UserSerializer
from user.sms import send_otp_sms

from .base import OTPRequestThrottle, OTPVerifyThrottle, PINVerifyThrottle

User = get_user_model()

# Maximum number of OTP verification failures before the record is locked.
MAX_OTP_ATTEMPTS = 5


class EmailAuthTokenSerializer(drf_serializers.Serializer):
    """Accepts email + password instead of username + password."""
    email = drf_serializers.EmailField(label='Email')
    password = drf_serializers.CharField(
        label='Password',
        style={'input_type': 'password'},
        trim_whitespace=False
    )

    def validate(self, attrs):
        email = attrs.get('email')
        password = attrs.get('password')

        from django.contrib.auth import get_user_model
        UserModel = get_user_model()

        # Check if user exists but is not active (email not verified)
        try:
            user_obj = UserModel.objects.get(email__iexact=email)
            if not user_obj.is_active:
                raise drf_serializers.ValidationError(
                    'Your account has not been verified. Please check your email to verify your account.',
                    code='not_verified'
                )
        except UserModel.DoesNotExist:
            raise drf_serializers.ValidationError(
                'No account found with this email address.',
                code='no_account'
            )

        user = authenticate(
            request=self.context.get('request'),
            username=email,  # Django backend uses USERNAME_FIELD which is 'email'
            password=password
        )

        if not user:
            raise drf_serializers.ValidationError(
                'Incorrect password. Please try again.',
                code='authorization'
            )

        attrs['user'] = user
        return attrs


class EmailAuthTokenView(APIView):
    """Custom token endpoint: POST {email, password} -> {token}"""
    permission_classes = [AllowAny]
    serializer_class = EmailAuthTokenSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(
            data=request.data,
            context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, created = Token.objects.get_or_create(user=user)
        return Response({'token': token.key})


class CheckPhoneStatusView(APIView):
    """
    Endpoint: POST /api/v1/auth/check-phone/
    Body: {"phone": "+254..."}
    Returns {"has_pin": true/false, "user_exists": true/false}
    """
    permission_classes = [AllowAny]

    def post(self, request):
        from user.models import CustomUser
        phone = request.data.get('phone', '').strip().replace(" ", "").replace("-", "")
        if not phone:
            return Response({'error': 'Phone number is required.'}, status=status.HTTP_400_BAD_REQUEST)

        user = CustomUser.objects.filter(phone=phone).first()
        return Response({
            'user_exists': bool(user),
            'has_pin': bool(user and user.has_pin)
        }, status=status.HTTP_200_OK)


class RequestOTPView(APIView):
    """
    Endpoint: POST /api/v1/auth/request-otp/
    Body: {"phone": "+254..."}
    Generates a 6-digit OTP, saves it in PhoneOTP with a 10-minute expiry, and dispatches SMS.
    """
    permission_classes = [AllowAny]
    throttle_classes = [OTPRequestThrottle]

    def post(self, request):
        import random
        from datetime import timedelta
        from django.conf import settings
        from user.models import PhoneOTP, CustomUser
        from user.sms import send_otp_sms

        phone = request.data.get('phone', '').strip().replace(" ", "").replace("-", "")
        if not phone:
            return Response({'error': 'Phone number is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Generate 6-digit OTP
        otp = f"{random.randint(100000, 999999)}"
        expires_at = timezone.now() + timedelta(minutes=10)

        # Save to DB
        PhoneOTP.objects.create(
            phone=phone,
            otp=otp,
            expires_at=expires_at
        )

        # Dispatch SMS
        send_otp_sms(phone, otp)

        user = CustomUser.objects.filter(phone=phone).first()

        return Response({
            'message': 'OTP sent successfully.',
            'phone': phone,
            'has_pin': bool(user and user.has_pin),
            'dev_otp': otp if getattr(settings, 'DEBUG', False) else None
        }, status=status.HTTP_200_OK)


class VerifyOTPView(APIView):
    """
    Endpoint: POST /api/v1/auth/verify-otp/
    Body: {"phone": "+254...", "otp": "123456"}
    Verifies OTP, authenticates or registers CustomUser, and returns Auth token + user info.
    """
    permission_classes = [AllowAny]
    throttle_classes = [OTPVerifyThrottle]

    def post(self, request):
        from django.conf import settings
        from user.models import PhoneOTP, CustomUser, Profile
        from user.serializers import UserSerializer

        phone = request.data.get('phone', '').strip().replace(" ", "").replace("-", "")
        otp = request.data.get('otp', '').strip()

        if not phone or not otp:
            return Response({'error': 'Phone and OTP are required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Look up valid OTP
        otp_record = PhoneOTP.objects.filter(phone=phone, is_verified=False).order_by('-created_at').first()

        # Dev fallback: allow test OTP '123456' in DEBUG mode if needed
        is_dev_test = getattr(settings, 'DEBUG', False) and otp == '123456'

        if not is_dev_test:
            if not otp_record:
                return Response({'error': 'No OTP requested for this phone number.'}, status=status.HTTP_400_BAD_REQUEST)

            if not otp_record.is_valid():
                return Response({'error': 'OTP has expired or already been used. Please request a new one.'}, status=status.HTTP_400_BAD_REQUEST)

            # Lock out after too many failed attempts
            if otp_record.attempts >= MAX_OTP_ATTEMPTS:
                return Response(
                    {'error': f'Too many incorrect attempts. Please request a new OTP.'},
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )

            if otp_record.otp != otp:
                otp_record.attempts += 1
                otp_record.save(update_fields=['attempts'])
                remaining = MAX_OTP_ATTEMPTS - otp_record.attempts
                return Response(
                    {'error': f'Invalid OTP code. {remaining} attempt(s) remaining.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            otp_record.is_verified = True
            otp_record.save(update_fields=['is_verified'])

        # Get or Create User
        user = CustomUser.objects.filter(phone=phone).first()
        is_new_user = False
        if not user:
            profile = Profile.objects.filter(phone=phone).first()
            if profile and profile.user:
                user = profile.user
                user.phone = phone
                user.is_phone_verified = True
                user.save()
            else:
                user = CustomUser.objects.create_user(phone=phone)
                user.is_phone_verified = True
                user.is_active = True
                user.save()
                is_new_user = True
                if hasattr(user, 'profile'):
                    user.profile.phone = phone
                    user.profile.save()

        if hasattr(user, 'profile') and not user.profile.phone:
            user.profile.phone = phone
            user.profile.save()

        token, _ = Token.objects.get_or_create(user=user)
        user_serializer = UserSerializer(user)

        return Response({
            'token': token.key,
            'is_new_user': is_new_user,
            'has_pin': bool(user.has_pin),
            'user': user_serializer.data
        }, status=status.HTTP_200_OK)


class VerifyPINView(APIView):
    """
    Endpoint: POST /api/v1/auth/verify-pin/
    Body: {"phone": "+254...", "pin": "1234"}
    Verifies 4-digit security PIN and returns Auth token + user info.
    """
    permission_classes = [AllowAny]
    throttle_classes = [PINVerifyThrottle]

    def post(self, request):
        from user.models import CustomUser
        from user.serializers import UserSerializer

        phone = request.data.get('phone', '').strip().replace(" ", "").replace("-", "")
        pin = request.data.get('pin', '').strip()

        if not phone or not pin:
            return Response({'error': 'Phone number and 4-digit PIN are required.'}, status=status.HTTP_400_BAD_REQUEST)

        user = CustomUser.objects.filter(phone=phone).first()
        if not user or not user.has_pin:
            return Response({'error': 'No security PIN set for this account. Please verify via SMS OTP.'}, status=status.HTTP_400_BAD_REQUEST)

        if not user.check_pin(pin):
            return Response({'error': 'Incorrect 4-digit PIN. Please try again.'}, status=status.HTTP_400_BAD_REQUEST)

        token, _ = Token.objects.get_or_create(user=user)
        user_serializer = UserSerializer(user)

        return Response({
            'token': token.key,
            'has_pin': True,
            'user': user_serializer.data
        }, status=status.HTTP_200_OK)


class SetPINView(APIView):
    """
    Endpoint: POST /api/v1/auth/set-pin/
    Body: {"pin": "1234"}  (phone is optional fallback, ignored when user is authenticated)
    Sets or updates the 4-digit security PIN for the currently authenticated user.
    Requires a valid auth token — call this after OTP verification has returned a token.
    """
    permission_classes = [permissions.IsAuthenticated]  # Security: token required

    def post(self, request):
        from user.models import CustomUser

        phone = request.data.get('phone', '').strip().replace(" ", "").replace("-", "")
        pin = request.data.get('pin', '').strip()

        if not pin or len(pin) != 4 or not pin.isdigit():
            return Response({'error': 'PIN must be exactly 4 digits.'}, status=status.HTTP_400_BAD_REQUEST)

        # Always use the authenticated user as the primary identity
        user = request.user

        user.set_pin(pin)

        return Response({
            'message': '4-digit security PIN updated successfully.',
            'has_pin': True
        }, status=status.HTTP_200_OK)


def verify_user_pin(user, pin):
    """
    Validates user transaction PIN. Returns (True, None) on success,
    or (False, Response) on failure.
    """
    if not user or not user.is_authenticated:
        return False, Response({'error': 'Authentication required.'}, status=status.HTTP_401_UNAUTHORIZED)
    if not getattr(user, 'has_pin', False):
        return False, Response({
            'error': 'No security PIN set for your account. Please set up a 4-digit security PIN in your Profile settings before transacting.',
            'code': 'PIN_NOT_SET'
        }, status=status.HTTP_400_BAD_REQUEST)
    if not pin or not str(pin).strip():
        return False, Response({
            'error': '4-digit Security PIN is required to authorize this transaction.',
            'code': 'PIN_REQUIRED'
        }, status=status.HTTP_400_BAD_REQUEST)
    if not user.check_pin(str(pin).strip()):
        return False, Response({
            'error': 'Incorrect security PIN. Please try again.',
            'code': 'INVALID_PIN'
        }, status=status.HTTP_400_BAD_REQUEST)
    return True, None


class VerifyCurrentPINView(APIView):
    """
    Endpoint: POST /api/v1/auth/verify-current-pin/
    Body: {"pin": "1234"}
    Validates the user's 4-digit security PIN for step-up authorization.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        pin = request.data.get('pin', '').strip()
        valid, err_resp = verify_user_pin(request.user, pin)
        if not valid:
            return err_resp
        return Response({'valid': True, 'message': 'PIN verified successfully.'}, status=status.HTTP_200_OK)




class UserViewSet(viewsets.GenericViewSet):
    queryset = CustomUser.objects.all()
    serializer_class = UserSerializer

    @action(detail=False, methods=['get'])
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], permission_classes=[permissions.AllowAny])
    def signup(self, request):
        serializer = SignupSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(UserSerializer(user, context={'request': request}).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



@api_view(['POST'])
@permission_classes([AllowAny])
def password_reset_request(request):
    """API endpoint for mobile password reset. Sends a reset email."""
    from django.contrib.auth.forms import PasswordResetForm
    from django.conf import settings

    email = request.data.get('email', '').strip()
    if not email:
        return Response({'error': 'Email is required.'}, status=status.HTTP_400_BAD_REQUEST)

    form = PasswordResetForm(data={'email': email})
    if form.is_valid():
        form.save(
            request=request,
            use_https=request.is_secure(),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@chema101.com'),
            email_template_name='registration/password_reset_email.html',
        )
    # Always return success to avoid revealing which emails exist
    return Response({'detail': 'If an account with that email exists, a password reset link has been sent.'})

from user.models import DeviceToken, Notification
from user.serializers import DeviceTokenSerializer, NotificationSerializer



@api_view(['GET'])
@permission_classes([AllowAny])
def mobile_callback_view(request):
    """
    Callback URL targeted after successful social OAuth redirect on the backend web.
    If authenticated, redirects to the mobile scheme to return the auth token to the app.
    """
    redirect_url = request.GET.get('redirect_url') or "komunity://auth-success"
    if request.user.is_authenticated:
        token, _ = Token.objects.get_or_create(user=request.user)
        separator = "&" if "?" in redirect_url else "?"
        return redirect(f"{redirect_url}{separator}token={token.key}")
    
    failure_url = request.GET.get('failure_url') or "komunity://auth-failed"
    return redirect(failure_url)


# =============================================================================
# FundCampaign ViewSet
# =============================================================================

from condolence.models import FundCampaign, CampaignContribution
from condolence.serializers import FundCampaignSerializer, CampaignContributionSerializer

