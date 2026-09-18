from django.contrib import admin
from .models import (
    Wallet, Transaction, GroupWalletTransferRequest,
    PlatformFeeConfig, PlatformFeeLedger,
    SMSCreditPackage, GroupSMSCreditBalance, SMSCreditPurchase,
    GroupSubscription, UserSubscription, ServiceVendor, VendorBooking,
    MicroInsurancePolicy, InsurancePolicyEnrollment, SavedCard
)

@admin.register(SavedCard)
class SavedCardAdmin(admin.ModelAdmin):
    list_display = ('user', 'card_brand', 'last4', 'expiry_month', 'expiry_year', 'is_default', 'created_at')
    list_filter = ('card_brand', 'is_default', 'created_at')
    search_fields = ('user__phone', 'user__email', 'last4')
    readonly_fields = ('created_at', 'updated_at', 'customer_id', 'payment_method_id')


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'external_wallet_id', 'balance', 'is_platform_treasury', 'created_at')
    list_filter = ('is_platform_treasury', 'created_at')
    search_fields = ('user__phone', 'user__username', 'user__email', 'external_wallet_id')
    readonly_fields = ('created_at', 'balance')
    
    def balance(self, obj):
        return f"R {obj.get_balance()}"

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'transaction_type', 'amount', 'fee_amount', 'net_amount', 'status', 'timestamp')
    list_filter = ('transaction_type', 'status', 'timestamp')
    search_fields = ('wallet__user__phone', 'waas_reference_id', 'voucher_reference')
    date_hierarchy = 'timestamp'
    readonly_fields = ('timestamp',)

@admin.register(GroupWalletTransferRequest)
class GroupWalletTransferRequestAdmin(admin.ModelAdmin):
    list_display = ('group', 'requested_by', 'recipient_profile', 'amount', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('group__name', 'requested_by__phone', 'recipient_profile__first_name')
    readonly_fields = ('created_at', 'updated_at', 'executed_at')

@admin.register(PlatformFeeConfig)
class PlatformFeeConfigAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'is_fees_enabled', 'is_vat_registered', 'vat_percentage',
        'is_saas_subscriptions_enabled', 'group_pro_monthly_price',
        'is_vendor_marketplace_enabled', 'vendor_commission_percentage',
        'is_maintenance_mode', 'updated_at'
    )
    fieldsets = (
        ('Transaction Fees', {
            'fields': (
                'is_fees_enabled',
                ('topup_percentage_fee', 'topup_flat_fee'),
                ('withdrawal_percentage_fee', 'withdrawal_flat_fee'),
                ('group_transfer_percentage_fee', 'group_transfer_flat_fee'),
            )
        }),
        ('SARS & Tax Compliance', {
            'fields': (
                'is_vat_registered',
                ('vat_percentage', 'vat_pricing_mode'),
                ('vat_registration_number', 'sars_tax_number'),
                ('registered_business_name', 'tax_year_end_month'),
                'registered_business_address',
                'vat_threshold_amount',
            )
        }),
        ('Platform Safety & FICA AML Limits', {
            'fields': (
                ('fica_reporting_threshold', 'max_single_payout'),
                'daily_user_transfer_limit',
                ('admin_alert_email', 'admin_alert_phone'),
                'is_maintenance_mode',
            )
        }),
        ('Phase 2: SaaS Subscriptions', {
            'fields': (
                'is_saas_subscriptions_enabled',
                ('group_pro_monthly_price', 'komunity_plus_monthly_price'),
            )
        }),
        ('Phase 3: Vendor Marketplace', {
            'fields': (
                'is_vendor_marketplace_enabled',
                'vendor_commission_percentage',
            )
        }),
    )
    
    def has_add_permission(self, request):
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

@admin.register(PlatformFeeLedger)
class PlatformFeeLedgerAdmin(admin.ModelAdmin):
    list_display = ('fee_type', 'gross_amount', 'fee_amount', 'vat_amount', 'net_fee_amount', 'tax_year', 'created_at')
    list_filter = ('fee_type', 'tax_year', 'created_at')
    search_fields = ('tax_year', 'transaction__waas_reference_id')
    readonly_fields = ('created_at',)

@admin.register(SMSCreditPackage)
class SMSCreditPackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'credits_count', 'price', 'is_active', 'created_at')
    list_filter = ('is_active',)

@admin.register(GroupSMSCreditBalance)
class GroupSMSCreditBalanceAdmin(admin.ModelAdmin):
    list_display = ('group', 'balance', 'updated_at')
    search_fields = ('group__name',)

@admin.register(SMSCreditPurchase)
class SMSCreditPurchaseAdmin(admin.ModelAdmin):
    list_display = ('group', 'package', 'credits_added', 'amount_paid', 'purchased_by', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('group__name', 'purchased_by__email', 'purchased_by__phone')

@admin.register(GroupSubscription)
class GroupSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('group', 'tier', 'is_active', 'monthly_price', 'expires_at')
    list_filter = ('tier', 'is_active')
    search_fields = ('group__name',)

@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('user', 'badge_label', 'is_active', 'expires_at')
    list_filter = ('is_active',)
    search_fields = ('user__email', 'user__phone')

@admin.register(ServiceVendor)
class ServiceVendorAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'rating', 'is_verified', 'is_active')
    list_filter = ('category', 'is_verified', 'is_active')
    search_fields = ('name', 'contact_phone', 'contact_email')

@admin.register(VendorBooking)
class VendorBookingAdmin(admin.ModelAdmin):
    list_display = ('id', 'vendor', 'user', 'booking_amount', 'commission_amount', 'status', 'created_at')
    list_filter = ('status', 'created_at')

@admin.register(MicroInsurancePolicy)
class MicroInsurancePolicyAdmin(admin.ModelAdmin):
    list_display = ('policy_name', 'provider_name', 'cover_amount', 'monthly_premium', 'is_active')
    list_filter = ('is_active',)

@admin.register(InsurancePolicyEnrollment)
class InsurancePolicyEnrollmentAdmin(admin.ModelAdmin):
    list_display = ('group', 'policy', 'enrolled_members_count', 'is_active', 'created_at')
    list_filter = ('is_active',)



