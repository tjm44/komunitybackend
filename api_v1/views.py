import uuid
from rest_framework import viewsets, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.authtoken.models import Token
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from django.contrib.auth import authenticate
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied, ValidationError


class StandardPagination(PageNumberPagination):
    page_size = 15
    page_size_query_param = 'page_size'
    max_page_size = 50


# -------------------------------------------------------------------------
# Custom throttle classes — scoped rates for OTP and PIN auth endpoints.
# Inheriting ScopedRateThrottle lets each view declare its own named scope.
# -------------------------------------------------------------------------
from rest_framework.throttling import AnonRateThrottle


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


from django.shortcuts import get_object_or_404
from django.utils import timezone
from decimal import Decimal

from django.db.models import Q
from chema.models import Group, Post, Comment, GroupMembership, PostImage, Reply, Organisation, ContributionCycle, MemberCyclePayment
from user.models import Profile
from condolence.models import Contribution, Deceased
from wallet.models import (
    Wallet, Transaction, GroupWalletTransferRequest,
    PlatformFeeConfig, PlatformFeeLedger,
    SMSCreditPackage, GroupSMSCreditBalance, SMSCreditPurchase,
    GroupSubscription, UserSubscription, ServiceVendor, VendorBooking,
    MicroInsurancePolicy, InsurancePolicyEnrollment
)

from chema.serializers import (
    GroupSerializer, PostSerializer, CommentSerializer, 
    GroupMembershipSerializer, PostImageSerializer, ReplySerializer,
    OrganisationSerializer, ContributionCycleSerializer, MemberCyclePaymentSerializer
)
from user.serializers import ProfileSerializer, UserSerializer, SignupSerializer
from condolence.serializers import ContributionSerializer, DeceasedSerializer
from wallet.serializers import (
    WalletSerializer, TransactionSerializer, GroupWalletTransferRequestSerializer,
    PlatformFeeConfigSerializer, SMSCreditPackageSerializer,
    GroupSMSCreditBalanceSerializer, SMSCreditPurchaseSerializer,
    GroupSubscriptionSerializer, UserSubscriptionSerializer,
    ServiceVendorSerializer, VendorBookingSerializer,
    MicroInsurancePolicySerializer, InsurancePolicyEnrollmentSerializer
)

from django.contrib.auth import get_user_model
CustomUser = get_user_model()

from user.notifications import send_push_notification


def waas_api_withdraw(wallet_id, channel, metadata, amount, currency='ZAR'):
    """Call Flutterwave sandbox transfer/disbursement endpoints."""
    from wallet.flutterwave import initiate_transfer
    
    print(f"Flutterwave: Withdrawing {amount} {currency} from {wallet_id} via {channel}")

    if channel == 'bank_transfer':
        acc_num = metadata.get('account_number')
        bank_code = metadata.get('bank_code')
        if not acc_num or not bank_code:
            return {'success': False, 'error': 'Bank account number and bank code are required.'}
        
        # Call initiate_transfer
        ref = f"withdraw-bank-{uuid.uuid4().hex[:8]}"
        res = initiate_transfer(
            amount=amount,
            bank_code=bank_code,
            account_number=acc_num,
            narration=f"Withdraw to Bank Account {acc_num}",
            reference=ref
        )
        return res

    elif channel == 'mobile_money':
        phone = metadata.get('phone_number')
        provider = metadata.get('provider')
        if not phone or not provider:
            return {'success': False, 'error': 'Mobile money phone number and provider are required.'}
        
        # Mobile money payout in Flutterwave is also a transfer
        ref = f"withdraw-momo-{uuid.uuid4().hex[:8]}"
        res = initiate_transfer(
            amount=amount,
            bank_code=provider,  # Network code (e.g. MTN, VODAFONE)
            account_number=phone,
            narration=f"Withdraw to MoMo {phone}",
            reference=ref
        )
        return res

    elif channel == 'voucher':
        # Flutterwave v4 does not support custom voucher payout generation in standard payout sandbox directly, 
        # so we fallback to a simulated success reference.
        partner = metadata.get('partner')
        if not partner:
            return {'success': False, 'error': 'Retail partner is required.'}
        
        import random
        # Generate a simulated voucher code
        voucher_code = f"VAL-{random.randint(100000, 999999)}"
        
        return {
            'success': True,
            'waas_ref': f"WD_VOUCHER_{timezone.now().timestamp()}",
            'voucher_code': voucher_code,
            'partner': partner,
        }
    else:
        return {'success': False, 'error': 'Unsupported withdrawal channel.'}

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

class ProfileViewSet(viewsets.ModelViewSet):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        # Allow users to only see their own profile or public profiles
        if self.request.user.is_authenticated:
            return Profile.objects.filter(is_active=True)
        return Profile.objects.none()

    @action(detail=False, methods=['get'])
    def me(self, request):
        serializer = self.get_serializer(request.user.profile)
        return Response(serializer.data)

    def perform_update(self, serializer):
        profile = serializer.save()
        profile.check_completion()
        profile.save()

    @action(detail=True, methods=['post'], url_path='verify-kyc')
    def verify_kyc(self, request, pk=None):
        profile = self.get_object()
        if profile.user != request.user:
            return Response({'error': 'You can only verify your own profile.'}, status=status.HTTP_403_FORBIDDEN)
            
        id_number = request.data.get('id_number')
        id_type = request.data.get('id_type', 'national_id')
        req_first_name = request.data.get('first_name') or profile.first_name
        req_surname = request.data.get('surname') or request.data.get('last_name') or profile.surname
        
        from user.kyc import FlutterwaveKYCProvider
        kyc_result = FlutterwaveKYCProvider.verify_document(
            first_name=req_first_name,
            surname=req_surname,
            id_number=id_number,
            id_type=id_type
        )
        
        if len(kyc_result) == 3:
            success, message, verified_data = kyc_result
        else:
            success, message = kyc_result[:2]
            verified_data = {}
        
        if not success:
            return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)
            
        # Update profile name with verified identity details if available
        v_first_name = (verified_data or {}).get('first_name') or (verified_data or {}).get('firstName') or req_first_name
        v_surname = (verified_data or {}).get('last_name') or (verified_data or {}).get('lastName') or (verified_data or {}).get('surname') or req_surname

        if v_first_name:
            profile.first_name = v_first_name
        if v_surname:
            profile.surname = v_surname

        profile.is_verified = True
        profile.check_completion()
        profile.save()

        serializer = self.get_serializer(profile)
        return Response({
            'status': 'verified',
            'message': message,
            'full_name': profile.full_name,
            'first_name': profile.first_name,
            'surname': profile.surname,
            'profile': serializer.data
        }, status=status.HTTP_200_OK)

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

class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer

    def perform_create(self, serializer):
        group = serializer.save(creator=self.request.user)
        # Ensure the creator active admin membership is upserted without duplicate creation
        GroupMembership.objects.update_or_create(
            group=group,
            member=self.request.user.profile,
            defaults={
                'is_admin': True,
                'role': 'admin',
                'status': 'active',
                'is_active': True
            }
        )
        # Deactivate others for this user to keep only one active
        GroupMembership.objects.filter(member=self.request.user.profile).exclude(group=group).update(is_active=False)

    def get_queryset(self):
        queryset = Group.objects.filter(is_active=True).order_by('-created_at')
        
        # Discovery Logic: Exclude groups the user is already an active member of
        # ONLY apply this to the list action, not detail actions like members or leave
        if self.action == 'list' and self.request.user.is_authenticated:
            queryset = queryset.exclude(
                groupmembership__member=self.request.user.profile,
                groupmembership__status='active'
            ).distinct()
        return queryset

    @action(detail=False, methods=['get'])
    def mine(self, request):
        profile = request.user.profile
        
        if request.GET.get('active') == 'true':
            groups = Group.objects.filter(
                groupmembership__member=profile,
                groupmembership__status='active',
                groupmembership__is_active=True
            ).distinct()
        else:
            groups = Group.objects.filter(
                groupmembership__member=profile,
                groupmembership__status='active'
            ).distinct()
            
        serializer = self.get_serializer(groups, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def active_members(self, request):
        active_mem = GroupMembership.objects.filter(member=request.user.profile, is_active=True).first()
        if not active_mem:
            return Response([])
        
        # Changed to handle the case where some memberships might be 'deceased' but we still want to see them in some lists?
        # Actually for sending money, only active 'active' members.
        memberships = GroupMembership.objects.filter(group=active_mem.group, status='active')
        serializer = GroupMembershipSerializer(memberships, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['get'], pagination_class=StandardPagination)
    def discover(self, request):
        queryset = Group.objects.filter(is_active=True).exclude(
            groupmembership__member=request.user.profile,
            groupmembership__status='active'
        ).distinct()

        # Type / Purpose filter
        purpose = request.query_params.get('purpose')
        if purpose and purpose != 'all':
            queryset = queryset.filter(purpose=purpose)

        # Search query (by name or description)
        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(description__icontains=search)
            )

        queryset = queryset.order_by('-created_at')

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def join(self, request, pk=None):
        group = self.get_object()
        profile = request.user.profile
        if group.verified_members_only and not profile.is_verified:
            return Response({'error': 'This group is restricted to verified profiles only.'}, status=status.HTTP_400_BAD_REQUEST)

        # Creators always rejoin as active admins, regardless of group settings
        is_creator = (group.creator == request.user)
        if is_creator:
            membership, _ = GroupMembership.objects.get_or_create(
                group=group,
                member=profile,
                defaults={
                    'status': 'active',
                    'is_active': True,
                    'is_admin': True,
                    'role': 'admin',
                }
            )
            # Ensure creator always has full admin rights even if membership pre-existed
            if not (membership.is_admin and membership.role == 'admin' and membership.status == 'active' and membership.is_active):
                membership.is_admin = True
                membership.role = 'admin'
                membership.status = 'active'
                membership.is_active = True
                membership.save()
            # Deactivate others for this user to keep only one active
            GroupMembership.objects.filter(member=profile).exclude(group=group).update(is_active=False)
            return Response({'status': membership.status}, status=status.HTTP_201_CREATED)

        status_val = 'pending' if group.requires_approval else 'active'
        is_active_val = (status_val == 'active')
        
        join_msg = request.data.get('join_message', '')
        b_name = request.data.get('beneficiary_name')
        b_rel = request.data.get('beneficiary_relationship')
        b_phone = request.data.get('beneficiary_phone')
        deps_list = request.data.get('dependents', [])

        v_make_model = request.data.get('vehicle_make_model')
        v_reg = request.data.get('vehicle_registration')
        v_insurer = request.data.get('insurer_name')
        v_policy = request.data.get('policy_number')
        v_vin = request.data.get('vin_number')
        v_license = request.data.get('driver_license_number')

        membership, created = GroupMembership.objects.get_or_create(
            group=group,
            member=profile,
            defaults={
                'status': status_val,
                'is_active': is_active_val,
                'join_message': join_msg,
                'beneficiary_name': b_name,
                'beneficiary_relationship': b_rel,
                'beneficiary_phone': b_phone,
                'vehicle_make_model': v_make_model,
                'vehicle_registration': v_reg,
                'insurer_name': v_insurer,
                'policy_number': v_policy,
                'vin_number': v_vin,
                'driver_license_number': v_license,
            }
        )
        if not created:
            membership.status = status_val
            membership.is_active = is_active_val
            if join_msg:
                membership.join_message = join_msg
            if b_name:
                membership.beneficiary_name = b_name
            if b_rel:
                membership.beneficiary_relationship = b_rel
            if b_phone:
                membership.beneficiary_phone = b_phone
            if v_make_model:
                membership.vehicle_make_model = v_make_model
            if v_reg:
                membership.vehicle_registration = v_reg
            if v_insurer:
                membership.insurer_name = v_insurer
            if v_policy:
                membership.policy_number = v_policy
            if v_vin:
                membership.vin_number = v_vin
            if v_license:
                membership.driver_license_number = v_license
            membership.save()

        # Handle dependents creation for bereavement groups
        if group.purpose == 'bereavement' and isinstance(deps_list, list):
            from chema.models import Dependent
            for dep in deps_list:
                if isinstance(dep, dict) and dep.get('name'):
                    dob = dep.get('date_of_birth') or dep.get('dob') or '2000-01-01'
                    Dependent.objects.get_or_create(
                        guardian=profile,
                        group=group,
                        name=dep.get('name'),
                        defaults={
                            'relationship': dep.get('relationship', 'Dependent'),
                            'date_of_birth': dob
                        }
                    )

        if is_active_val:
            # Deactivate others for this user to keep only one active
            GroupMembership.objects.filter(member=profile).exclude(group=group).update(is_active=False)
            
            # Notify other members if enabled
            if group.notify_on_member_join:
                for active_mem in group.groupmembership_set.filter(status='active').exclude(member=profile):
                    send_push_notification(
                        user=active_mem.member.user,
                        title=f"New Member in {group.name}",
                        message=f"{profile.full_name} has joined the group.",
                        notification_type="member_joined",
                        data={'group_id': group.id}
                    )
        return Response({'status': membership.status}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def leave(self, request, pk=None):
        group = self.get_object()
        profile = request.user.profile
        GroupMembership.objects.filter(group=group, member=profile).update(is_active=False, status='inactive')
        return Response({'status': 'left'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def select(self, request, pk=None):
        group = self.get_object()
        profile = request.user.profile
        # Set this one as active in DB (will be used as fallback on web and primary on mobile)
        GroupMembership.objects.filter(member=profile, group=group).update(is_active=True)
        # Deactivate others for this user
        GroupMembership.objects.filter(member=profile).exclude(group=group).update(is_active=False)
        return Response({'status': 'selected'})

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        group = self.get_object()
        profile = request.user.profile
        GroupMembership.objects.filter(group=group, member=profile).update(last_viewed_at=timezone.now())
        return Response({'status': 'marked_read'})

    @action(detail=True, methods=['get'])
    def members(self, request, pk=None):
        group = self.get_object()
        memberships = GroupMembership.objects.filter(group=group, status='active')
        serializer = GroupMembershipSerializer(memberships, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def pending_members(self, request, pk=None):
        group = self.get_object()
        if not group.is_admin(request.user):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)
        memberships = GroupMembership.objects.filter(group=group, status='pending')
        serializer = GroupMembershipSerializer(memberships, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def transactions(self, request, pk=None):
        group = self.get_object()
        # Transparency: Any active member or admin can view history
        is_member = group.is_member(request.user)
        is_admin = group.is_admin(request.user)
        
        if not (is_member or is_admin):
            return Response(
                {'error': f'Access denied. You must be an active member of {group.name} to view its wallet.'}, 
                status=status.HTTP_403_FORBIDDEN
            )

        transactions = Transaction.objects.filter(
            destination_group=group,
            status='COMPLETED'
        ).order_by('-timestamp')
        
        serializer = TransactionSerializer(transactions, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def wallet_transfer_requests(self, request, pk=None):
        group = self.get_object()
        if not (group.is_member(request.user) or group.is_admin(request.user)):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)

        transfer_requests = GroupWalletTransferRequest.objects.filter(group=group).order_by('-created_at')
        serializer = GroupWalletTransferRequestSerializer(transfer_requests, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def request_wallet_transfer(self, request, pk=None):
        group = self.get_object()
        if not group.is_admin(request.user):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)

        recipient_profile_id = request.data.get('recipient_profile')
        amount = request.data.get('amount')
        deceased_contribution_id = request.data.get('deceased_contribution')
        fund_campaign_id = request.data.get('fund_campaign')

        if not recipient_profile_id or amount is None:
            return Response({'error': 'recipient_profile and amount are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            amount_val = Decimal(str(amount))
            if amount_val <= 0:
                raise ValueError()
        except Exception:
            return Response({'error': 'Invalid amount provided.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            recipient_profile = Profile.objects.get(id=recipient_profile_id)
        except Profile.DoesNotExist:
            return Response({'error': 'Recipient member not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not GroupMembership.objects.filter(group=group, member=recipient_profile, status='active', is_active=True).exists():
            return Response({'error': 'Recipient must be an active member of this group.'}, status=status.HTTP_400_BAD_REQUEST)

        if group.get_balance() < amount_val:
            return Response({'error': 'Insufficient group wallet balance for this transfer request.'}, status=status.HTTP_400_BAD_REQUEST)

        from condolence.models import Deceased, FundCampaign
        deceased_contribution = None
        if deceased_contribution_id:
            try:
                deceased_contribution = Deceased.objects.get(id=deceased_contribution_id, group=group)
            except Deceased.DoesNotExist:
                return Response({'error': 'Deceased record not found for this group.'}, status=status.HTTP_404_NOT_FOUND)

        fund_campaign = None
        if fund_campaign_id:
            try:
                fund_campaign = FundCampaign.objects.get(id=fund_campaign_id, group=group)
            except FundCampaign.DoesNotExist:
                return Response({'error': 'Fund campaign not found for this group.'}, status=status.HTTP_404_NOT_FOUND)

        transfer_request = GroupWalletTransferRequest.objects.create(
            group=group,
            requested_by=request.user,
            recipient_profile=recipient_profile,
            amount=amount_val,
            deceased_contribution=deceased_contribution,
            fund_campaign=fund_campaign,
            note=request.data.get('note', ''),
        )
        # Requester counts as first approval
        transfer_request.approvals.add(request.user)
        transfer_request.save()

        # If only 1 approval needed, execute immediately
        if transfer_request.can_execute():
            try:
                transfer_request.execute()
            except Exception as exc:
                return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            serializer = GroupWalletTransferRequestSerializer(transfer_request, context={'request': request})
            return Response({'status': 'executed', 'request': serializer.data}, status=status.HTTP_201_CREATED)

        serializer = GroupWalletTransferRequestSerializer(transfer_request, context={'request': request})
        return Response({
            'status': 'pending_approval',
            'approvals_given': transfer_request.approvals.count(),
            'approvals_needed': transfer_request.required_approvals,
            'request': serializer.data,
        }, status=status.HTTP_202_ACCEPTED)

    @action(detail=True, methods=['post'])
    def approve_wallet_transfer_request(self, request, pk=None):
        group = self.get_object()
        if not group.is_admin(request.user):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)

        transfer_request_id = request.data.get('request_id')
        if not transfer_request_id:
            return Response({'error': 'request_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        transfer_request = get_object_or_404(GroupWalletTransferRequest, id=transfer_request_id, group=group)
        if transfer_request.status != GroupWalletTransferRequest.STATUS_PENDING:
            return Response({'error': 'Transfer request is not pending.'}, status=status.HTTP_400_BAD_REQUEST)

        if transfer_request.approvals.filter(id=request.user.id).exists():
            return Response({'error': 'You have already approved this request.'}, status=status.HTTP_400_BAD_REQUEST)

        transfer_request.approvals.add(request.user)
        transfer_request.save()

        if transfer_request.can_execute():
            try:
                transfer_request.execute()
            except Exception as exc:
                return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        serializer = GroupWalletTransferRequestSerializer(transfer_request, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def reject_wallet_transfer_request(self, request, pk=None):
        group = self.get_object()
        if not group.is_admin(request.user):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)

        transfer_request_id = request.data.get('request_id')
        if not transfer_request_id:
            return Response({'error': 'request_id is required.'}, status=status.HTTP_400_BAD_REQUEST)

        transfer_request = get_object_or_404(GroupWalletTransferRequest, id=transfer_request_id, group=group)
        if transfer_request.status != GroupWalletTransferRequest.STATUS_PENDING:
            return Response({'error': 'Transfer request is not pending.'}, status=status.HTTP_400_BAD_REQUEST)

        transfer_request.status = GroupWalletTransferRequest.STATUS_REJECTED
        transfer_request.save()
        serializer = GroupWalletTransferRequestSerializer(transfer_request, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def campaigns(self, request, pk=None):
        group = self.get_object()
        from condolence.models import FundCampaign
        from condolence.serializers import FundCampaignSerializer
        campaigns = FundCampaign.objects.filter(group=group).order_by('-created_at')
        serializer = FundCampaignSerializer(campaigns, many=True, context={'request': request})
        return Response(serializer.data)

    # ─────────────────────────────────────────────────────────────────────────
    # RECURRING CONTRIBUTION CYCLES, LEDGER & REMINDERS
    # ─────────────────────────────────────────────────────────────────────────

    @action(detail=True, methods=['get'])
    def recurring_cycles(self, request, pk=None):
        """
        List all past, current, and upcoming contribution cycles for this group,
        complete with member payment matrix and summary statistics.
        """
        group = self.get_object()
        if not group.enable_recurring_contributions or not group.is_active:
            return Response({
                'enabled': False,
                'cycles': [],
                'summary': None
            })

        # Ensure active cycle exists for the current month
        active_cycle = group.contribution_cycles.filter(status='active').first()
        if not active_cycle and group.recurring_amount > 0:
            active_cycle = group.ensure_active_cycle()

        cycles = group.contribution_cycles.all().order_by('-due_date')
        cycles_serializer = ContributionCycleSerializer(cycles, many=True, context={'request': request})

        # Calculate group-level recurring stats
        from django.db.models import Sum
        all_payments = MemberCyclePayment.objects.filter(cycle__group=group)
        total_collected_all_time = float(all_payments.filter(status='paid').aggregate(total=Sum('amount_paid'))['total'] or 0.00)
        total_due_all_time = float(all_payments.aggregate(total=Sum('amount_due'))['total'] or 0.00)

        # Days until next due date
        days_until_due = None
        if active_cycle and active_cycle.due_date:
            today = timezone.now().date()
            delta = (active_cycle.due_date - today).days
            days_until_due = delta

        # Current user's payment for active cycle
        my_payment_data = None
        if request.user.is_authenticated and active_cycle:
            my_pay = active_cycle.payments.filter(member=request.user.profile).first()
            if my_pay:
                my_payment_data = MemberCyclePaymentSerializer(my_pay, context={'request': request}).data

        return Response({
            'enabled': True,
            'recurring_amount': float(group.recurring_amount),
            'recurring_frequency': group.recurring_frequency,
            'recurring_due_day': group.recurring_due_day,
            'recurring_title': group.recurring_title,
            'recurring_reminder_days': group.recurring_reminder_days,
            'active_cycle_id': active_cycle.id if active_cycle else None,
            'days_until_due': days_until_due,
            'my_active_payment': my_payment_data,
            'summary': {
                'total_collected_all_time': total_collected_all_time,
                'total_due_all_time': total_due_all_time,
                'total_cycles_count': cycles.count(),
                'active_cycle_collected': active_cycle.get_total_collected() if active_cycle else 0.00,
                'active_cycle_expected': active_cycle.get_total_expected() if active_cycle else 0.00,
                'active_cycle_paid_count': active_cycle.get_paid_count() if active_cycle else 0,
                'active_cycle_unpaid_count': active_cycle.get_unpaid_count() if active_cycle else 0,
                'active_cycle_progress': active_cycle.get_progress_percentage() if active_cycle else 0,
            },
            'cycles': cycles_serializer.data,
        })

    @action(detail=True, methods=['post'])
    def generate_cycle(self, request, pk=None):
        """
        Admin endpoint to manually trigger/refresh a contribution cycle for a
        specific month/year or next upcoming cycle.
        """
        group = self.get_object()
        if not group.is_admin(request.user):
            return Response({'error': 'Only group admins can generate contribution cycles.'}, status=status.HTTP_403_FORBIDDEN)

        import calendar
        from datetime import date
        now = timezone.now()
        month = int(request.data.get('month', now.month))
        year = int(request.data.get('year', now.year))
        target_amount = Decimal(str(request.data.get('target_amount', group.recurring_amount)))

        max_days = calendar.monthrange(year, month)[1]
        due_day = min(group.recurring_due_day or 25, max_days)
        due_date_str = request.data.get('due_date')
        if due_date_str:
            from datetime import datetime
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        else:
            due_date = date(year, month, due_day)

        title = request.data.get('title')
        if not title:
            import calendar as cal
            month_name = cal.month_name[month]
            title = f"{month_name} {year} {group.recurring_title or 'Contribution'}"

        cycle, created = ContributionCycle.objects.get_or_create(
            group=group,
            cycle_month=month,
            cycle_year=year,
            defaults={
                'title': title,
                'due_date': due_date,
                'target_amount_per_member': target_amount,
                'status': 'active',
            }
        )

        if not created:
            cycle.title = title
            cycle.due_date = due_date
            cycle.target_amount_per_member = target_amount
            cycle.save()

        cycle.populate_member_payments()
        serializer = ContributionCycleSerializer(cycle, context={'request': request})
        return Response(serializer.data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def pay_cycle(self, request, pk=None):
        """
        Member pays their recurring contribution for a cycle using their wallet balance.
        """
        group = self.get_object()

        try:
            profile = request.user.profile
        except Exception:
            return Response({'error': 'User profile not found. Please complete your profile setup.'}, status=status.HTTP_400_BAD_REQUEST)

        if not group.is_member(request.user):
            return Response({'error': 'You must be an active member of this group to make contributions.'}, status=status.HTTP_403_FORBIDDEN)

        cycle_id = request.data.get('cycle_id')
        if cycle_id:
            cycle = get_object_or_404(ContributionCycle, id=cycle_id, group=group)
        else:
            cycle = group.contribution_cycles.filter(status='active').first()
            if not cycle:
                cycle = group.ensure_active_cycle()

        if not cycle:
            return Response({'error': 'No active contribution cycle found.'}, status=status.HTTP_400_BAD_REQUEST)

        # Get or create payment record
        payment, _ = MemberCyclePayment.objects.get_or_create(
            cycle=cycle,
            member=profile,
            defaults={'amount_due': cycle.target_amount_per_member, 'amount_paid': 0.00, 'status': 'pending'}
        )

        if payment.status == 'paid':
            return Response({'error': 'You have already completed your contribution for this cycle.'}, status=status.HTTP_400_BAD_REQUEST)

        # Use explicit None check so Decimal(0.00) doesn't fall through as falsy
        raw_amount = request.data.get('amount')
        if raw_amount is not None:
            fallback_amount = raw_amount
        elif payment.amount_due is not None and payment.amount_due > 0:
            fallback_amount = payment.amount_due
        else:
            fallback_amount = cycle.target_amount_per_member or 0

        try:
            amount_to_pay = Decimal(str(fallback_amount))
        except Exception:
            return Response({'error': 'Invalid contribution amount provided.'}, status=status.HTTP_400_BAD_REQUEST)
        if amount_to_pay <= 0:
            return Response({'error': 'Contribution amount must be greater than zero.'}, status=status.HTTP_400_BAD_REQUEST)

        # Check wallet balance
        wallet, _ = Wallet.objects.get_or_create(
            user=request.user,
            defaults={'external_wallet_id': f"WAAS_{request.user.id}"}
        )

        if wallet.get_balance() < amount_to_pay:
            return Response({'error': 'Insufficient wallet balance to pay group dues.'}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            # Create wallet transaction
            tx = Transaction.objects.create(
                wallet=wallet,
                transaction_type='TRANSFER',
                amount=amount_to_pay,
                status='COMPLETED',
                destination_group=group,
                note=f"Recurring contribution for {cycle.title}",
                waas_reference_id=f"RECUR_{cycle.id}_{profile.id}_{int(timezone.now().timestamp())}"
            )
            # Mark payment as paid
            payment.mark_as_paid(amount=amount_to_pay, transaction=tx, method='wallet')
            wallet.recalculate_balance()

        # Send notification to Group Admins
        admin_users = set()
        if group.creator:
            admin_users.add(group.creator)
        for adm in group.admins.all():
            admin_users.add(adm)
        for gm in group.groupmembership_set.filter(role='admin', status='active', is_active=True).select_related('member__user'):
            if gm.member and gm.member.user:
                admin_users.add(gm.member.user)

        for adm_user in admin_users:
            if adm_user != request.user:
                send_push_notification(
                    user=adm_user,
                    title=f"Dues Paid in {group.name}",
                    message=f"{profile.full_name} contributed R{amount_to_pay:.2f} for {cycle.title}.",
                    notification_type="cycle_payment",
                    data={'group_id': group.id, 'cycle_id': cycle.id}
                )

        return Response({
            'status': 'success',
            'message': f"Successfully paid R{amount_to_pay:.2f} for {cycle.title}!",
            'payment': MemberCyclePaymentSerializer(payment, context={'request': request}).data,
            'cycle': ContributionCycleSerializer(cycle, context={'request': request}).data,
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def send_cycle_reminder(self, request, pk=None):
        """
        Admin endpoint to dispatch push and in-app reminder notifications to all
        members who have not yet paid for the specified (or active) cycle.
        """
        group = self.get_object()
        if not group.is_admin(request.user):
            return Response({'error': 'Only group admins can send reminder notifications.'}, status=status.HTTP_403_FORBIDDEN)

        cycle_id = request.data.get('cycle_id')
        if cycle_id:
            cycle = get_object_or_404(ContributionCycle, id=cycle_id, group=group)
        else:
            cycle = group.contribution_cycles.filter(status='active').first()

        if not cycle:
            return Response({'error': 'No active cycle found to send reminders for.'}, status=status.HTTP_400_BAD_REQUEST)

        # Make sure member payments are up to date
        cycle.populate_member_payments()

        unpaid_payments = cycle.payments.exclude(status__in=['paid', 'exempt']).select_related('member__user')
        reminded_count = 0
        now = timezone.now()

        due_date_str = cycle.due_date.strftime('%d %B %Y') if cycle.due_date else 'soon'
        amount_str = f"R{cycle.target_amount_per_member:.2f}"

        for pay in unpaid_payments:
            member_user = pay.member.user if pay.member else None
            if member_user:
                send_push_notification(
                    user=member_user,
                    title=f"Reminder: {group.name} Dues Due {due_date_str}",
                    message=f"Hi {pay.member.full_name}, your contribution of {amount_str} for '{cycle.title}' is due on {due_date_str}. Tap to pay now with your wallet.",
                    notification_type="cycle_reminder",
                    data={'group_id': group.id, 'cycle_id': cycle.id, 'action': 'pay_dues'}
                )
                pay.reminder_sent_at = now
                pay.save(update_fields=['reminder_sent_at'])
                reminded_count += 1

        return Response({
            'status': 'success',
            'reminded_count': reminded_count,
            'message': f"Sent contribution reminders to {reminded_count} member(s) for {cycle.title}."
        })

    @action(detail=True, methods=['get'])
    def recurring_ledger(self, request, pk=None):
        """
        Complete tabular ledger of all recurring contribution cycles and payments
        for auditing and transparency.
        """
        group = self.get_object()
        cycles = group.contribution_cycles.all().order_by('-due_date')
        
        cycle_id = request.query_params.get('cycle_id')
        if cycle_id:
            cycles = cycles.filter(id=cycle_id)

        ledger_data = []
        for cycle in cycles:
            payments = cycle.payments.all().select_related('member', 'transaction')
            payments_data = []
            for p in payments:
                payments_data.append({
                    'id': p.id,
                    'member_id': p.member.id if p.member else None,
                    'member_name': p.member.full_name if p.member else 'Unknown',
                    'member_phone': p.member.phone if p.member else '',
                    'amount_due': float(p.amount_due),
                    'amount_paid': float(p.amount_paid),
                    'status': p.status,
                    'paid_at': p.paid_at.isoformat() if p.paid_at else None,
                    'payment_method': p.payment_method,
                    'transaction_ref': p.transaction.waas_reference_id if p.transaction else None,
                    'reminder_sent_at': p.reminder_sent_at.isoformat() if p.reminder_sent_at else None,
                })

            ledger_data.append({
                'cycle_id': cycle.id,
                'title': cycle.title,
                'due_date': cycle.due_date.isoformat() if cycle.due_date else None,
                'target_amount_per_member': float(cycle.target_amount_per_member),
                'status': cycle.status,
                'total_expected': cycle.get_total_expected(),
                'total_collected': cycle.get_total_collected(),
                'paid_count': cycle.get_paid_count(),
                'unpaid_count': cycle.get_unpaid_count(),
                'progress_percentage': cycle.get_progress_percentage(),
                'payments': payments_data
            })

        return Response({
            'group_id': group.id,
            'group_name': group.name,
            'ledger': ledger_data
        })



class OrganisationViewSet(viewsets.ModelViewSet):
    queryset = Organisation.objects.all()
    serializer_class = OrganisationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        profile = self.request.user.profile
        if not profile.is_verified:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Only verified users can create Organisations. Please complete your KYC verification first.")
        
        org = serializer.save(creator=self.request.user)
        # Automatically add the creator as admin
        org.admins.add(self.request.user)

    def get_queryset(self):
        queryset = Organisation.objects.filter(is_active=True).order_by('-created_at')
        return queryset

    @action(detail=False, methods=['get'])
    def mine(self, request):
        # Organisations the user created or is an admin of
        orgs = Organisation.objects.filter(
            Q(creator=request.user) | Q(admins=request.user)
        ).distinct().order_by('-created_at')
        serializer = self.get_serializer(orgs, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def discover(self, request):
        # Explore active verified organisations
        orgs = Organisation.objects.filter(is_active=True, is_verified=True).order_by('-created_at')
        serializer = self.get_serializer(orgs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], url_path='verify-org', permission_classes=[permissions.IsAdminUser])
    def verify_org(self, request, pk=None):
        org = self.get_object()
        is_verified = request.data.get('is_verified', True)
        org.is_verified = bool(is_verified)
        org.save(update_fields=['is_verified'])
        return Response({
            'status': 'updated',
            'organisation_id': org.id,
            'is_verified': org.is_verified
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='request-verification')
    def request_verification(self, request, pk=None):
        org = self.get_object()
        if not org.is_admin(request.user):
            return Response({'error': 'Only organisation admins can request verification.'}, status=status.HTTP_403_FORBIDDEN)
        if org.is_verified:
            return Response({'status': 'already_verified', 'message': 'This organisation is already verified.'})
        return Response({
            'status': 'request_received',
            'message': 'Your verification request has been submitted. The Komunity team will review your organisation within 2–5 business days.'
        }, status=status.HTTP_200_OK)

    @action(detail=True, methods=['get'])
    def transactions(self, request, pk=None):
        org = self.get_object()
        transactions = Transaction.objects.filter(
            destination_organisation=org,
            status='COMPLETED'
        ).order_by('-timestamp')
        serializer = TransactionSerializer(transactions, many=True)
        return Response(serializer.data)


class GroupMembershipViewSet(viewsets.ModelViewSet):
    queryset = GroupMembership.objects.all()
    serializer_class = GroupMembershipSerializer

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        membership = self.get_object()
        if not membership.group.is_admin(request.user):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)
        
        membership.approve(request.user)
        
        # Notify the user
        send_push_notification(
            user=membership.member.user,
            title=f"Welcome to {membership.group.name}!",
            message="Your membership request has been approved.",
            notification_type="membership_approved",
            data={'group_id': membership.group.id}
        )

        # Notify other members if enabled
        if membership.group.notify_on_member_join:
            for active_mem in membership.group.groupmembership_set.filter(status='active').exclude(member=membership.member):
                send_push_notification(
                    user=active_mem.member.user,
                    title=f"New Member in {membership.group.name}",
                    message=f"{membership.member.full_name} has joined the group.",
                    notification_type="member_joined",
                    data={'group_id': membership.group.id}
                )
        
        return Response({'status': 'active'})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        membership = self.get_object()
        if not membership.group.is_admin(request.user):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)
        
        membership.status = 'rejected'
        membership.is_active = False
        membership.save()
        
        # Notify the user
        send_push_notification(
            user=membership.member.user,
            title=f"Membership Update for {membership.group.name}",
            message="Your membership request was declined.",
            notification_type="membership_rejected",
            data={'group_id': membership.group.id}
        )
        
        return Response({'status': 'rejected'})

    @action(detail=True, methods=['post'])
    def declare_deceased(self, request, pk=None):
        membership = self.get_object()
        if not membership.group.is_admin(request.user):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)
        
        # Update membership status
        membership.is_deceased = True
        membership.is_active = False # No longer an active contributor
        membership.save()
        
        # Create Deceased record in condolence app if it doesn't exist
        deceased_record = Deceased.objects.filter(deceased=membership.member).first()
        if not deceased_record:
            Deceased.objects.create(
                deceased=membership.member,
                group=membership.group,
                group_admin=request.user.profile
            )
        
        # Notify other admins
        admins = membership.group.members.filter(groupmembership__is_admin=True, groupmembership__status='active')
        for admin_profile in admins:
            if admin_profile == request.user.profile: continue # Skip sender
            send_push_notification(
                user=admin_profile.user,
                title="Deceased Member Report",
                message=f"{membership.member.full_name} has been declared deceased in {membership.group.name}.",
                notification_type="deceased_declared",
                data={'group_id': membership.group.id, 'deceased_id': membership.member.id}
            )
            
        return Response({'status': 'deceased_declared'}, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def change_role(self, request, pk=None):
        membership = self.get_object()
        if not membership.group.is_admin(request.user):
            return Response({'error': 'Not authorized'}, status=status.HTTP_403_FORBIDDEN)

        new_role = request.data.get('role')
        if new_role not in dict(GroupMembership.ROLE_CHOICES):
            return Response({'error': 'Invalid role'}, status=status.HTTP_400_BAD_REQUEST)

        # Protect the creator's admin role from being downgraded
        if membership.member.user == membership.group.creator and new_role == 'member':
            return Response(
                {'error': 'The group creator cannot be demoted from admin.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        membership.role = new_role
        was_admin = membership.is_admin
        membership.is_admin = new_role in ['admin', 'moderator']
        membership.save()

        # Notify other members if promoted and notify_on_member_promote is enabled
        if membership.is_admin and not was_admin and membership.group.notify_on_member_promote:
            for active_mem in membership.group.groupmembership_set.filter(status='active').exclude(member=membership.member):
                send_push_notification(
                    user=active_mem.member.user,
                    title="Group Admin Promoted",
                    message=f"{membership.member.full_name} has been promoted to Admin in {membership.group.name}.",
                    notification_type="member_promoted",
                    data={'group_id': membership.group.id}
                )

        return Response({
            'status': 'role updated',
            'role': membership.role,
            'is_admin': membership.is_admin
        })

    @action(detail=True, methods=['patch', 'post'])
    def update_member_management(self, request, pk=None):
        membership = self.get_object()
        if not membership.group.is_admin(request.user):
            return Response({'error': 'Not authorized. Only group admins can update member details.'}, status=status.HTTP_403_FORBIDDEN)

        profile = membership.member
        data = request.data

        # 1. Update Role
        was_admin = membership.is_admin
        if 'role' in data:
            new_role = data['role']
            if new_role in dict(GroupMembership.ROLE_CHOICES):
                if membership.member.user != membership.group.creator or new_role != 'member':
                    membership.role = new_role
                    membership.is_admin = (new_role in ['admin', 'moderator'])

        # 2. Update Active status
        if 'is_active' in data:
            is_act = bool(data['is_active'])
            membership.is_active = is_act
            if is_act:
                membership.status = 'active'
            else:
                membership.status = 'inactive'

        # 3. Update Deceased status
        if 'is_deceased' in data:
            is_dec = bool(data['is_deceased'])
            membership.is_deceased = is_dec
            profile.is_deceased = is_dec
            if is_dec:
                membership.is_active = False
                membership.status = 'inactive'
                deceased_record = Deceased.objects.filter(deceased=profile).first()
                if not deceased_record:
                    Deceased.objects.create(
                        deceased=profile,
                        group=membership.group,
                        group_admin=request.user.profile
                    )

        # 4. Update Date of Death
        if 'date_of_death' in data:
            dod = data['date_of_death']
            profile.date_of_death = dod if dod else None

        membership.save()
        profile.save()

        # Notify other members if promoted and notify_on_member_promote is enabled
        if membership.is_admin and not was_admin and membership.group.notify_on_member_promote:
            for active_mem in membership.group.groupmembership_set.filter(status='active').exclude(member=membership.member):
                send_push_notification(
                    user=active_mem.member.user,
                    title="Group Admin Promoted",
                    message=f"{membership.member.full_name} has been promoted to Admin in {membership.group.name}.",
                    notification_type="member_promoted",
                    data={'group_id': membership.group.id}
                )

        serializer = GroupMembershipSerializer(membership, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.filter(approved=True).order_by('-created_at')
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), IsAuthorOrReadOnly()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        queryset = Post.objects.filter(approved=True).order_by('-created_at')
        
        # Only filter by group_id on list action to avoid 404s on detail/action endpoints
        if self.action == 'list':
            group_id = self.request.query_params.get('group_id')
            if group_id:
                queryset = queryset.filter(group_id=group_id)
            elif self.request.user.is_authenticated:
                # Audit: Default to active group
                active_mem = GroupMembership.objects.filter(
                    member=self.request.user.profile, 
                    is_active=True
                ).first()
                if active_mem:
                    queryset = queryset.filter(group=active_mem.group)
                else:
                    queryset = queryset.none()
        return queryset

    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        post = self.get_object()
        try:
            profile = request.user.profile
        except Exception:
            return Response({'error': 'Profile not found'}, status=status.HTTP_400_BAD_REQUEST)
            
        if post.likes.filter(id=profile.id).exists():
            post.likes.remove(profile)
            liked = False
        else:
            post.likes.add(profile)
            liked = True
        return Response({
            'liked': liked,
            'likes_count': post.get_likes_count()
        })

    def perform_create(self, serializer):
        try:
            profile = self.request.user.profile
            post = serializer.save(author=profile)
            
            # Notify group members (limited to 20 for performance)
            if post.group:
                members = post.group.members.filter(groupmembership__status='active').exclude(id=profile.id)[:20]
                for member in members:
                    send_push_notification(
                        user=member.user, # Profile -> User
                        title=f"New Post in {post.group.name}",
                        message=f"{profile.full_name} posted: {post.content[:40]}{'...' if len(post.content) > 40 else ''}",
                        notification_type="new_post",
                        data={'post_id': post.id, 'group_id': post.group.id}
                    )

        except Exception as e:
            # Handle potential missing profile or notification errors
            print(f"Error in post creation/notification: {e}")
            if not serializer.instance: # If save failed before
                 serializer.save()

class PostImageViewSet(viewsets.ModelViewSet):
    queryset = PostImage.objects.all()
    serializer_class = PostImageSerializer
    permission_classes = [permissions.IsAuthenticated, IsPostImageAuthorOrReadOnly]

class CommentViewSet(viewsets.ModelViewSet):
    queryset = Comment.objects.all().order_by('-created_at')
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAuthorOrReadOnly]

    def get_queryset(self):
        queryset = Comment.objects.all().order_by('-created_at')
        post_id = self.request.query_params.get('post_id')
        if post_id:
            queryset = queryset.filter(post_id=post_id)
        return queryset

    def perform_create(self, serializer):
        try:
            profile = self.request.user.profile
            serializer.save(author=profile)
        except Exception:
            serializer.save()

class ReplyViewSet(viewsets.ModelViewSet):
    queryset = Reply.objects.all().order_by('created_at')
    serializer_class = ReplySerializer
    permission_classes = [permissions.IsAuthenticated, IsAuthorOrReadOnly]

    def perform_create(self, serializer):
        try:
            profile = self.request.user.profile
            serializer.save(author=profile)
        except Exception:
            serializer.save()

class DeceasedViewSet(viewsets.ModelViewSet):
    queryset = Deceased.objects.filter(cont_is_active=True)
    serializer_class = DeceasedSerializer

    def get_queryset(self):
        queryset = Deceased.objects.filter(cont_is_active=True)
        group_id = self.request.query_params.get('group')
        
        if group_id:
            queryset = queryset.filter(group_id=group_id)
        elif self.request.user.is_authenticated:
            # Fallback to the user's active group
            active_membership = GroupMembership.objects.filter(
                member=self.request.user.profile, 
                is_active=True
            ).first()
            if active_membership:
                queryset = queryset.filter(group=active_membership.group)
            else:
                # If no active group, maybe return empty or all? 
                # For safety in this "filtered" audit, let's return none if they have no active group but are expecting a filtered list
                queryset = queryset.none()
        
        return queryset.order_by('-date')

    @action(detail=True, methods=['post'])
    def disburse_funds(self, request, pk=None):
        deceased = self.get_object()
        if not deceased.group.is_admin(request.user):
            return Response({'error': 'Only group admins can disburse funds'}, status=status.HTTP_403_FORBIDDEN)
        
        pin = request.data.get('pin')
        pin_valid, pin_error = verify_user_pin(request.user, pin)
        if not pin_valid:
            return pin_error
        
        if not deceased.beneficiary:
            return Response({'error': 'No beneficiary assigned'}, status=status.HTTP_400_BAD_REQUEST)
        
        balance = deceased.get_balance()
        if balance <= 0:
            return Response({'error': 'No funds available for disbursement'}, status=status.HTTP_400_BAD_REQUEST)

        # Multi-admin approval path
        if deceased.group and (deceased.group.min_disbursement_approvals > 1 or deceased.group.get_admin_count() > 1):
            from wallet.models import GroupWalletTransferRequest
            from wallet.serializers import GroupWalletTransferRequestSerializer

            transfer_request = GroupWalletTransferRequest.objects.create(
                group=deceased.group,
                requested_by=request.user,
                recipient_profile=deceased.beneficiary,
                amount=balance,
                deceased_contribution=deceased,
                note=f"Bereavement payout for {deceased.full_name}",
            )
            transfer_request.approvals.add(request.user)
            transfer_request.save()

            if transfer_request.can_execute():
                try:
                    transfer_request.execute()
                except Exception as exc:
                    return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                serializer = GroupWalletTransferRequestSerializer(transfer_request, context={'request': request})
                return Response({'status': 'executed', 'request': serializer.data})

            serializer = GroupWalletTransferRequestSerializer(transfer_request, context={'request': request})
            return Response({
                'status': 'pending_approval',
                'approvals_given': transfer_request.approvals.count(),
                'approvals_needed': transfer_request.required_approvals,
                'request': serializer.data,
            }, status=status.HTTP_202_ACCEPTED)
        from wallet.models import Wallet, Transaction
        from django.db import transaction as db_transaction
        
        # Get or create beneficiary wallet
        beneficiary_wallet, _ = Wallet.objects.get_or_create(
            user=deceased.beneficiary.user, 
            defaults={'external_wallet_id': f"WAAS_{deceased.beneficiary.user.id}"}
        )
        
        with db_transaction.atomic():
            # Create payout transaction for beneficiary
            transaction = Transaction.objects.create(
                wallet=beneficiary_wallet,
                transaction_type='PAYOUT_RECEIVED',
                amount=balance,
                status='COMPLETED',
                destination_group=deceased.group,
                deceased_contribution=deceased,
                waas_reference_id=f"PAY_{timezone.now().timestamp()}"
            )
            beneficiary_wallet.recalculate_balance()
            
            # We no longer close the fund automatically here
            # deceased.funds_disbursed = True
            # deceased.contributions_open = False
            deceased.save()
            
            # Notify beneficiary
            send_push_notification(
                user=deceased.beneficiary.user,
                title="Funds Received",
                message=f"You received {balance} for {deceased.deceased.full_name}.",
                notification_type="funds_disbursed",
                data={'amount': str(balance)}
            )
            
        return Response({
            'status': 'success',
            'amount': balance,
            'beneficiary': deceased.beneficiary.full_name,
            'transaction': TransactionSerializer(transaction).data
        })

class ContributionViewSet(viewsets.ModelViewSet):
    queryset = Contribution.objects.all()
    serializer_class = ContributionSerializer
    pagination_class = StandardPagination

    def list(self, request, *args, **kwargs):
        profile = request.user.profile
        # Fetch both deceased contributions and campaign contributions
        legacy_contribs = Contribution.objects.filter(contributing_member=profile)
        camp_contribs = CampaignContribution.objects.filter(contributing_member=profile)

        group_id = request.query_params.get('group_id')
        if group_id:
            legacy_contribs = legacy_contribs.filter(group_id=group_id)
            camp_contribs = camp_contribs.filter(group_id=group_id)

        legacy_data = ContributionSerializer(legacy_contribs, many=True, context={'request': request}).data
        camp_data = CampaignContributionSerializer(camp_contribs, many=True, context={'request': request}).data

        # Standardize items
        combined = []
        for item in legacy_data:
            item['type'] = 'deceased'
            combined.append(item)
        for item in camp_data:
            item['type'] = 'campaign'
            item['contribution_date'] = item.get('contribution_date')
            combined.append(item)

        # Sort by contribution_date descending
        combined.sort(key=lambda x: x.get('contribution_date') or '', reverse=True)
        return Response(combined)

def _apply_platform_fee(transaction, fee_type):
    from decimal import Decimal
    from wallet.models import PlatformFeeConfig, PlatformFeeLedger, Wallet, Transaction

    config = PlatformFeeConfig.get_config()
    gross = Decimal(str(transaction.amount))

    if not config.is_fees_enabled or gross <= 0:
        transaction.fee_amount = Decimal('0.00')
        transaction.net_amount = gross
        transaction.save(update_fields=['fee_amount', 'net_amount'])
        return

    if fee_type == 'TOP_UP':
        pct = config.topup_percentage_fee / Decimal('100.00')
        flat = config.topup_flat_fee
        ledger_type = 'TOP_UP_FEE'
    elif fee_type == 'WITHDRAWAL':
        pct = config.withdrawal_percentage_fee / Decimal('100.00')
        flat = config.withdrawal_flat_fee
        ledger_type = 'WITHDRAWAL_FEE'
    elif fee_type == 'GROUP_TRANSFER':
        pct = config.group_transfer_percentage_fee / Decimal('100.00')
        flat = config.group_transfer_flat_fee
        ledger_type = 'GROUP_TRANSFER_FEE'
    else:
        pct = Decimal('0.00')
        flat = Decimal('0.00')
        ledger_type = 'TOP_UP_FEE'

    fee = (gross * pct) + flat
    fee = min(fee, gross).quantize(Decimal('0.01'))
    net = (gross - fee).quantize(Decimal('0.01'))

    transaction.fee_amount = fee
    transaction.net_amount = net
    transaction.save(update_fields=['fee_amount', 'net_amount'])

    if fee > 0:
        PlatformFeeLedger.objects.create(
            transaction=transaction,
            fee_type=ledger_type,
            gross_amount=gross,
            fee_amount=fee,
            net_amount=net
        )
        treasury_wallet = Wallet.get_treasury_wallet()
        Transaction.objects.create(
            wallet=treasury_wallet,
            transaction_type='PLATFORM_FEE_COLLECTED',
            amount=fee,
            net_amount=fee,
            status='COMPLETED',
            sender_wallet=transaction.wallet,
            destination_group=transaction.destination_group,
            fund_campaign=transaction.fund_campaign,
            deceased_contribution=transaction.deceased_contribution,
            note=f"Platform Fee ({ledger_type}) from Transaction #{transaction.id}"
        )
        treasury_wallet.recalculate_balance()


class WalletViewSet(viewsets.ModelViewSet):
    serializer_class = WalletSerializer

    def get_queryset(self):
        return Wallet.objects.filter(user=self.request.user)

    @action(detail=False, methods=['get'])
    def balance(self, request):
        wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={'external_wallet_id': f"WAAS_{request.user.id}"})
        return Response({'balance': wallet.get_balance()})

    @action(detail=False, methods=['get'])
    def treasury(self, request):
        if not request.user.is_staff and not request.user.is_superuser:
            return Response({'error': 'Unauthorized. Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        treasury_wallet = Wallet.get_treasury_wallet()
        treasury_balance = treasury_wallet.get_balance()
        
        from django.db.models import Sum, Count
        from wallet.models import PlatformFeeLedger, Transaction
        
        total_fees = PlatformFeeLedger.objects.aggregate(total=Sum('fee_amount'))['total'] or 0.00
        
        breakdown_query = PlatformFeeLedger.objects.values('fee_type').annotate(
            total_amount=Sum('fee_amount'),
            transaction_count=Count('id')
        )
        
        breakdown = {}
        for item in breakdown_query:
            breakdown[item['fee_type']] = {
                'total_amount': float(item['total_amount']),
                'count': item['transaction_count']
            }
            
        recent_fee_txs = TransactionSerializer(
            treasury_wallet.transactions.order_by('-timestamp')[:20],
            many=True,
            context={'request': request}
        ).data

        return Response({
            'treasury_wallet_id': treasury_wallet.id,
            'external_wallet_id': treasury_wallet.external_wallet_id,
            'treasury_balance': float(treasury_balance),
            'total_fees_collected': float(total_fees),
            'breakdown_by_type': breakdown,
            'recent_transactions': recent_fee_txs,
        })

    @action(detail=False, methods=['get'], url_path='fee-config')
    def fee_config(self, request):
        from decimal import Decimal
        config = PlatformFeeConfig.get_config()
        serializer = PlatformFeeConfigSerializer(config)
        
        amount = request.query_params.get('amount')
        fee_type = request.query_params.get('type', 'TOP_UP')
        
        quote = None
        if amount:
            try:
                amt = Decimal(str(amount))
                if fee_type == 'TOP_UP':
                    pct = config.topup_percentage_fee / Decimal('100.00')
                    flat = config.topup_flat_fee
                elif fee_type == 'WITHDRAWAL':
                    pct = config.withdrawal_percentage_fee / Decimal('100.00')
                    flat = config.withdrawal_flat_fee
                else:
                    pct = config.group_transfer_percentage_fee / Decimal('100.00')
                    flat = config.group_transfer_flat_fee
                
                if config.is_fees_enabled:
                    fee = min((amt * pct) + flat, amt).quantize(Decimal('0.01'))
                    net = (amt - fee).quantize(Decimal('0.01'))
                else:
                    fee = Decimal('0.00')
                    net = amt
                    
                quote = {
                    'gross_amount': str(amt),
                    'fee_amount': str(fee),
                    'net_amount': str(net)
                }
            except Exception:
                pass
                
        return Response({
            'config': serializer.data,
            'quote': quote
        })

    @action(detail=False, methods=['get'], url_path='sms-packages')
    def sms_packages(self, request):
        from decimal import Decimal
        if not SMSCreditPackage.objects.exists():
            SMSCreditPackage.objects.bulk_create([
                SMSCreditPackage(name="Starter Pack", credits_count=50, price=Decimal('25.00')),
                SMSCreditPackage(name="Standard Pack", credits_count=200, price=Decimal('90.00')),
                SMSCreditPackage(name="Pro Pack", credits_count=500, price=Decimal('200.00')),
            ])
        packages = SMSCreditPackage.objects.filter(is_active=True).order_by('price')
        serializer = SMSCreditPackageSerializer(packages, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='buy-sms-package')
    def buy_sms_package(self, request):
        from decimal import Decimal
        package_id = request.data.get('package_id')
        group_id = request.data.get('group_id')
        
        if not package_id or not group_id:
            return Response({'error': 'package_id and group_id are required.'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            package = SMSCreditPackage.objects.get(id=package_id, is_active=True)
            group = Group.objects.get(id=group_id)
        except (SMSCreditPackage.DoesNotExist, Group.DoesNotExist):
            return Response({'error': 'Invalid SMS package or group.'}, status=status.HTTP_404_NOT_FOUND)
            
        # Verify user is group admin or staff
        membership = group.groupmembership_set.filter(member__user=request.user, role='admin', status='active').first()
        if not membership and not request.user.is_staff:
            return Response({'error': 'Only group admins can purchase SMS packages.'}, status=status.HTTP_403_FORBIDDEN)
            
        wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={'external_wallet_id': f"WAAS_{request.user.id}"})
        if wallet.get_balance() < package.price:
            return Response({'error': f'Insufficient wallet balance. R{package.price} required.'}, status=status.HTTP_400_BAD_REQUEST)
            
        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            tx = Transaction.objects.create(
                wallet=wallet,
                transaction_type='SMS_PACKAGE_PURCHASE',
                amount=package.price,
                fee_amount=Decimal('0.00'),
                net_amount=package.price,
                status='COMPLETED',
                destination_group=group,
                note=f"Purchased {package.name} ({package.credits_count} SMS credits)"
            )
            wallet.recalculate_balance()
            
            sms_bal, _ = GroupSMSCreditBalance.objects.get_or_create(group=group)
            sms_bal.balance += package.credits_count
            sms_bal.save()
            
            purchase = SMSCreditPurchase.objects.create(
                group=group,
                package=package,
                purchased_by=request.user,
                credits_added=package.credits_count,
                amount_paid=package.price,
                transaction=tx
            )
            
            PlatformFeeLedger.objects.create(
                transaction=tx,
                fee_type='SMS_PACKAGE_FEE',
                gross_amount=package.price,
                fee_amount=package.price,
                net_amount=Decimal('0.00')
            )
            
        return Response({
            'status': 'success',
            'new_sms_balance': sms_bal.balance,
            'purchase': SMSCreditPurchaseSerializer(purchase).data
        })

    @action(detail=False, methods=['get'], url_path='group-sms-balance')
    def group_sms_balance(self, request):
        group_id = request.query_params.get('group_id')
        if not group_id:
            return Response({'error': 'group_id query param required.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            group = Group.objects.get(id=group_id)
            sms_bal, _ = GroupSMSCreditBalance.objects.get_or_create(group=group)
            return Response(GroupSMSCreditBalanceSerializer(sms_bal).data)
        except Group.DoesNotExist:
            return Response({'error': 'Group not found.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['post'], url_path='subscribe-group')
    def subscribe_group(self, request):
        from decimal import Decimal
        config = PlatformFeeConfig.get_config()
        if not config.is_saas_subscriptions_enabled:
            return Response({
                'error': 'Phase 2 Group SaaS Subscriptions are currently disabled in platform settings.'
            }, status=status.HTTP_403_FORBIDDEN)

        group_id = request.data.get('group_id')
        tier = request.data.get('tier', 'PRO').upper()
        if not group_id or tier not in ('PRO', 'ENTERPRISE'):
            return Response({'error': 'group_id and valid tier (PRO/ENTERPRISE) are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            group = Group.objects.get(id=group_id)
        except Group.DoesNotExist:
            return Response({'error': 'Group not found.'}, status=status.HTTP_404_NOT_FOUND)

        price = config.group_pro_monthly_price if tier == 'PRO' else Decimal('500.00')
        wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={'external_wallet_id': f"WAAS_{request.user.id}"})
        if wallet.get_balance() < price:
            return Response({'error': f'Insufficient wallet balance. R{price} required for Group {tier}.'}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction as db_transaction
        from django.utils import timezone
        import datetime
        with db_transaction.atomic():
            tx = Transaction.objects.create(
                wallet=wallet,
                transaction_type='SMS_PACKAGE_PURCHASE',
                amount=price,
                fee_amount=Decimal('0.00'),
                net_amount=price,
                status='COMPLETED',
                destination_group=group,
                note=f"Group {tier} SaaS Subscription"
            )
            wallet.recalculate_balance()

            sub, _ = GroupSubscription.objects.get_or_create(group=group)
            sub.tier = tier
            sub.is_active = True
            sub.monthly_price = price
            sub.expires_at = timezone.now() + datetime.timedelta(days=30)
            sub.save()

            PlatformFeeLedger.objects.create(
                transaction=tx,
                fee_type='GROUP_SAAS_FEE',
                gross_amount=price,
                fee_amount=price,
                net_amount=Decimal('0.00')
            )

        return Response({
            'status': 'success',
            'subscription': GroupSubscriptionSerializer(sub).data
        })

    @action(detail=False, methods=['post'], url_path='subscribe-user')
    def subscribe_user(self, request):
        from decimal import Decimal
        config = PlatformFeeConfig.get_config()
        if not config.is_saas_subscriptions_enabled:
            return Response({
                'error': 'Phase 2 Komunity Plus Subscriptions are currently disabled in platform settings.'
            }, status=status.HTTP_403_FORBIDDEN)

        price = config.komunity_plus_monthly_price
        wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={'external_wallet_id': f"WAAS_{request.user.id}"})
        if wallet.get_balance() < price:
            return Response({'error': f'Insufficient wallet balance. R{price} required for Komunity Plus.'}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction as db_transaction
        from django.utils import timezone
        import datetime
        with db_transaction.atomic():
            tx = Transaction.objects.create(
                wallet=wallet,
                transaction_type='SMS_PACKAGE_PURCHASE',
                amount=price,
                fee_amount=Decimal('0.00'),
                net_amount=price,
                status='COMPLETED',
                note="Komunity Plus Member Subscription"
            )
            wallet.recalculate_balance()

            sub, _ = UserSubscription.objects.get_or_create(user=request.user)
            sub.is_active = True
            sub.expires_at = timezone.now() + datetime.timedelta(days=30)
            sub.save()

            PlatformFeeLedger.objects.create(
                transaction=tx,
                fee_type='KOMUNITY_PLUS_FEE',
                gross_amount=price,
                fee_amount=price,
                net_amount=Decimal('0.00')
            )

        return Response({
            'status': 'success',
            'subscription': UserSubscriptionSerializer(sub).data
        })

    @action(detail=False, methods=['get'], url_path='vendors')
    def vendors(self, request):
        config = PlatformFeeConfig.get_config()
        if not config.is_vendor_marketplace_enabled:
            return Response({
                'error': 'Phase 3 Vendor Marketplace is currently disabled in platform settings.'
            }, status=status.HTTP_403_FORBIDDEN)

        if not ServiceVendor.objects.exists():
            ServiceVendor.objects.bulk_create([
                ServiceVendor(name="Dignity Funeral Services", category="FUNERAL_PARLOR", contact_phone="0115550199", contact_email="contact@dignity.co.za", rating=4.90),
                ServiceVendor(name="Ubuntu Event Catering", category="CATERING", contact_phone="0115550288", contact_email="info@ubuntucatering.co.za", rating=4.85),
                ServiceVendor(name="Harmony Grief Support & Counseling", category="COUNSELING", contact_phone="0115550377", contact_email="help@harmonygrief.org", rating=5.00),
            ])

        vendors = ServiceVendor.objects.filter(is_active=True)
        serializer = ServiceVendorSerializer(vendors, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='book-vendor')
    def book_vendor(self, request):
        from decimal import Decimal
        config = PlatformFeeConfig.get_config()
        if not config.is_vendor_marketplace_enabled:
            return Response({
                'error': 'Phase 3 Vendor Marketplace is currently disabled in platform settings.'
            }, status=status.HTTP_403_FORBIDDEN)

        vendor_id = request.data.get('vendor_id')
        amount = request.data.get('amount')
        description = request.data.get('description', 'Service Booking')
        group_id = request.data.get('group_id')

        if not vendor_id or not amount:
            return Response({'error': 'vendor_id and amount are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            vendor = ServiceVendor.objects.get(id=vendor_id, is_active=True)
            amt = Decimal(str(amount))
        except (ServiceVendor.DoesNotExist, Exception):
            return Response({'error': 'Invalid vendor or amount.'}, status=status.HTTP_400_BAD_REQUEST)

        commission_pct = config.vendor_commission_percentage / Decimal('100.00')
        commission = (amt * commission_pct).quantize(Decimal('0.01'))

        wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={'external_wallet_id': f"WAAS_{request.user.id}"})
        if wallet.get_balance() < amt:
            return Response({'error': f'Insufficient wallet balance. R{amt} required.'}, status=status.HTTP_400_BAD_REQUEST)

        from django.db import transaction as db_transaction
        with db_transaction.atomic():
            tx = Transaction.objects.create(
                wallet=wallet,
                transaction_type='TRANSFER',
                amount=amt,
                fee_amount=commission,
                net_amount=amt - commission,
                status='COMPLETED',
                note=f"Vendor Booking: {vendor.name}"
            )
            wallet.recalculate_balance()

            booking = VendorBooking.objects.create(
                vendor=vendor,
                group_id=group_id,
                user=request.user,
                service_description=description,
                booking_amount=amt,
                commission_amount=commission,
                status='CONFIRMED',
                transaction=tx
            )

            if commission > 0:
                PlatformFeeLedger.objects.create(
                    transaction=tx,
                    fee_type='VENDOR_COMMISSION_FEE',
                    gross_amount=amt,
                    fee_amount=commission,
                    net_amount=amt - commission
                )

        return Response({
            'status': 'success',
            'booking': VendorBookingSerializer(booking).data
        })

    @action(detail=False, methods=['get'], url_path='insurance-policies')
    def insurance_policies(self, request):
        from decimal import Decimal
        config = PlatformFeeConfig.get_config()
        if not config.is_vendor_marketplace_enabled:
            return Response({
                'error': 'Phase 3 Micro-Insurance Offerings are currently disabled in platform settings.'
            }, status=status.HTTP_403_FORBIDDEN)

        if not MicroInsurancePolicy.objects.exists():
            MicroInsurancePolicy.objects.bulk_create([
                MicroInsurancePolicy(provider_name="Old Mutual / Sanlam Partner", policy_name="Group Funeral Assurance", cover_amount=Decimal('15000.00'), monthly_premium=Decimal('18.00')),
                MicroInsurancePolicy(provider_name="Hollard Partner", policy_name="Emergency Excess Cover", cover_amount=Decimal('5000.00'), monthly_premium=Decimal('12.00')),
            ])

        policies = MicroInsurancePolicy.objects.filter(is_active=True)
        serializer = MicroInsurancePolicySerializer(policies, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='lookup-recipient')
    def lookup_recipient(self, request):
        """Look up a user profile by phone number for P2P send verification."""
        phone = request.query_params.get('phone', '').strip()
        if not phone:
            return Response({'error': 'Phone number is required.'}, status=status.HTTP_400_BAD_REQUEST)

        # Normalise: strip spaces/dashes
        phone_clean = phone.replace(' ', '').replace('-', '')

        # Try to find by user phone or profile phone
        from django.db.models import Q
        user = CustomUser.objects.filter(
            Q(phone=phone_clean) | Q(profile__phone=phone_clean)
        ).first()

        if not user:
            return Response({'error': 'No Komunity account found for this phone number.'}, status=status.HTTP_404_NOT_FOUND)

        if user == request.user:
            return Response({'error': 'You cannot send money to yourself.'}, status=status.HTTP_400_BAD_REQUEST)

        profile = getattr(user, 'profile', None)
        full_name = getattr(profile, 'full_name', None) or (
            f"{getattr(profile, 'first_name', '')} {getattr(profile, 'surname', '')}".strip()
        ) or user.phone or str(user.id)

        return Response({
            'user_id': user.id,
            'full_name': full_name,
            'phone': phone_clean,
            'is_verified': getattr(profile, 'is_verified', False),
        })

    @action(detail=False, methods=['get'])
    def saved_cards(self, request):
        from wallet.models import SavedCard
        from wallet.serializers import SavedCardSerializer
        cards = SavedCard.objects.filter(user=request.user).order_by('-is_default', '-created_at')
        return Response(SavedCardSerializer(cards, many=True).data)

    @action(detail=False, methods=['post'])
    def delete_saved_card(self, request):
        from wallet.models import SavedCard
        card_id = request.data.get('card_id')
        if not card_id:
            return Response({'error': 'card_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        card = SavedCard.objects.filter(id=card_id, user=request.user).first()
        if not card:
            return Response({'error': 'Card not found.'}, status=status.HTTP_404_NOT_FOUND)
        was_default = card.is_default
        card.delete()
        if was_default:
            next_card = SavedCard.objects.filter(user=request.user).first()
            if next_card:
                next_card.is_default = True
                next_card.save(update_fields=['is_default'])
        return Response({'status': 'success', 'message': 'Card deleted successfully.'})

    @action(detail=False, methods=['post'])
    def set_default_card(self, request):
        from wallet.models import SavedCard
        card_id = request.data.get('card_id')
        if not card_id:
            return Response({'error': 'card_id is required.'}, status=status.HTTP_400_BAD_REQUEST)
        card = SavedCard.objects.filter(id=card_id, user=request.user).first()
        if not card:
            return Response({'error': 'Card not found.'}, status=status.HTTP_404_NOT_FOUND)
        SavedCard.objects.filter(user=request.user).update(is_default=False)
        card.is_default = True
        card.save(update_fields=['is_default'])
        return Response({'status': 'success', 'message': 'Default card updated.'})

    @action(detail=False, methods=['post'])
    def top_up(self, request):
        import uuid
        from wallet.flutterwave import charge_voucher, charge_card, charge_saved_card, detect_card_brand
        from wallet.models import SavedCard

        wallet, _ = Wallet.objects.get_or_create(
            user=request.user,
            defaults={'external_wallet_id': f"WAAS_{request.user.id}"}
        )

        payment_method = request.data.get('payment_method', 'voucher')
        if payment_method not in ('voucher', 'card', 'saved_card'):
            return Response({'error': 'Invalid payment_method. Must be card, saved_card, or voucher.'}, status=status.HTTP_400_BAD_REQUEST)

        voucher_pin = None
        card_number = None
        expiry_month = None
        expiry_year = None
        cvv = None
        amount = 100.00
        saved_card = None

        if payment_method == 'voucher':
            voucher_pin = request.data.get('voucher_pin')
            if not voucher_pin:
                return Response({'error': 'voucher_pin is required'}, status=status.HTTP_400_BAD_REQUEST)
        elif payment_method == 'saved_card':
            saved_card_id = request.data.get('saved_card_id')
            amount_val = request.data.get('amount')
            if not saved_card_id or not amount_val:
                return Response({'error': 'saved_card_id and amount are required.'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                amount = float(amount_val)
                if amount <= 0:
                    raise ValueError()
            except (TypeError, ValueError):
                return Response({'error': 'Invalid amount.'}, status=status.HTTP_400_BAD_REQUEST)

            saved_card = SavedCard.objects.filter(id=saved_card_id, user=request.user).first()
            if not saved_card:
                return Response({'error': 'Saved card not found.'}, status=status.HTTP_404_NOT_FOUND)
        else:
            card_number = request.data.get('card_number')
            expiry_month = request.data.get('expiry_month')
            expiry_year = request.data.get('expiry_year')
            cvv = request.data.get('cvv')
            amount_val = request.data.get('amount')

            if not all([card_number, expiry_month, expiry_year, cvv, amount_val]):
                return Response({'error': 'card_number, expiry_month, expiry_year, cvv, and amount are required'}, status=status.HTTP_400_BAD_REQUEST)
            try:
                amount = float(amount_val)
                if amount <= 0:
                    raise ValueError()
            except ValueError:
                return Response({'error': 'Invalid amount.'}, status=status.HTTP_400_BAD_REQUEST)

        # Create a PENDING transaction log entry first
        tx_ref = f"api-topup-{uuid.uuid4().hex[:8]}"
        transaction = Transaction.objects.create(
            wallet=wallet,
            transaction_type='TOP_UP',
            amount=0 if payment_method == 'voucher' else amount,
            status='PENDING',
            voucher_reference=voucher_pin,
        )

        # Get phone from user profile if available
        phone = getattr(getattr(request.user, 'profile', None), 'phone', None) or '0000000000'
        user_email = getattr(getattr(request.user, 'profile', None), 'email', 'user@example.com') or 'user@example.com'

        # Call Flutterwave Sandbox
        if payment_method == 'voucher':
            flw_response = charge_voucher(
                voucher_pin=voucher_pin,
                amount=100.00,   # Sandbox: default 100 ZAR; amount comes back from the voucher
                email=user_email,
                phone_number=phone,
                tx_ref=tx_ref
            )
        elif payment_method == 'saved_card':
            flw_response = charge_saved_card(
                customer_id=saved_card.customer_id,
                payment_method_id=saved_card.payment_method_id,
                amount=amount,
                tx_ref=tx_ref
            )
        else:
            flw_response = charge_card(
                card_number=card_number,
                expiry_month=expiry_month,
                expiry_year=expiry_year,
                cvv=cvv,
                amount=amount,
                email=user_email,
                phone_number=phone,
                tx_ref=tx_ref
            )

        if flw_response.get('success'):
            amount_val = flw_response.get('amount', amount)
            transaction.status = 'COMPLETED'
            transaction.amount = amount_val
            transaction.waas_reference_id = str(flw_response.get('waas_ref', tx_ref))
            transaction.save()
            _apply_platform_fee(transaction, 'TOP_UP')
            wallet.recalculate_balance()

            # Handle optional card saving
            save_card = request.data.get('save_card')
            if payment_method == 'card' and save_card in (True, 'true', 'True', 1, '1'):
                cust_id = flw_response.get('customer_id')
                pm_id = flw_response.get('payment_method_id')
                if cust_id and pm_id:
                    clean_num = card_number.replace(' ', '').replace('-', '')
                    brand = detect_card_brand(clean_num)
                    last4 = clean_num[-4:]
                    exp_m = str(expiry_month).zfill(2)
                    exp_y = str(expiry_year)
                    is_first = not SavedCard.objects.filter(user=request.user).exists()
                    SavedCard.objects.update_or_create(
                        user=request.user,
                        payment_method_id=pm_id,
                        defaults={
                            'customer_id': cust_id,
                            'card_brand': brand,
                            'last4': last4,
                            'expiry_month': exp_m,
                            'expiry_year': exp_y,
                            'is_default': is_first,
                        }
                    )

            return Response({
                'status': 'success',
                'balance': str(wallet.get_balance()),
                'transaction': TransactionSerializer(transaction).data
            })
        else:
            transaction.status = 'FAILED'
            transaction.save()
            return Response(
                {'error': flw_response.get('error', 'Top-up failed.')},
                status=status.HTTP_400_BAD_REQUEST
            )


    @action(detail=False, methods=['post'])
    def withdraw(self, request):
        pin = request.data.get('pin')
        pin_valid, pin_error = verify_user_pin(request.user, pin)
        if not pin_valid:
            return pin_error

        wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={'external_wallet_id': f"WAAS_{request.user.id}"})
        amount = request.data.get('amount')
        channel = request.data.get('channel')
        metadata = request.data.get('metadata', {}) or {}
        currency = request.data.get('currency', 'ZAR')

        if not amount or not channel:
            return Response({'error': 'Amount and channel are required.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            amount_val = float(amount)
            if amount_val <= 0:
                return Response({'error': 'Amount must be positive.'}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError:
            return Response({'error': 'Invalid amount format.'}, status=status.HTTP_400_BAD_REQUEST)

        if wallet.get_balance() < amount_val:
            return Response({'error': 'Insufficient balance.'}, status=status.HTTP_400_BAD_REQUEST)

        if channel not in [choice.value for choice in Transaction.TransactionChannel]:
            return Response({'error': 'Unsupported payout channel.'}, status=status.HTTP_400_BAD_REQUEST)

        from wallet.encryption import encrypt_metadata
        encrypted_meta = encrypt_metadata({**metadata, 'currency': currency})

        transaction = Transaction.objects.create(
            wallet=wallet,
            transaction_type='WITHDRAWAL',
            amount=amount_val,
            status='PENDING',
            withdrawal_channel=channel,
            withdrawal_metadata=encrypted_meta
        )

        api_response = waas_api_withdraw(wallet.external_wallet_id, channel, metadata, amount_val, currency)
        if api_response['success']:
            transaction.status = 'COMPLETED'
            transaction.waas_reference_id = api_response['waas_ref']
            # Persist any extra data (e.g. voucher_code) into withdrawal_metadata
            updated_metadata = {**metadata, 'currency': currency}
            if api_response.get('voucher_code'):
                updated_metadata['voucher_code'] = api_response['voucher_code']
            if api_response.get('partner'):
                updated_metadata['partner'] = api_response['partner']
            transaction.withdrawal_metadata = encrypt_metadata(updated_metadata)
            transaction.save()
            _apply_platform_fee(transaction, 'WITHDRAWAL')
            wallet.recalculate_balance()

            response_data = {
                'status': 'success',
                'balance': wallet.get_balance(),
                'transaction': TransactionSerializer(transaction).data
            }
            # Include voucher code in top-level response so frontend can display it
            if api_response.get('voucher_code'):
                response_data['voucher_code'] = api_response['voucher_code']
                response_data['partner'] = api_response.get('partner', '')
            return Response(response_data)
        else:
            transaction.status = 'FAILED'
            transaction.save()
            return Response({'error': api_response.get('error', 'Withdrawal failed.')}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    def send_money(self, request):
        pin = request.data.get('pin')
        pin_valid, pin_error = verify_user_pin(request.user, pin)
        if not pin_valid:
            return pin_error

        from django.db import transaction as db_transaction
        
        sender_wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={'external_wallet_id': f"WAAS_{request.user.id}"})
        recipient_user_id = request.data.get('recipient_user_id')
        amount = request.data.get('amount')
        note = request.data.get('note', '')

        if not recipient_user_id or not amount:
            return Response({'error': 'Recipient and amount are required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            amount_val = float(amount)
            if amount_val <= 0:
                return Response({'error': 'Amount must be positive'}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError:
            return Response({'error': 'Invalid amount format'}, status=status.HTTP_400_BAD_REQUEST)

        # Check if sending to self
        if str(request.user.id) == str(recipient_user_id):
            return Response({'error': 'Cannot send money to yourself'}, status=status.HTTP_400_BAD_REQUEST)

        # Get recipient wallet
        try:
            recipient_user = CustomUser.objects.get(id=recipient_user_id)
            recipient_wallet, _ = Wallet.objects.get_or_create(user=recipient_user, defaults={'external_wallet_id': f"WAAS_{recipient_user.id}"})
        except CustomUser.DoesNotExist:
            return Response({'error': 'Recipient not found'}, status=status.HTTP_404_NOT_FOUND)

        # Check balance
        if sender_wallet.get_balance() < amount_val:
            return Response({'error': 'Insufficient balance'}, status=status.HTTP_400_BAD_REQUEST)

        # Resolve sender/recipient display names for notes
        sender_name = getattr(getattr(request.user, 'profile', None), 'full_name', None) or str(request.user)
        recipient_name = getattr(getattr(recipient_user, 'profile', None), 'full_name', None) or str(recipient_user)
        p2p_ref = f"P2P_{timezone.now().timestamp()}"

        # Create both transactions atomically
        with db_transaction.atomic():
            # Debit from sender
            sender_txn = Transaction.objects.create(
                wallet=sender_wallet,
                transaction_type='P2P_SENT',
                amount=amount,
                status='COMPLETED',
                recipient_wallet=recipient_wallet,
                note=f"Sent to {recipient_name}" + (f" — {note}" if note else ""),
                waas_reference_id=p2p_ref,
            )

            # Credit to recipient — link back the sender wallet
            recipient_txn = Transaction.objects.create(
                wallet=recipient_wallet,
                transaction_type='P2P_RECEIVED',
                amount=amount,
                status='COMPLETED',
                sender_wallet=sender_wallet,
                note=f"Received from {sender_name}" + (f" — {note}" if note else ""),
                waas_reference_id=p2p_ref,
            )
            sender_wallet.recalculate_balance()
            recipient_wallet.recalculate_balance()

        recipient_info = getattr(getattr(recipient_user, 'profile', None), 'email', None) or recipient_user.phone or str(recipient_user.id)
        return Response({
            'status': 'success',
            'balance': sender_wallet.get_balance(),
            'transaction': TransactionSerializer(sender_txn).data,
            'recipient': recipient_info
        })

    @action(detail=False, methods=['post'])
    def contribute_to_deceased(self, request):
        from condolence.models import Deceased, Contribution
        from django.db import transaction as db_transaction
        
        pin = request.data.get('pin')
        pin_valid, pin_error = verify_user_pin(request.user, pin)
        if not pin_valid:
            return pin_error
        
        wallet, _ = Wallet.objects.get_or_create(user=request.user, defaults={'external_wallet_id': f"WAAS_{request.user.id}"})
        deceased_id = request.data.get('deceased_id')
        amount = request.data.get('amount')

        if not deceased_id or not amount:
            return Response({'error': 'Deceased member and amount are required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            amount_val = float(amount)
            if amount_val <= 0:
                return Response({'error': 'Amount must be positive'}, status=status.HTTP_400_BAD_REQUEST)
        except ValueError:
            return Response({'error': 'Invalid amount format'}, status=status.HTTP_400_BAD_REQUEST)

        # Get deceased member
        try:
            deceased = Deceased.objects.get(id=deceased_id)
        except Deceased.DoesNotExist:
            return Response({'error': 'Deceased member not found'}, status=status.HTTP_404_NOT_FOUND)

        # Check if contributions are still open
        if not deceased.cont_is_active or not deceased.contributions_open:
            return Response({'error': 'Contributions are closed for this member'}, status=status.HTTP_400_BAD_REQUEST)

        # Check balance
        if wallet.get_balance() < amount_val:
            return Response({'error': 'Insufficient balance'}, status=status.HTTP_400_BAD_REQUEST)

        # Resolve deceased name for note
        try:
            _dec_name = deceased.deceased.full_name
        except Exception:
            _dec_name = str(deceased)

        # Create transaction and contribution atomically
        with db_transaction.atomic():
            # Create wallet transaction
            transaction = Transaction.objects.create(
                wallet=wallet,
                transaction_type='TRANSFER',
                amount=amount,
                status='COMPLETED',
                deceased_contribution=deceased,
                note=f"Contribution to bereavement: {_dec_name}",
                waas_reference_id=f"DEC_{timezone.now().timestamp()}"
            )

            # Create contribution record
            contribution = Contribution.objects.create(
                group=deceased.group,
                deceased_member=deceased,
                contributing_member=request.user.profile,
                amount=amount,
                payment_method='wallet',
                transaction=transaction
            )
            wallet.recalculate_balance()

        # Send Notifications
        try:
            # Notify Contributor
            send_push_notification(
                user=request.user,
                title="Contribution Successful",
                message=f"You successfully contributed {amount} to {deceased.deceased.full_name}'s fund.",
                notification_type="contribution_sent",
                data={'contribution_id': contribution.id, 'deceased_id': deceased.id}
            )
            
            # Notify Group Admin
            if deceased.group_admin and deceased.group_admin.user:
                send_push_notification(
                    user=deceased.group_admin.user,
                    title="New Contribution Received",
                    message=f"{request.user.profile.full_name} contributed {amount} to {deceased.deceased.full_name}.",
                    notification_type="contribution_received",
                    data={'contribution_id': contribution.id, 'deceased_id': deceased.id}
                )
        except Exception as e:
            print(f"Error sending contribution notifications: {e}")

        return Response({
            'status': 'success',
            'balance': wallet.get_balance(),
            'transaction': TransactionSerializer(transaction).data,
            'contribution': {
                'id': contribution.id,
                'deceased': deceased.deceased.full_name,
                'amount': str(contribution.amount),
                'total_raised': str(deceased.get_total_raised())
            }
        })

class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TransactionSerializer

    def get_queryset(self):
        return Transaction.objects.filter(wallet__user=self.request.user).order_by('-timestamp')


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

class DeviceTokenViewSet(viewsets.ModelViewSet):
    queryset = DeviceToken.objects.all()
    serializer_class = DeviceTokenSerializer
    # Only authenticated users can register tokens
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return DeviceToken.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        # If token exists for this user, just update updated_at (handled by auto_now)
        # But since token is unique, we might need to handle integrity error or use update_or_create logic manually
        # OR we can let the frontend handle it by checking if it exists?
        # Better: use create to get_or_create.
        pass

    @action(detail=False, methods=['post'])
    def register(self, request):
        token = request.data.get('token')
        platform = request.data.get('platform')
        
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Update or create
        # Ensure token is unique globally and assigned to current user
        device_token, created = DeviceToken.objects.update_or_create(
            token=token,
            defaults={'user': request.user, 'platform': platform, 'is_active': True}
        )
        
        return Response({'status': 'registered', 'created': created})

class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).order_by('-created_at')

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save()
        return Response({'status': 'marked as read', 'id': notification.id})

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        updated_count = Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return Response({'status': 'all marked as read', 'count': updated_count})

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return Response({'unread_count': count})


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def search_api_view(request):
    """
    Search groups and members.
    Query param: q
    """
    from django.db.models import Q
    query = request.GET.get('q', '').strip()
    
    if not query:
        return Response({'groups': [], 'members': []})

    groups = Group.objects.filter(
        Q(name__icontains=query) | 
        Q(description__icontains=query)
    ).distinct()

    members = Profile.objects.filter(
        Q(user__email__icontains=query) | 
        Q(first_name__icontains=query) | 
        Q(surname__icontains=query)
    ).distinct()

    return Response({
        'groups': GroupSerializer(groups, many=True, context={'request': request}).data,
        'members': ProfileSerializer(members, many=True, context={'request': request}).data
    })


from django.shortcuts import redirect

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

class FundCampaignViewSet(viewsets.ModelViewSet):
    """
    CRUD for FundCampaign objects.

    Endpoints:
      GET    /api/v1/campaigns/                 - list campaigns for active/requested group
      GET    /api/v1/campaigns/public/          - list all public (emergency) campaigns
      POST   /api/v1/campaigns/                 - create a new campaign (admin only)
      GET    /api/v1/campaigns/{id}/            - retrieve campaign detail
      PATCH  /api/v1/campaigns/{id}/            - update campaign (admin only)
      POST   /api/v1/campaigns/{id}/contribute/ - contribute from wallet balance
      POST   /api/v1/campaigns/{id}/disburse/   - disburse funds to beneficiary (admin)
      POST   /api/v1/campaigns/{id}/close/      - close campaign (admin)
    """
    queryset = FundCampaign.objects.all()
    serializer_class = FundCampaignSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = FundCampaign.objects.all()
        group_id = self.request.query_params.get('group')
        organisation_id = self.request.query_params.get('organisation')
        if group_id:
            queryset = queryset.filter(group_id=group_id)
        elif organisation_id:
            queryset = queryset.filter(organisation_id=organisation_id)
        elif self.action == 'list':
            # Default: campaigns for the user's active group
            active_mem = GroupMembership.objects.filter(
                member=self.request.user.profile, is_active=True
            ).first()
            if active_mem:
                queryset = queryset.filter(group=active_mem.group)
            else:
                queryset = queryset.none()
        return queryset.order_by('-created_at')

    def perform_create(self, serializer):
        group = serializer.validated_data.get('group')
        organisation = serializer.validated_data.get('organisation')
        
        if group:
            if not group.is_admin(self.request.user):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Only group admins can create fund campaigns.")
        elif organisation:
            if not organisation.is_admin(self.request.user):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("Only organisation admins can create fund campaigns.")
        else:
            from rest_framework.exceptions import ValidationError
            raise ValidationError("A campaign must be linked to either a Group or an Organisation.")

        # Emergency campaigns are always public.
        # Custom campaigns on a verified organisation are also public (visible in Fundraisers tab).
        campaign_type = serializer.validated_data.get('campaign_type', 'custom')
        is_public = (campaign_type == 'emergency')
        if campaign_type == 'custom' and organisation and organisation.is_verified:
            is_public = True
        
        campaign = serializer.save(created_by=self.request.user.profile, is_public=is_public)
        
        # If group-linked and preference is active, notify members
        if campaign.group and campaign.group.notify_on_campaign_created:
            for active_mem in campaign.group.groupmembership_set.filter(status='active').exclude(member=self.request.user.profile):
                send_push_notification(
                    user=active_mem.member.user,
                    title="New Fund Campaign Launched",
                    message=f"A new campaign '{campaign.title}' has been launched in {campaign.group.name}.",
                    notification_type="campaign_created",
                    data={'group_id': campaign.group.id, 'campaign_id': campaign.id}
                )

    # ── Public fundraisers list (no auth filter) ─────────────────────────────
    @action(detail=False, methods=['get'], permission_classes=[permissions.AllowAny])
    def public(self, request):
        """
        Returns all active public campaigns visible to any user.
        Includes:
          - Emergency campaigns from verified organisations
          - Custom campaigns from verified organisations
          - Emergency/custom campaigns from groups (group-linked)
        """
        from django.db.models import Q
        campaigns = FundCampaign.objects.filter(
            is_public=True,
            contributions_open=True,
        ).filter(
            # Either linked to a verified org, or linked to a group (no verification required for groups)
            Q(organisation__is_verified=True) | Q(group__isnull=False, organisation__isnull=True)
        ).select_related(
            'organisation', 'group', 'beneficiary', 'created_by'
        ).order_by('-created_at')
        serializer = self.get_serializer(campaigns, many=True)
        return Response(serializer.data)

    # ── Contribute from wallet ────────────────────────────────────────────────
    @action(detail=True, methods=['post'])
    def contribute(self, request, pk=None):
        """Deduct from user wallet and log a CampaignContribution."""
        campaign = self.get_object()
        if not campaign.contributions_open:
            return Response({'error': 'This campaign is no longer accepting contributions.'}, status=status.HTTP_400_BAD_REQUEST)

        if campaign.group and not campaign.is_public:
            if not campaign.group.is_member(request.user):
                return Response({'error': 'You must be an active member of this community to contribute to this campaign.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            from decimal import Decimal
            amount = Decimal(str(request.data.get('amount', 0)))
            if amount <= 0:
                raise ValueError("Amount must be positive")
        except Exception:
            return Response({'error': 'Invalid amount.'}, status=status.HTTP_400_BAD_REQUEST)

        profile = request.user.profile

        from wallet.models import Wallet, Transaction as WalletTransaction
        from django.db import transaction as db_transaction

        wallet, _ = Wallet.objects.get_or_create(
            user=request.user,
            defaults={'external_wallet_id': f"WAAS_{request.user.id}"}
        )

        if wallet.get_balance() < amount:
            return Response({'error': 'Insufficient wallet balance.'}, status=status.HTTP_400_BAD_REQUEST)

        note = request.data.get('note', '')

        with db_transaction.atomic():
            tx = WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='TRANSFER',
                amount=amount,
                status='COMPLETED',
                destination_group=campaign.group,
                destination_organisation=campaign.organisation,
                fund_campaign=campaign,
                waas_reference_id=f"CAMP_{timezone.now().timestamp()}"
            )
            contribution = CampaignContribution.objects.create(
                campaign=campaign,
                group=campaign.group,
                organisation=campaign.organisation,
                contributing_member=profile,
                amount=amount,
                payment_method='wallet',
                transaction=tx,
                note=note,
            )
            wallet.recalculate_balance()

        # Notify campaign admin
        admin_user = campaign.created_by.user if campaign.created_by else (campaign.group.creator if campaign.group else campaign.organisation.creator)
        notification_data = {'campaign_id': campaign.id}
        if campaign.group:
            notification_data['group_id'] = campaign.group.id
        elif campaign.organisation:
            notification_data['organisation_id'] = campaign.organisation.id

        send_push_notification(
            user=admin_user,
            title=f"New Contribution to {campaign.title}",
            message=f"{profile.full_name} contributed R{amount} to '{campaign.title}'.",
            notification_type="campaign_contribution",
            data=notification_data
        )

        return Response({
            'status': 'success',
            'amount': str(amount),
            'total_raised': float(campaign.get_total_raised()),
            'contributor_count': campaign.get_contributor_count(),
        }, status=status.HTTP_201_CREATED)

    # ── Campaign Ledger & Audit Trail ──────────────────────────────────────────
    @action(detail=True, methods=['get'])
    def ledger(self, request, pk=None):
        """Retrieve full financial ledger for this specific campaign."""
        campaign = self.get_object()
        contributions = CampaignContribution.objects.filter(campaign=campaign).select_related('contributing_member__user')
        from wallet.models import Transaction as WalletTransaction
        withdrawals = WalletTransaction.objects.filter(fund_campaign=campaign, transaction_type='PAYOUT_RECEIVED').select_related('wallet__user')

        contributions_data = []
        for c in contributions:
            contributions_data.append({
                'id': f"contrib-{c.id}",
                'type': 'contribution',
                'amount': float(c.amount),
                'contributor_name': c.contributing_member.full_name if c.contributing_member else 'Anonymous',
                'contributor_avatar': c.contributing_member.profile_picture.url if c.contributing_member and c.contributing_member.profile_picture else None,
                'payment_method': c.payment_method,
                'date': c.contribution_date.isoformat() if c.contribution_date else None,
                'note': c.note or '',
                'timestamp': c.contribution_date.isoformat() if c.contribution_date else None,
            })

        withdrawals_data = []
        for w in withdrawals:
            recipient_profile = getattr(w.wallet.user, 'profile', None) if hasattr(w.wallet.user, 'profile') else None
            withdrawals_data.append({
                'id': f"withdraw-{w.id}",
                'type': 'withdrawal',
                'amount': float(w.amount),
                'recipient_name': recipient_profile.full_name if recipient_profile else (w.wallet.user.username if hasattr(w.wallet.user, 'username') else str(w.wallet.user)),
                'status': w.status,
                'date': w.timestamp.strftime('%Y-%m-%d') if w.timestamp else None,
                'timestamp': w.timestamp.isoformat() if w.timestamp else None,
                # Use the human-readable note field; fall back to a generic label
                'note': w.note or 'Campaign disbursement',
            })

        # Combine timeline sorted by timestamp descending
        timeline = sorted(contributions_data + withdrawals_data, key=lambda x: x.get('timestamp') or '', reverse=True)

        return Response({
            'campaign_id': campaign.id,
            'title': campaign.title,
            'campaign_type': campaign.campaign_type,
            'total_raised': float(campaign.get_total_raised()),
            'total_disbursed': float(campaign.get_total_disbursed()),
            'available_balance': float(campaign.get_balance()),
            'contributor_count': campaign.get_contributor_count(),
            'contributions': contributions_data,
            'withdrawals': withdrawals_data,
            'timeline': timeline,
        })

    # ── Disburse/Withdraw funds (partial or full) ──────────────────────────────
    @action(detail=True, methods=['post'])
    def disburse(self, request, pk=None):
        """Transfer funds from the campaign balance (admin only). Supports partial withdrawals.
        If the group requires multi-admin approval (min_disbursement_approvals > 1),
        a GroupWalletTransferRequest is created instead of executing immediately.
        """
        campaign = self.get_object()
        is_admin = campaign.group.is_admin(request.user) if campaign.group else campaign.organisation.is_admin(request.user)
        if not is_admin:
            return Response({'error': 'Only admins can withdraw/disburse campaign funds.'}, status=status.HTTP_403_FORBIDDEN)

        available_balance = campaign.get_balance()
        if available_balance <= 0:
            return Response({'error': 'No funds available for withdrawal.'}, status=status.HTTP_400_BAD_REQUEST)

        # Handle amount
        req_amount = request.data.get('amount')
        if req_amount is not None:
            try:
                from decimal import Decimal
                withdraw_amount = Decimal(str(req_amount))
                if withdraw_amount <= 0:
                    raise ValueError()
                if withdraw_amount > available_balance:
                    return Response({'error': f'Requested amount R{withdraw_amount} exceeds available campaign balance R{available_balance}.'}, status=status.HTTP_400_BAD_REQUEST)
            except Exception:
                return Response({'error': 'Invalid withdrawal amount.'}, status=status.HTTP_400_BAD_REQUEST)
        else:
            withdraw_amount = available_balance

        # Handle beneficiary / recipient
        beneficiary_id = request.data.get('beneficiary_id')
        recipient_profile = None
        if beneficiary_id:
            from user.models import Profile
            recipient_profile = Profile.objects.filter(id=beneficiary_id).first()
        if not recipient_profile:
            recipient_profile = campaign.beneficiary or request.user.profile

        note = request.data.get('note', '')

        # ── Multi-admin approval path (group only) ──────────────────────────────
        if campaign.group and (campaign.group.min_disbursement_approvals > 1 or campaign.group.get_admin_count() > 1):
            from wallet.models import GroupWalletTransferRequest
            from wallet.serializers import GroupWalletTransferRequestSerializer

            transfer_request = GroupWalletTransferRequest.objects.create(
                group=campaign.group,
                requested_by=request.user,
                recipient_profile=recipient_profile,
                amount=withdraw_amount,
                fund_campaign=campaign,
                note=note or f"Disbursement from campaign: {campaign.title}",
            )
            # Requester counts as first approver
            transfer_request.approvals.add(request.user)
            transfer_request.save()

            # Auto-execute if threshold already met (e.g. group has exactly 1 admin and changed setting back)
            if transfer_request.can_execute():
                try:
                    transfer_request.execute()
                except Exception as exc:
                    return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
                serializer = GroupWalletTransferRequestSerializer(transfer_request, context={'request': request})
                return Response({'status': 'executed', 'request': serializer.data})

            serializer = GroupWalletTransferRequestSerializer(transfer_request, context={'request': request})
            return Response({
                'status': 'pending_approval',
                'approvals_given': transfer_request.approvals.count(),
                'approvals_needed': transfer_request.required_approvals,
                'request': serializer.data,
            }, status=status.HTTP_202_ACCEPTED)

        # ── Direct disbursement path (org campaigns or single-admin groups) ───────
        from wallet.models import Wallet, Transaction as WalletTransaction
        from django.db import transaction as db_transaction

        recipient_wallet, _ = Wallet.objects.get_or_create(
            user=recipient_profile.user,
            defaults={'external_wallet_id': f"WAAS_{recipient_profile.user.id}"}
        )

        with db_transaction.atomic():
            human_note = note if note else f"Disbursement from campaign: {campaign.title}"
            tx = WalletTransaction.objects.create(
                wallet=recipient_wallet,
                transaction_type='PAYOUT_RECEIVED',
                amount=withdraw_amount,
                status='COMPLETED',
                destination_group=campaign.group,
                destination_organisation=campaign.organisation,
                fund_campaign=campaign,
                note=human_note,
                waas_reference_id=f"CAMP_{timezone.now().timestamp()}"
            )
            recipient_wallet.recalculate_balance()

            # Check remaining balance
            remaining_balance = campaign.get_balance()
            close_campaign = request.data.get('close_campaign', False)
            if remaining_balance == 0 or close_campaign:
                campaign.funds_disbursed = True
                campaign.save()

        send_push_notification(
            user=recipient_profile.user,
            title="Campaign Funds Received",
            message=f"You received R{withdraw_amount} from the '{campaign.title}' campaign.",
            notification_type="campaign_disbursed",
            data={'amount': str(withdraw_amount), 'campaign_id': campaign.id}
        )

        # If linked to a group and preference is active, notify other members
        if campaign.group and campaign.group.notify_on_wallet_transfer:
            for active_mem in campaign.group.groupmembership_set.filter(status='active').exclude(member=recipient_profile):
                send_push_notification(
                    user=active_mem.member.user,
                    title="Group Wallet Disbursement",
                    message=f"R {withdraw_amount} has been disbursed from campaign '{campaign.title}' to {recipient_profile.full_name}.",
                    notification_type="campaign_disbursed_group",
                    data={'group_id': campaign.group.id}
                )

        return Response({
            'status': 'success',
            'amount_disbursed': str(withdraw_amount),
            'remaining_balance': str(campaign.get_balance()),
            'beneficiary': recipient_profile.full_name,
            'funds_disbursed': campaign.funds_disbursed,
        })

    # ── Close campaign ────────────────────────────────────────────────────────
    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        """Close the campaign to new contributions (admin only)."""
        campaign = self.get_object()
        is_admin = campaign.group.is_admin(request.user) if campaign.group else campaign.organisation.is_admin(request.user)
        if not is_admin:
            return Response({'error': 'Only admins can close a campaign.'}, status=status.HTTP_403_FORBIDDEN)
        campaign.close()
        return Response({'status': 'closed', 'contributions_open': False})
