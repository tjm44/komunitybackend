from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ProfileViewSet, GroupViewSet, PostViewSet, CommentViewSet, 
    DeceasedViewSet, ContributionViewSet, WalletViewSet, PostImageViewSet,
    TransactionViewSet, UserViewSet, ReplyViewSet, GroupMembershipViewSet,
    DeviceTokenViewSet, NotificationViewSet, password_reset_request, search_api_view,
    EmailAuthTokenView, mobile_callback_view,
    FundCampaignViewSet, OrganisationViewSet,
    RequestOTPView, VerifyOTPView, CheckPhoneStatusView,
    VerifyPINView, SetPINView, VerifyCurrentPINView,
)

router = DefaultRouter()
router.register(r'profiles', ProfileViewSet)
router.register(r'groups', GroupViewSet)
router.register(r'organisations', OrganisationViewSet)
router.register(r'memberships', GroupMembershipViewSet)
router.register(r'posts', PostViewSet)
router.register(r'post-images', PostImageViewSet)
router.register(r'comments', CommentViewSet)
router.register(r'replies', ReplyViewSet)
router.register(r'deceased', DeceasedViewSet)
router.register(r'contributions', ContributionViewSet)
router.register(r'wallets', WalletViewSet, basename='wallet')
router.register(r'transactions', TransactionViewSet, basename='transactions')
router.register(r'users', UserViewSet, basename='users')
router.register(r'device-tokens', DeviceTokenViewSet)
router.register(r'notifications', NotificationViewSet, basename='notifications')
router.register(r'campaigns', FundCampaignViewSet, basename='campaigns')

urlpatterns = [
    path('', include(router.urls)),
    path('auth/', include('dj_rest_auth.urls')),
    path('auth/registration/', include('dj_rest_auth.registration.urls')),
    path('auth/check-phone/', CheckPhoneStatusView.as_view(), name='check_phone'),
    path('auth/request-otp/', RequestOTPView.as_view(), name='request_otp'),
    path('auth/verify-otp/', VerifyOTPView.as_view(), name='verify_otp'),
    path('auth/verify-pin/', VerifyPINView.as_view(), name='verify_pin'),
    path('auth/verify-current-pin/', VerifyCurrentPINView.as_view(), name='verify_current_pin'),
    path('auth/set-pin/', SetPINView.as_view(), name='set_pin'),
    path('auth-token/', EmailAuthTokenView.as_view(), name='auth_token'),
    path('password-reset/', password_reset_request, name='api_password_reset'),
    path('search/', search_api_view, name='api_search'),
    path('auth/mobile-callback/', mobile_callback_view, name='mobile_callback'),
]


