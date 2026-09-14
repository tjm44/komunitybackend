from django.conf import settings
from django.db import models, transaction as db_transaction
from django.utils import timezone
from chema.models import Group

class Wallet(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    external_wallet_id = models.CharField(max_length=100, unique=True)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    is_platform_treasury = models.BooleanField(default=False, help_text="Flag indicating whether this is the Platform System Treasury Wallet")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Wallet for {self.user}"

    @classmethod
    def get_treasury_wallet(cls):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        treasury_user, _ = User.objects.get_or_create(
            email='treasury@komunity.app',
            defaults={
                'phone': '+27000000000',
                'is_staff': True,
                'is_active': True,
            }
        )
        wallet, _ = cls.objects.get_or_create(
            user=treasury_user,
            defaults={
                'external_wallet_id': 'PLATFORM_TREASURY_WALLET',
                'is_platform_treasury': True,
            }
        )
        if not wallet.is_platform_treasury:
            wallet.is_platform_treasury = True
            wallet.save(update_fields=['is_platform_treasury'])
        return wallet

    def recalculate_balance(self):
        from decimal import Decimal
        from django.db.models import Sum, F, Q, DecimalField
        from django.db.models.functions import Coalesce
        
        # Calculate Incoming (Top-Ups + Payouts + Received Transfers + Platform Fee Credits)
        incoming_txs = self.transactions.filter(
            transaction_type__in=['TOP_UP', 'PAYOUT_RECEIVED', 'P2P_RECEIVED', 'PLATFORM_FEE_COLLECTED'],
            status='COMPLETED'
        )
        incoming = Decimal('0.00')
        for tx in incoming_txs:
            incoming += (tx.net_amount if tx.net_amount and tx.net_amount > 0 else tx.amount)

        # Calculate Outgoing (Transfers + Withdrawals + Sent Transfers) using gross amount
        outgoing_txs = self.transactions.filter(
            transaction_type__in=['TRANSFER', 'WITHDRAWAL', 'P2P_SENT', 'SMS_PACKAGE_PURCHASE'],
            status='COMPLETED'
        )
        outgoing = Decimal('0.00')
        for tx in outgoing_txs:
            outgoing += tx.amount

        calculated = incoming - outgoing
        if self.balance != calculated:
            self.balance = calculated
            self.save(update_fields=['balance'])
        return calculated

    def get_balance(self):
        return self.balance

class Transaction(models.Model):
    class TransactionType(models.TextChoices):
        TOP_UP = 'TOP_UP', 'Top-Up'
        TRANSFER = 'TRANSFER', 'Transfer to Group'
        WITHDRAWAL = 'WITHDRAWAL', 'Withdrawal'
        PAYOUT_RECEIVED = 'PAYOUT_RECEIVED', 'Payout Received'
        P2P_SENT = 'P2P_SENT', 'Peer-to-Peer Sent'
        P2P_RECEIVED = 'P2P_RECEIVED', 'Peer-to-Peer Received'
        SMS_PACKAGE_PURCHASE = 'SMS_PACKAGE_PURCHASE', 'SMS Package Purchase'
        PLATFORM_FEE_COLLECTED = 'PLATFORM_FEE_COLLECTED', 'Platform Fee Revenue'

    class TransactionStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    class TransactionChannel(models.TextChoices):
        BANK_TRANSFER = 'bank_transfer', 'Bank Transfer'
        MOBILE_MONEY = 'mobile_money', 'Mobile Money'
        VOUCHER = 'voucher', 'Voucher Cash-out'

    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="transactions")
    transaction_type = models.CharField(max_length=35, choices=TransactionType.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Platform fee charged on this transaction")
    net_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Net amount after deducting platform fee")
    status = models.CharField(max_length=20, choices=TransactionStatus.choices, default=TransactionStatus.PENDING)
    withdrawal_channel = models.CharField(max_length=30, choices=TransactionChannel.choices, blank=True, null=True)
    withdrawal_metadata = models.JSONField(blank=True, null=True)

    # Where the money went (if applicable)
    destination_group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, blank=True)
    destination_organisation = models.ForeignKey('chema.Organisation', on_delete=models.SET_NULL, null=True, blank=True)
    recipient_wallet = models.ForeignKey(Wallet, on_delete=models.SET_NULL, null=True, blank=True, related_name="incoming_transfers")
    sender_wallet = models.ForeignKey(Wallet, on_delete=models.SET_NULL, null=True, blank=True, related_name="outgoing_p2p_transfers")
    # Legacy: links to a Deceased record (bereavement condolence system)
    deceased_contribution = models.ForeignKey(
        'condolence.Deceased', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='wallet_contributions'
    )
    # New: links to a generic FundCampaign (excess, emergency, custom)
    fund_campaign = models.ForeignKey(
        'condolence.FundCampaign', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='campaign_transactions',
        help_text="Links this transaction to a generic FundCampaign"
    )

    # IDs from the external systems for reconciliation
    voucher_reference = models.CharField(max_length=100, blank=True, null=True)
    waas_reference_id = models.CharField(max_length=100, blank=True, null=True)  # The ID from your WaaS provider
    idempotency_key = models.CharField(max_length=100, unique=True, null=True, blank=True)
    note = models.CharField(max_length=512, blank=True, null=True, help_text="Human-readable purpose/description of this transaction")
    
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.transaction_type} of {self.amount} for {self.wallet.user} - {self.status}"


class GroupWalletTransferRequest(models.Model):
    STATUS_PENDING = 'PENDING'
    STATUS_EXECUTED = 'EXECUTED'
    STATUS_REJECTED = 'REJECTED'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_EXECUTED, 'Executed'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    note = models.TextField(null=True, blank=True, help_text="Optional context/reason for this transfer request")

    @property
    def required_approvals(self):
        """Dynamic approval threshold based on group governance settings and active admin count."""
        admin_count = self.group.get_admin_count()
        return max(self.group.min_disbursement_approvals, admin_count)

    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='wallet_transfer_requests')
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='group_wallet_transfer_requests'
    )
    recipient_profile = models.ForeignKey(
        'user.Profile',
        on_delete=models.CASCADE,
        related_name='group_wallet_transfer_requests'
    )
    recipient_wallet = models.ForeignKey(
        Wallet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='received_group_transfer_requests'
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    deceased_contribution = models.ForeignKey(
        'condolence.Deceased', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='wallet_transfer_requests'
    )
    fund_campaign = models.ForeignKey(
        'condolence.FundCampaign', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='campaign_transfer_requests',
        help_text="Links this transfer request to a generic FundCampaign"
    )
    approvals = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='approved_group_transfer_requests',
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    executed_transaction = models.ForeignKey(
        Transaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='executed_group_transfer_requests'
    )

    def __str__(self):
        return f"{self.group.name} transfer request {self.amount} to {self.recipient_profile.full_name} ({self.status})"

    def can_execute(self):
        return (
            self.status == self.STATUS_PENDING and
            self.approvals.count() >= self.required_approvals and
            self.group.get_balance() >= self.amount
        )

    def execute(self):
        if self.status != self.STATUS_PENDING:
            raise ValueError('Only pending transfer requests can be executed.')

        if self.approvals.count() < self.required_approvals:
            raise ValueError('Not enough approvals to execute this transfer request.')

        if self.group.get_balance() < self.amount:
            raise ValueError('Insufficient group wallet balance to execute the transfer.')

        with db_transaction.atomic():
            recipient_wallet, _ = Wallet.objects.get_or_create(
                user=self.recipient_profile.user,
                defaults={'external_wallet_id': f"WAAS_{self.recipient_profile.user.id}"}
            )

            from decimal import Decimal
            config = PlatformFeeConfig.get_config()
            gross = Decimal(str(self.amount))
            fee = Decimal('0.00')
            net = gross

            if config.is_fees_enabled and gross > 0:
                pct = config.group_transfer_percentage_fee / Decimal('100.00')
                flat = config.group_transfer_flat_fee
                fee = min((gross * pct) + flat, gross).quantize(Decimal('0.01'))
                net = (gross - fee).quantize(Decimal('0.01'))

            transaction = Transaction.objects.create(
                wallet=recipient_wallet,
                transaction_type='PAYOUT_RECEIVED',
                amount=self.amount,
                fee_amount=fee,
                net_amount=net,
                status='COMPLETED',
                destination_group=self.group,
                deceased_contribution=self.deceased_contribution,
                fund_campaign=self.fund_campaign,
                waas_reference_id=f"GROUP_TRANSFER_{timezone.now().timestamp()}"
            )
            recipient_wallet.recalculate_balance()

            if fee > 0:
                PlatformFeeLedger.objects.create(
                    transaction=transaction,
                    fee_type='GROUP_TRANSFER_FEE',
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
                    sender_wallet=recipient_wallet,
                    destination_group=self.group,
                    note=f"Group Transfer Fee from Transaction #{transaction.id}"
                )
                treasury_wallet.recalculate_balance()

            self.recipient_wallet = recipient_wallet
            self.executed_transaction = transaction
            self.status = self.STATUS_EXECUTED
            self.executed_at = timezone.now()
            self.save()

        # If notify_on_wallet_transfer preference is active, notify group members
        if self.group.notify_on_wallet_transfer:
            from user.notifications import send_push_notification
            for active_mem in self.group.groupmembership_set.filter(status='active').exclude(member=self.recipient_profile):
                send_push_notification(
                    user=active_mem.member.user,
                    title=f"Funds Disbursed from {self.group.name}",
                    message=f"R {self.amount} has been disbursed from the group wallet to {self.recipient_profile.full_name}.",
                    notification_type="wallet_transfer_executed",
                    data={'group_id': self.group.id}
                )


class PlatformFeeConfig(models.Model):
    """
    Singleton model for live Django Admin configuration of fee rates across top-ups, withdrawals, and transfers.
    """
    is_fees_enabled = models.BooleanField(default=True, help_text="Global toggle to enable or disable platform fee collection")
    
    topup_percentage_fee = models.DecimalField(max_digits=5, decimal_places=2, default=2.50, help_text="Top-up percentage fee (%) e.g. 2.50")
    topup_flat_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, help_text="Flat fee per top-up transaction")

    withdrawal_percentage_fee = models.DecimalField(max_digits=5, decimal_places=2, default=1.50, help_text="Withdrawal percentage fee (%) e.g. 1.50")
    withdrawal_flat_fee = models.DecimalField(max_digits=8, decimal_places=2, default=5.00, help_text="Flat fee per withdrawal transaction e.g. 5.00")

    group_transfer_percentage_fee = models.DecimalField(max_digits=5, decimal_places=2, default=1.00, help_text="Group disbursement percentage fee (%) e.g. 1.00")
    group_transfer_flat_fee = models.DecimalField(max_digits=8, decimal_places=2, default=0.00, help_text="Flat fee per group disbursement")

    # Phase 2 Feature Flags & Pricing (Group SaaS & Komunity Plus)
    is_saas_subscriptions_enabled = models.BooleanField(default=False, help_text="Phase 2 Toggle: Group SaaS subscriptions & Komunity Plus individual badges")
    group_pro_monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=150.00, help_text="Monthly price for Group Pro tier (ZAR)")
    komunity_plus_monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=35.00, help_text="Monthly price for individual Komunity Plus badge (ZAR)")

    # Phase 3 Feature Flags & Pricing (Vendor Marketplace & Micro-Insurance)
    is_vendor_marketplace_enabled = models.BooleanField(default=False, help_text="Phase 3 Toggle: B2B Service Vendor Marketplace & Micro-Insurance products")
    vendor_commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=10.00, help_text="Platform commission % on vendor marketplace bookings")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Platform Fee Configuration"
        verbose_name_plural = "Platform Fee Configuration"

    @classmethod
    def get_config(cls):
        config, _ = cls.objects.get_or_create(id=1)
        return config

    def __str__(self):
        return f"Platform Fee Config (Fees: {self.is_fees_enabled}, SaaS: {self.is_saas_subscriptions_enabled}, Vendors: {self.is_vendor_marketplace_enabled})"


class PlatformFeeLedger(models.Model):
    """
    Audit ledger tracking all platform monetization fee earnings.
    """
    FEE_TYPES = (
        ('TOP_UP_FEE', 'Top-Up Fee'),
        ('WITHDRAWAL_FEE', 'Withdrawal Fee'),
        ('GROUP_TRANSFER_FEE', 'Group Transfer Fee'),
        ('SMS_PACKAGE_FEE', 'SMS Package Purchase Fee'),
        ('GROUP_SAAS_FEE', 'Group SaaS Subscription Fee'),
        ('KOMUNITY_PLUS_FEE', 'Komunity Plus Subscription Fee'),
        ('VENDOR_COMMISSION_FEE', 'Vendor Marketplace Commission'),
        ('INSURANCE_COMMISSION_FEE', 'Micro-Insurance Commission'),
    )

    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name="platform_fees", null=True, blank=True)
    fee_type = models.CharField(max_length=30, choices=FEE_TYPES)
    gross_amount = models.DecimalField(max_digits=12, decimal_places=2)
    fee_amount = models.DecimalField(max_digits=12, decimal_places=2)
    net_amount = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_fee_type_display()}: R{self.fee_amount} on R{self.gross_amount}"


class SMSCreditPackage(models.Model):
    """
    Pay-As-You-Go SMS Notification packages for group admins.
    """
    name = models.CharField(max_length=100)
    credits_count = models.IntegerField(help_text="Number of SMS notification credits in bundle")
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price in local currency")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.credits_count} Credits for R{self.price})"


class GroupSMSCreditBalance(models.Model):
    """
    Current SMS credit balance for a group.
    """
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="sms_credit_balance")
    balance = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.group.name}: {self.balance} SMS credits"


class SMSCreditPurchase(models.Model):
    """
    Record of SMS package purchases by group admins.
    """
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="sms_purchases")
    package = models.ForeignKey(SMSCreditPackage, on_delete=models.SET_NULL, null=True)
    purchased_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    credits_added = models.IntegerField()
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2)
    transaction = models.ForeignKey(Transaction, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.credits_added} credits purchased for {self.group.name} by {self.purchased_by}"


# ==========================================
# PHASE 2 MODELS: SaaS & Subscriptions
# ==========================================

class GroupSubscription(models.Model):
    TIER_CHOICES = (
        ('FREE', 'Free Tier'),
        ('PRO', 'Group Pro'),
        ('ENTERPRISE', 'Enterprise / NGO'),
    )

    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name="subscription")
    tier = models.CharField(max_length=20, choices=TIER_CHOICES, default='FREE')
    is_active = models.BooleanField(default=True)
    monthly_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.group.name} - {self.get_tier_display()} (Active: {self.is_active})"


class UserSubscription(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="plus_subscription")
    is_active = models.BooleanField(default=False)
    badge_label = models.CharField(max_length=50, default="Komunity Plus Member")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user} - Komunity Plus ({'Active' if self.is_active else 'Expired/Inactive'})"


# ==========================================
# PHASE 3 MODELS: B2B Marketplace & Micro-Insurance
# ==========================================

class ServiceVendor(models.Model):
    CATEGORY_CHOICES = (
        ('FUNERAL_PARLOR', 'Funeral Parlor & Undertaker'),
        ('CATERING', 'Catering & Event Hire'),
        ('COUNSELING', 'Mental Health & Counseling'),
        ('LEGAL_AID', 'Legal Aid & Advisory'),
        ('WELLNESS', 'Healthcare & Wellness'),
    )

    name = models.CharField(max_length=150)
    category = models.CharField(max_length=30, choices=CATEGORY_CHOICES)
    description = models.TextField(blank=True, null=True)
    contact_phone = models.CharField(max_length=20)
    contact_email = models.EmailField(blank=True, null=True)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=5.00)
    is_verified = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class VendorBooking(models.Model):
    STATUS_CHOICES = (
        ('PENDING', 'Pending'),
        ('CONFIRMED', 'Confirmed'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    )

    vendor = models.ForeignKey(ServiceVendor, on_delete=models.CASCADE, related_name="bookings")
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    service_description = models.CharField(max_length=255)
    booking_amount = models.DecimalField(max_digits=10, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    transaction = models.ForeignKey(Transaction, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Booking #{self.id} for {self.vendor.name} by {self.user} (R{self.booking_amount})"


class MicroInsurancePolicy(models.Model):
    provider_name = models.CharField(max_length=100, help_text="Partner insurance underwriter")
    policy_name = models.CharField(max_length=150)
    cover_amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Total payout coverage ZAR")
    monthly_premium = models.DecimalField(max_digits=8, decimal_places=2, help_text="Monthly premium per member")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.policy_name} ({self.provider_name}) - Cover R{self.cover_amount} @ R{self.monthly_premium}/mo"


class InsurancePolicyEnrollment(models.Model):
    policy = models.ForeignKey(MicroInsurancePolicy, on_delete=models.CASCADE, related_name="enrollments")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="insurance_policies")
    enrolled_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    enrolled_members_count = models.IntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.group.name} enrolled in {self.policy.policy_name} ({self.enrolled_members_count} members)"


class SavedCard(models.Model):
    """
    Secure tokenized payment card representation for a user.
    Adheres strictly to PCI-DSS: NEVER stores raw card number or CVV.
    Stores gateway token references along with safe display information.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='saved_cards')
    customer_id = models.CharField(max_length=120, help_text="Flutterwave/Gateway customer ID")
    payment_method_id = models.CharField(max_length=120, help_text="Flutterwave/Gateway tokenized payment method ID")
    card_brand = models.CharField(max_length=50, default='Visa', help_text="Card brand e.g. Visa, Mastercard")
    last4 = models.CharField(max_length=4, help_text="Last 4 digits of card")
    expiry_month = models.CharField(max_length=2, help_text="MM")
    expiry_year = models.CharField(max_length=4, help_text="YY or YYYY")
    cardholder_name = models.CharField(max_length=150, blank=True, null=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_default', '-created_at']

    def __str__(self):
        return f"{self.card_brand} •••• {self.last4} ({self.user})"


