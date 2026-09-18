import logging
import random
import uuid
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from chema.models import Group
from condolence.models import Contribution, Deceased
from user.models import CustomUser, Profile
from user.notifications import send_push_notification
from wallet.models import (
    GroupSMSCreditBalance, GroupSubscription, GroupWalletTransferRequest,
    MicroInsurancePolicy, PlatformFeeConfig, PlatformFeeLedger, SavedCard,
    ServiceVendor, SMSCreditPackage, SMSCreditPurchase, Transaction,
    UserSubscription, Wallet
)
from wallet.serializers import (
    GroupSMSCreditBalanceSerializer, GroupSubscriptionSerializer,
    GroupWalletTransferRequestSerializer, MicroInsurancePolicySerializer,
    PlatformFeeConfigSerializer, SavedCardSerializer, ServiceVendorSerializer,
    SMSCreditPackageSerializer, SMSCreditPurchaseSerializer,
    TransactionSerializer, UserSubscriptionSerializer, WalletSerializer
)
from .auth import verify_user_pin
from .base import StandardPagination

logger = logging.getLogger(__name__)
User = get_user_model()

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
        PlatformFeeLedger.record_fee(
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
        total_vat = PlatformFeeLedger.objects.aggregate(total=Sum('vat_amount'))['total'] or 0.00
        total_net_revenue = PlatformFeeLedger.objects.aggregate(total=Sum('net_fee_amount'))['total'] or 0.00
        
        breakdown_query = PlatformFeeLedger.objects.values('fee_type').annotate(
            total_amount=Sum('fee_amount'),
            vat_amount=Sum('vat_amount'),
            net_amount=Sum('net_fee_amount'),
            transaction_count=Count('id')
        )
        
        breakdown = {}
        for item in breakdown_query:
            breakdown[item['fee_type']] = {
                'total_amount': float(item['total_amount']),
                'vat_amount': float(item['vat_amount'] or 0.0),
                'net_amount': float(item['net_amount'] or 0.0),
                'count': item['transaction_count']
            }
            
        recent_fee_txs = TransactionSerializer(
            treasury_wallet.transactions.order_by('-timestamp')[:20],
            many=True,
            context={'request': request}
        ).data

        config = PlatformFeeConfig.get_config()

        return Response({
            'treasury_wallet_id': treasury_wallet.id,
            'external_wallet_id': treasury_wallet.external_wallet_id,
            'treasury_balance': float(treasury_balance),
            'total_fees_collected': float(total_fees),
            'total_vat_liability': float(total_vat),
            'total_net_platform_revenue': float(total_net_revenue),
            'is_vat_registered': config.is_vat_registered,
            'vat_percentage': float(config.vat_percentage),
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
                    
                quote_vat = Decimal('0.00')
                if config.is_vat_registered and fee > 0:
                    if config.vat_pricing_mode == 'INCLUSIVE':
                        quote_vat = (fee * config.vat_percentage / (Decimal('100.00') + config.vat_percentage)).quantize(Decimal('0.01'))
                    else:
                        quote_vat = (fee * (config.vat_percentage / Decimal('100.00'))).quantize(Decimal('0.01'))

                quote = {
                    'gross_amount': str(amt),
                    'fee_amount': str(fee),
                    'net_amount': str(net),
                    'vat_amount': str(quote_vat),
                    'is_vat_registered': config.is_vat_registered,
                    'vat_rate': str(config.vat_percentage if config.is_vat_registered else Decimal('0.00'))
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
            
            PlatformFeeLedger.record_fee(
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

            PlatformFeeLedger.record_fee(
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

            PlatformFeeLedger.record_fee(
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
                PlatformFeeLedger.record_fee(
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

        # Idempotency check: prevent duplicate submissions
        idempotency_key = request.data.get('idempotency_key') or request.headers.get('X-Idempotency-Key')
        if idempotency_key:
            existing_txn = Transaction.objects.filter(idempotency_key=idempotency_key).first()
            if existing_txn:
                recipient_info = getattr(getattr(recipient_user, 'profile', None), 'email', None) or recipient_user.phone or str(recipient_user.id)
                return Response({
                    'status': 'success',
                    'balance': sender_wallet.get_balance(),
                    'transaction': TransactionSerializer(existing_txn).data,
                    'recipient': recipient_info,
                    'idempotent_replay': True
                })

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
                idempotency_key=idempotency_key,
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


