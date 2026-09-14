from rest_framework import serializers
from .models import (
    Wallet, Transaction, GroupWalletTransferRequest,
    PlatformFeeConfig, PlatformFeeLedger,
    SMSCreditPackage, GroupSMSCreditBalance, SMSCreditPurchase,
    GroupSubscription, UserSubscription, ServiceVendor, VendorBooking,
    MicroInsurancePolicy, InsurancePolicyEnrollment, SavedCard
)
from chema.serializers import GroupSerializer
from user.serializers import ProfileSerializer

class GroupWalletTransferRequestSerializer(serializers.ModelSerializer):
    group_detail = GroupSerializer(source='group', read_only=True)
    requested_by_detail = serializers.SerializerMethodField()
    recipient_profile_detail = ProfileSerializer(source='recipient_profile', read_only=True)
    # alias used by older frontend code
    recipient_detail = ProfileSerializer(source='recipient_profile', read_only=True)
    approvals_count = serializers.SerializerMethodField()
    approvals_needed = serializers.SerializerMethodField()
    approver_names = serializers.SerializerMethodField()
    current_user_has_approved = serializers.SerializerMethodField()
    can_execute = serializers.SerializerMethodField()

    class Meta:
        model = GroupWalletTransferRequest
        fields = [
            'id', 'group', 'group_detail', 'requested_by', 'requested_by_detail',
            'recipient_profile', 'recipient_profile_detail', 'recipient_detail',
            'amount', 'status', 'note',
            'approvals_count', 'approvals_needed', 'approver_names',
            'current_user_has_approved', 'can_execute',
            'deceased_contribution', 'fund_campaign',
            'created_at', 'updated_at', 'executed_at',
        ]
        read_only_fields = [
            'status', 'approvals_count', 'approvals_needed', 'approver_names',
            'current_user_has_approved', 'created_at', 'updated_at', 'executed_at',
        ]

    def get_requested_by_detail(self, obj):
        if obj.requested_by:
            profile = getattr(obj.requested_by, 'profile', None)
            if profile:
                return {
                    'id': profile.id,
                    'full_name': profile.full_name,
                    'email': profile.email,
                }
        return None

    def get_approvals_count(self, obj):
        return obj.approvals.count()

    def get_approvals_needed(self, obj):
        return obj.required_approvals

    def get_approver_names(self, obj):
        names = []
        for user in obj.approvals.all().select_related('profile'):
            profile = getattr(user, 'profile', None)
            names.append(profile.full_name if profile else user.email)
        return names

    def get_current_user_has_approved(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return obj.approvals.filter(id=request.user.id).exists()
        return False

    def get_can_execute(self, obj):
        return obj.can_execute()


class TransactionSerializer(serializers.ModelSerializer):
    destination_group_detail = GroupSerializer(source='destination_group', read_only=True)
    recipient_wallet_detail = serializers.SerializerMethodField()
    sender_wallet_detail = serializers.SerializerMethodField()
    wallet_detail = serializers.SerializerMethodField()
    fund_campaign_detail = serializers.SerializerMethodField()
    deceased_contribution_detail = serializers.SerializerMethodField()
    # Computed human-readable fields
    description = serializers.SerializerMethodField()
    from_label = serializers.SerializerMethodField()
    to_label = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            'id', 'wallet', 'transaction_type', 'amount', 'fee_amount', 'net_amount', 'status', 'note',
            'withdrawal_channel', 'withdrawal_metadata',
            'destination_group', 'destination_group_detail', 
            'recipient_wallet', 'recipient_wallet_detail',
            'sender_wallet', 'sender_wallet_detail',
            'wallet_detail',
            'fund_campaign', 'fund_campaign_detail',
            'deceased_contribution', 'deceased_contribution_detail',
            'voucher_reference', 'waas_reference_id', 'timestamp',
            # Computed
            'description', 'from_label', 'to_label',
        ]
    
    def get_withdrawal_metadata(self, obj):
        from wallet.encryption import mask_metadata
        return mask_metadata(obj.withdrawal_metadata)

    def _profile_mini(self, user):
        """Return a compact name dict for a user."""
        if not user:
            return None
        profile = getattr(user, 'profile', None)
        full_name = getattr(profile, 'full_name', None) or str(user)
        return {'user_id': user.id, 'full_name': full_name}

    def get_wallet_detail(self, obj):
        return self._profile_mini(obj.wallet.user)
    
    def get_recipient_wallet_detail(self, obj):
        if obj.recipient_wallet:
            return self._profile_mini(obj.recipient_wallet.user)
        return None

    def get_sender_wallet_detail(self, obj):
        if obj.sender_wallet:
            return self._profile_mini(obj.sender_wallet.user)
        return None

    def get_fund_campaign_detail(self, obj):
        if obj.fund_campaign:
            return {
                'id': obj.fund_campaign.id,
                'title': obj.fund_campaign.title,
                'campaign_type': obj.fund_campaign.campaign_type,
            }
        return None

    def get_deceased_contribution_detail(self, obj):
        if obj.deceased_contribution:
            dec = obj.deceased_contribution
            full_name = None
            try:
                full_name = dec.deceased.full_name
            except Exception:
                pass
            return {
                'id': dec.id,
                'full_name': full_name or str(dec),
                'group': dec.group.name if dec.group else None,
            }
        return None

    def get_description(self, obj):
        """Single human-readable label for this transaction."""
        t = obj.transaction_type
        if t == 'CONTRIBUTION':
            target = None
            if obj.fund_campaign:
                parent = ""
                if obj.fund_campaign.group:
                    parent = obj.fund_campaign.group.name
                elif obj.fund_campaign.organisation:
                    parent = obj.fund_campaign.organisation.name
                if parent:
                    target = f"{parent} - {obj.fund_campaign.title}"
                else:
                    target = obj.fund_campaign.title
            elif obj.deceased_contribution:
                try:
                    target = obj.deceased_contribution.deceased.full_name
                except Exception:
                    pass
            return f"Contribution to {target}" if target else "Contribution"
        if t == 'TRANSFER':
            target = None
            if obj.fund_campaign:
                parent = ""
                if obj.fund_campaign.group:
                    parent = obj.fund_campaign.group.name
                elif obj.fund_campaign.organisation:
                    parent = obj.fund_campaign.organisation.name
                if parent:
                    target = f"{parent} - {obj.fund_campaign.title}"
                else:
                    target = obj.fund_campaign.title
            elif obj.deceased_contribution:
                try:
                    target = obj.deceased_contribution.deceased.full_name
                except Exception:
                    pass
            elif obj.destination_group:
                target = obj.destination_group.name
            return f"Contribution to {target}" if target else "Fund Contribution"
        if t == 'P2P_SENT':
            name = None
            if obj.recipient_wallet:
                name = self._profile_mini(obj.recipient_wallet.user)['full_name']
            return f"Sent to {name}" if name else "Peer-to-Peer Transfer Sent"
        if t == 'P2P_RECEIVED':
            name = None
            if obj.sender_wallet:
                name = self._profile_mini(obj.sender_wallet.user)['full_name']
            return f"Received from {name}" if name else "Peer-to-Peer Transfer Received"
        if t == 'TOP_UP':
            return "Wallet Top-Up"
        if t == 'WITHDRAWAL':
            ch = (obj.withdrawal_channel or '').replace('_', ' ').title()
            return f"Withdrawal via {ch}" if ch else "Wallet Withdrawal"
        if t == 'PAYOUT_RECEIVED':
            src = None
            if obj.fund_campaign:
                parent = ""
                if obj.fund_campaign.group:
                    parent = obj.fund_campaign.group.name
                elif obj.fund_campaign.organisation:
                    parent = obj.fund_campaign.organisation.name
                if parent:
                    src = f"{parent} - {obj.fund_campaign.title}"
                else:
                    src = obj.fund_campaign.title
            elif obj.destination_group:
                src = obj.destination_group.name
            return f"Payout from {src}" if src else "Payout Received"
        return (t or 'Transaction').replace('_', ' ').title()

    def get_from_label(self, obj):
        """Who sent the money."""
        t = obj.transaction_type
        if t in ('P2P_SENT', 'TRANSFER', 'CONTRIBUTION', 'WITHDRAWAL'):
            return self._profile_mini(obj.wallet.user)
        if t == 'P2P_RECEIVED':
            if obj.sender_wallet:
                return self._profile_mini(obj.sender_wallet.user)
        if t == 'PAYOUT_RECEIVED':
            if obj.fund_campaign:
                return {'full_name': f"Campaign: {obj.fund_campaign.title}"}
            if obj.destination_group:
                return {'full_name': f"Group: {obj.destination_group.name}"}
        return None

    def get_to_label(self, obj):
        """Who received / where the money went."""
        t = obj.transaction_type
        if t == 'P2P_SENT':
            if obj.recipient_wallet:
                return self._profile_mini(obj.recipient_wallet.user)
        if t in ('P2P_RECEIVED', 'PAYOUT_RECEIVED', 'TOP_UP'):
            return self._profile_mini(obj.wallet.user)
        if t in ('TRANSFER', 'CONTRIBUTION'):
            if obj.fund_campaign:
                return {'full_name': f"Campaign: {obj.fund_campaign.title}"}
            if obj.deceased_contribution:
                try:
                    fn = obj.deceased_contribution.deceased.full_name
                    return {'full_name': f"Bereavement: {fn}"}
                except Exception:
                    pass
            if obj.destination_group:
                return {'full_name': f"Group: {obj.destination_group.name}"}
        if t == 'WITHDRAWAL':
            ch = (obj.withdrawal_channel or '').replace('_', ' ').title()
            return {'full_name': ch or 'External Account'}
        return None


class WalletSerializer(serializers.ModelSerializer):
    balance = serializers.DecimalField(source='get_balance', max_digits=10, decimal_places=2, read_only=True)
    recent_transactions = serializers.SerializerMethodField()

    class Meta:
        model = Wallet
        fields = ['id', 'user', 'external_wallet_id', 'balance', 'is_platform_treasury', 'recent_transactions', 'created_at']

    def get_recent_transactions(self, obj):
        transactions = obj.transactions.all().order_by('-timestamp')[:5]
        return TransactionSerializer(transactions, many=True).data


class PlatformFeeConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformFeeConfig
        fields = [
            'is_fees_enabled',
            'topup_percentage_fee', 'topup_flat_fee',
            'withdrawal_percentage_fee', 'withdrawal_flat_fee',
            'group_transfer_percentage_fee', 'group_transfer_flat_fee',
            # Phase 2
            'is_saas_subscriptions_enabled',
            'group_pro_monthly_price', 'komunity_plus_monthly_price',
            # Phase 3
            'is_vendor_marketplace_enabled',
            'vendor_commission_percentage',
            'updated_at'
        ]


class SMSCreditPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = SMSCreditPackage
        fields = ['id', 'name', 'credits_count', 'price', 'is_active', 'created_at']


class GroupSMSCreditBalanceSerializer(serializers.ModelSerializer):
    group_id = serializers.IntegerField(source='group.id', read_only=True)
    group_name = serializers.CharField(source='group.name', read_only=True)

    class Meta:
        model = GroupSMSCreditBalance
        fields = ['group_id', 'group_name', 'balance', 'updated_at']


class SMSCreditPurchaseSerializer(serializers.ModelSerializer):
    package_detail = SMSCreditPackageSerializer(source='package', read_only=True)

    class Meta:
        model = SMSCreditPurchase
        fields = ['id', 'group', 'package', 'package_detail', 'credits_added', 'amount_paid', 'created_at']


class GroupSubscriptionSerializer(serializers.ModelSerializer):
    tier_display = serializers.CharField(source='get_tier_display', read_only=True)

    class Meta:
        model = GroupSubscription
        fields = ['id', 'group', 'tier', 'tier_display', 'is_active', 'monthly_price', 'expires_at', 'created_at']


class UserSubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserSubscription
        fields = ['id', 'user', 'is_active', 'badge_label', 'expires_at', 'created_at']


class ServiceVendorSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = ServiceVendor
        fields = ['id', 'name', 'category', 'category_display', 'description', 'contact_phone', 'contact_email', 'rating', 'is_verified', 'is_active']


class VendorBookingSerializer(serializers.ModelSerializer):
    vendor_detail = ServiceVendorSerializer(source='vendor', read_only=True)

    class Meta:
        model = VendorBooking
        fields = ['id', 'vendor', 'vendor_detail', 'group', 'user', 'service_description', 'booking_amount', 'commission_amount', 'status', 'created_at']


class MicroInsurancePolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = MicroInsurancePolicy
        fields = ['id', 'provider_name', 'policy_name', 'cover_amount', 'monthly_premium', 'is_active']


class InsurancePolicyEnrollmentSerializer(serializers.ModelSerializer):
    policy_detail = MicroInsurancePolicySerializer(source='policy', read_only=True)

    class Meta:
        model = InsurancePolicyEnrollment
        fields = ['id', 'policy', 'policy_detail', 'group', 'enrolled_by', 'enrolled_members_count', 'is_active', 'created_at']


class SavedCardSerializer(serializers.ModelSerializer):
    """
    Serializer for safe tokenized card display.
    Excludes sensitive customer_id and payment_method_id for PCI compliance.
    """
    class Meta:
        model = SavedCard
        fields = [
            'id', 'card_brand', 'last4', 'expiry_month', 'expiry_year',
            'cardholder_name', 'is_default', 'created_at'
        ]
        read_only_fields = ['id', 'card_brand', 'last4', 'expiry_month', 'expiry_year', 'created_at']


