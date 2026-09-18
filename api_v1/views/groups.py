import calendar
import logging
import os
import random
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction as db_transaction
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from chema.models import (
    ContributionCycle, Group, GroupMembership, MemberCyclePayment, Organisation,
    Post, Comment, PostImage, Reply, Dependent
)
from chema.serializers import (
    ContributionCycleSerializer, GroupMembershipSerializer,
    GroupSerializer, MemberCyclePaymentSerializer, OrganisationSerializer,
    PostSerializer
)
from condolence.models import Deceased, FundCampaign
from condolence.serializers import FundCampaignSerializer
from user.models import Profile
from user.notifications import send_push_notification
from user.serializers import ProfileSerializer
from wallet.models import (
    GroupSMSCreditBalance, GroupSubscription, PlatformFeeConfig, SMSCreditPackage,
    Transaction, Wallet, GroupWalletTransferRequest
)
from wallet.serializers import (
    GroupWalletTransferRequestSerializer, TransactionSerializer
)
from .base import StandardPagination

logger = logging.getLogger(__name__)
User = get_user_model()

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

        now = timezone.now()
        month = int(request.data.get('month', now.month))
        year = int(request.data.get('year', now.year))
        target_amount = Decimal(str(request.data.get('target_amount', group.recurring_amount)))

        max_days = calendar.monthrange(year, month)[1]
        due_day = min(group.recurring_due_day or 25, max_days)
        due_date_str = request.data.get('due_date')
        if due_date_str:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        else:
            due_date = date(year, month, due_day)

        title = request.data.get('title')
        if not title:
            month_name = calendar.month_name[month]
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

