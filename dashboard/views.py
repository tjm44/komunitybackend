"""
Komunity Platform – One-Stop Admin Dashboard Views
===================================================
All views require is_staff. No DRF — pure Django class-based views.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib import messages
from django.db.models import Sum, Count, Q, Avg
from django.db.models.functions import TruncDay
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.http import JsonResponse
import json

# ── Model imports ────────────────────────────────────────────────────────────
from user.models import CustomUser, Profile, Notification, DeviceToken, PhoneOTP
from chema.models import (
    Group, GroupMembership, Organisation,
    ContributionCycle, MemberCyclePayment,
    GroupBereavementProfile, GroupExcessProfile, GroupChurchProfile,
    GroupStokvelProfile, GroupStudentProfile
)
from condolence.models import Deceased, Contribution, FundCampaign, CampaignContribution
from wallet.models import (
    Wallet, Transaction, GroupWalletTransferRequest,
    PlatformFeeConfig, PlatformFeeLedger,
    SMSCreditPackage, GroupSMSCreditBalance, SMSCreditPurchase,
    GroupSubscription, UserSubscription,
    ServiceVendor, VendorBooking,
    MicroInsurancePolicy, InsurancePolicyEnrollment,
)

# ── Decorator shortcut ───────────────────────────────────────────────────────
staff_required = method_decorator(staff_member_required(login_url='/admin/login/'), name='dispatch')


# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def _revenue_chart_data(days=30):
    """Returns daily fee revenue as labels + values lists for Chart.js."""
    since = timezone.now() - timedelta(days=days)
    qs = (
        PlatformFeeLedger.objects
        .filter(created_at__gte=since)
        .annotate(day=TruncDay('created_at'))
        .values('day')
        .annotate(total=Sum('fee_amount'))
        .order_by('day')
    )
    labels, values = [], []
    for row in qs:
        labels.append(row['day'].strftime('%b %d'))
        values.append(float(row['total']))
    return labels, values


def _tx_chart_data(days=30):
    """Returns daily transaction volume (count) for the last N days."""
    since = timezone.now() - timedelta(days=days)
    qs = (
        Transaction.objects
        .filter(timestamp__gte=since, status='COMPLETED')
        .annotate(day=TruncDay('timestamp'))
        .values('day')
        .annotate(count=Count('id'))
        .order_by('day')
    )
    labels, values = [], []
    for row in qs:
        labels.append(row['day'].strftime('%b %d'))
        values.append(row['count'])
    return labels, values


# ─────────────────────────────────────────────────────────────────────────────
# 1. OVERVIEW / HOME
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class DashboardHomeView(View):
    template_name = 'dashboard/home.html'

    def get(self, request):
        now = timezone.now()
        since_30 = now - timedelta(days=30)
        since_7 = now - timedelta(days=7)

        # KPI Cards
        total_users = CustomUser.objects.count()
        new_users_30d = CustomUser.objects.filter(date_joined__gte=since_30).count()
        total_groups = Group.objects.filter(is_active=True).count()
        total_transactions_30d = Transaction.objects.filter(
            timestamp__gte=since_30, status='COMPLETED'
        ).count()
        total_volume_30d = Transaction.objects.filter(
            timestamp__gte=since_30, status='COMPLETED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        # Platform Revenue
        revenue_30d = PlatformFeeLedger.objects.filter(
            created_at__gte=since_30
        ).aggregate(total=Sum('fee_amount'))['total'] or Decimal('0')
        revenue_7d = PlatformFeeLedger.objects.filter(
            created_at__gte=since_7
        ).aggregate(total=Sum('fee_amount'))['total'] or Decimal('0')

        # Treasury wallet
        try:
            treasury = Wallet.get_treasury_wallet()
            treasury_balance = treasury.balance
        except Exception:
            treasury_balance = Decimal('0')

        # Fee breakdown by type (30d)
        fee_breakdown = (
            PlatformFeeLedger.objects
            .filter(created_at__gte=since_30)
            .values('fee_type')
            .annotate(total=Sum('fee_amount'))
            .order_by('-total')
        )

        # Recent Transactions (10)
        recent_transactions = Transaction.objects.select_related(
            'wallet__user', 'wallet__user__profile'
        ).order_by('-timestamp')[:10]

        # Recent Users (5)
        recent_users = CustomUser.objects.select_related('profile').order_by('-date_joined')[:5]

        # Pending transfer requests
        pending_transfers = GroupWalletTransferRequest.objects.filter(status='PENDING').count()

        # Charts
        rev_labels, rev_values = _revenue_chart_data(30)
        tx_labels, tx_values = _tx_chart_data(30)

        # Transaction type breakdown (30d)
        tx_type_breakdown = (
            Transaction.objects
            .filter(timestamp__gte=since_30, status='COMPLETED')
            .values('transaction_type')
            .annotate(count=Count('id'), total=Sum('amount'))
            .order_by('-total')
        )

        # Organisations & Recurring Cycles
        total_organisations = Organisation.objects.filter(is_active=True).count()
        total_cycles = ContributionCycle.objects.count()
        recurring_collected_total = MemberCyclePayment.objects.filter(
            status='paid'
        ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0')

        # Group Purpose Distribution
        purpose_distribution = (
            Group.objects.filter(is_active=True)
            .values('purpose')
            .annotate(count=Count('id'))
            .order_by('-count')
        )
        purpose_map = dict(Group.GROUP_PURPOSE_CHOICES)
        for p in purpose_distribution:
            p['label'] = purpose_map.get(p['purpose'], p['purpose'].title())

        context = {
            'page': 'home',
            'total_users': total_users,
            'new_users_30d': new_users_30d,
            'total_groups': total_groups,
            'total_organisations': total_organisations,
            'total_cycles': total_cycles,
            'recurring_collected_total': recurring_collected_total,
            'purpose_distribution': purpose_distribution,
            'total_transactions_30d': total_transactions_30d,
            'total_volume_30d': total_volume_30d,
            'revenue_30d': revenue_30d,
            'revenue_7d': revenue_7d,
            'treasury_balance': treasury_balance,
            'fee_breakdown': fee_breakdown,
            'recent_transactions': recent_transactions,
            'recent_users': recent_users,
            'pending_transfers': pending_transfers,
            'rev_labels': json.dumps(rev_labels),
            'rev_values': json.dumps(rev_values),
            'tx_labels': json.dumps(tx_labels),
            'tx_values': json.dumps(tx_values),
            'tx_type_breakdown': tx_type_breakdown,
        }
        return render(request, self.template_name, context)


# ─────────────────────────────────────────────────────────────────────────────
# 2. USERS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class UsersListView(View):
    template_name = 'dashboard/users.html'
    PAGE_SIZE = 25

    def get(self, request):
        q = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '')
        page = max(int(request.GET.get('page', 1)), 1)

        qs = CustomUser.objects.select_related('profile').order_by('-date_joined')

        if q:
            qs = qs.filter(
                Q(phone__icontains=q) |
                Q(email__icontains=q) |
                Q(profile__first_name__icontains=q) |
                Q(profile__surname__icontains=q)
            )
        if status_filter == 'active':
            qs = qs.filter(is_active=True)
        elif status_filter == 'inactive':
            qs = qs.filter(is_active=False)
        elif status_filter == 'verified':
            qs = qs.filter(profile__is_verified=True)
        elif status_filter == 'staff':
            qs = qs.filter(is_staff=True)

        total = qs.count()
        offset = (page - 1) * self.PAGE_SIZE
        users = qs[offset: offset + self.PAGE_SIZE]
        total_pages = max((total + self.PAGE_SIZE - 1) // self.PAGE_SIZE, 1)

        context = {
            'page': 'users',
            'users': users,
            'q': q,
            'status_filter': status_filter,
            'current_page': page,
            'total_pages': total_pages,
            'total': total,
            'page_range': range(max(1, page - 2), min(total_pages + 1, page + 3)),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')
        user_id = request.POST.get('user_id')
        user = get_object_or_404(CustomUser, pk=user_id)
        if action == 'deactivate':
            user.is_active = False
            user.save()
            messages.success(request, f'User {user} deactivated.')
        elif action == 'activate':
            user.is_active = True
            user.save()
            messages.success(request, f'User {user} activated.')
        elif action == 'verify':
            if hasattr(user, 'profile'):
                user.profile.is_verified = True
                user.profile.save()
                messages.success(request, f'User {user} verified.')
        return redirect(request.META.get('HTTP_REFERER', '/dashboard/users/'))


@staff_required
class UserDetailView(View):
    template_name = 'dashboard/user_detail.html'

    def get(self, request, pk):
        user = get_object_or_404(CustomUser.objects.select_related('profile'), pk=pk)
        profile = getattr(user, 'profile', None)

        # Wallet
        wallet = Wallet.objects.filter(user=user).first()
        transactions = []
        if wallet:
            transactions = wallet.transactions.order_by('-timestamp')[:20]

        # Group memberships
        memberships = GroupMembership.objects.filter(
            member=profile
        ).select_related('group').order_by('-date_joined') if profile else []

        # Notifications
        notifications = Notification.objects.filter(recipient=user).order_by('-created_at')[:10]

        # Device tokens
        device_tokens = DeviceToken.objects.filter(user=user, is_active=True)

        context = {
            'page': 'users',
            'target_user': user,
            'profile': profile,
            'wallet': wallet,
            'transactions': transactions,
            'memberships': memberships,
            'notifications': notifications,
            'device_tokens': device_tokens,
        }
        return render(request, self.template_name, context)

    def post(self, request, pk):
        user = get_object_or_404(CustomUser, pk=pk)
        action = request.POST.get('action')
        if action == 'deactivate':
            user.is_active = False
            user.save()
            messages.success(request, f'User deactivated.')
        elif action == 'activate':
            user.is_active = True
            user.save()
            messages.success(request, f'User activated.')
        elif action == 'verify':
            if hasattr(user, 'profile'):
                user.profile.is_verified = True
                user.profile.save()
                messages.success(request, f'Profile verified.')
        elif action == 'send_notification':
            title = request.POST.get('notif_title', '').strip()
            message = request.POST.get('notif_message', '').strip()
            if title and message:
                try:
                    from user.notifications import send_push_notification
                    send_push_notification(
                        user=user,
                        title=title,
                        message=message,
                        notification_type='admin_message',
                    )
                    messages.success(request, 'Push notification sent.')
                except Exception as e:
                    messages.error(request, f'Failed to send: {e}')
        return redirect('dashboard:user_detail', pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# 3. GROUPS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class GroupsListView(View):
    template_name = 'dashboard/groups.html'
    PAGE_SIZE = 25

    def get(self, request):
        q = request.GET.get('q', '').strip()
        purpose_filter = request.GET.get('purpose', '')
        page = max(int(request.GET.get('page', 1)), 1)

        qs = Group.objects.annotate(
            member_count=Count('groupmembership', filter=Q(groupmembership__status='active'))
        ).order_by('-created_at')

        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
        if purpose_filter:
            qs = qs.filter(purpose=purpose_filter)

        total = qs.count()
        offset = (page - 1) * self.PAGE_SIZE
        groups = qs[offset: offset + self.PAGE_SIZE]
        total_pages = max((total + self.PAGE_SIZE - 1) // self.PAGE_SIZE, 1)

        # Attach balances
        for g in groups:
            try:
                g.wallet_balance = g.get_balance()
            except Exception:
                g.wallet_balance = Decimal('0')

        purpose_choices = Group.GROUP_PURPOSE_CHOICES

        context = {
            'page': 'groups',
            'groups': groups,
            'q': q,
            'purpose_filter': purpose_filter,
            'purpose_choices': purpose_choices,
            'current_page': page,
            'total_pages': total_pages,
            'total': total,
            'page_range': range(max(1, page - 2), min(total_pages + 1, page + 3)),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')
        group_id = request.POST.get('group_id')
        group = get_object_or_404(Group, pk=group_id)
        if action == 'deactivate':
            group.is_active = False
            group.save()
            messages.success(request, f'Group "{group.name}" deactivated.')
        elif action == 'activate':
            group.is_active = True
            group.save()
            messages.success(request, f'Group "{group.name}" activated.')
        return redirect(request.META.get('HTTP_REFERER', '/dashboard/groups/'))


@staff_required
class GroupDetailView(View):
    template_name = 'dashboard/group_detail.html'

    def get(self, request, pk):
        group = get_object_or_404(Group, pk=pk)

        # Members
        memberships = GroupMembership.objects.filter(
            group=group
        ).select_related('member', 'member__user').order_by('date_joined')

        # Group wallet balance
        try:
            wallet_balance = group.get_balance()
        except Exception:
            wallet_balance = Decimal('0')

        # Transfer requests
        transfer_requests = GroupWalletTransferRequest.objects.filter(
            group=group
        ).select_related('requested_by', 'recipient_profile').order_by('-created_at')[:10]

        # Recent Transactions touching this group
        recent_transactions = Transaction.objects.filter(
            Q(destination_group=group)
        ).order_by('-timestamp')[:15]

        # Campaigns
        campaigns = FundCampaign.objects.filter(group=group).order_by('-created_at')[:10]

        # SMS credit balance
        sms_balance = GroupSMSCreditBalance.objects.filter(group=group).first()

        # Subscription
        subscription = GroupSubscription.objects.filter(group=group).first()

        # Recurring Contribution Cycles & Payments
        contribution_cycles = list(
            group.contribution_cycles
            .prefetch_related('payments', 'payments__member')
            .order_by('-cycle_year', '-cycle_month')[:12]
        )
        for cycle in contribution_cycles:
            cycle.total_expected_val = cycle.get_total_expected()
            cycle.total_collected_val = cycle.get_total_collected()
            cycle.paid_count_val = cycle.get_paid_count()
            cycle.unpaid_count_val = cycle.get_unpaid_count()
            cycle.progress_val = cycle.get_progress_percentage()

        active_cycle = group.contribution_cycles.filter(status='active').first()

        context = {
            'page': 'groups',
            'group': group,
            'memberships': memberships,
            'wallet_balance': wallet_balance,
            'transfer_requests': transfer_requests,
            'recent_transactions': recent_transactions,
            'campaigns': campaigns,
            'sms_balance': sms_balance,
            'subscription': subscription,
            'contribution_cycles': contribution_cycles,
            'active_cycle': active_cycle,
        }
        return render(request, self.template_name, context)

    def post(self, request, pk):
        group = get_object_or_404(Group, pk=pk)
        action = request.POST.get('action')
        if action == 'toggle_active':
            group.is_active = not group.is_active
            group.save()
            messages.success(request, f'Group status updated.')
        elif action == 'trigger_cycle':
            cycle = group.ensure_active_cycle()
            if cycle:
                messages.success(request, f'Active contribution cycle generated: "{cycle.title}".')
            else:
                messages.warning(request, 'Recurring contributions not enabled or recurring amount is 0.')
        return redirect('dashboard:group_detail', pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# 4. FINANCE
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class FinanceView(View):
    template_name = 'dashboard/finance.html'
    PAGE_SIZE = 30

    def get(self, request):
        tx_type = request.GET.get('tx_type', '')
        tx_status = request.GET.get('tx_status', '')
        date_from = request.GET.get('date_from', '')
        date_to = request.GET.get('date_to', '')
        page = max(int(request.GET.get('page', 1)), 1)

        # Treasury wallet
        try:
            treasury = Wallet.get_treasury_wallet()
            treasury_balance = treasury.balance
            treasury_transactions = treasury.transactions.order_by('-timestamp')[:10]
        except Exception:
            treasury_balance = Decimal('0')
            treasury_transactions = []

        # All-time revenue
        total_revenue = PlatformFeeLedger.objects.aggregate(
            total=Sum('fee_amount')
        )['total'] or Decimal('0')

        # Revenue by type
        revenue_by_type = (
            PlatformFeeLedger.objects
            .values('fee_type')
            .annotate(total=Sum('fee_amount'), count=Count('id'))
            .order_by('-total')
        )

        # Transactions (filtered)
        qs = Transaction.objects.select_related(
            'wallet__user', 'wallet__user__profile', 'destination_group'
        ).order_by('-timestamp')

        if tx_type:
            qs = qs.filter(transaction_type=tx_type)
        if tx_status:
            qs = qs.filter(status=tx_status)
        if date_from:
            qs = qs.filter(timestamp__date__gte=date_from)
        if date_to:
            qs = qs.filter(timestamp__date__lte=date_to)

        total_tx = qs.count()
        offset = (page - 1) * self.PAGE_SIZE
        transactions = qs[offset: offset + self.PAGE_SIZE]
        total_pages = max((total_tx + self.PAGE_SIZE - 1) // self.PAGE_SIZE, 1)

        # Platform fee ledger (recent 20)
        fee_ledger = PlatformFeeLedger.objects.select_related(
            'transaction__wallet__user'
        ).order_by('-created_at')[:20]

        # Pending group transfer requests
        pending_transfers = GroupWalletTransferRequest.objects.filter(
            status='PENDING'
        ).select_related('group', 'recipient_profile').order_by('-created_at')

        context = {
            'page': 'finance',
            'treasury_balance': treasury_balance,
            'treasury_transactions': treasury_transactions,
            'total_revenue': total_revenue,
            'revenue_by_type': revenue_by_type,
            'transactions': transactions,
            'fee_ledger': fee_ledger,
            'pending_transfers': pending_transfers,
            # filters
            'tx_type': tx_type,
            'tx_status': tx_status,
            'date_from': date_from,
            'date_to': date_to,
            'current_page': page,
            'total_pages': total_pages,
            'total_tx': total_tx,
            'page_range': range(max(1, page - 2), min(total_pages + 1, page + 3)),
            'tx_type_choices': Transaction.TransactionType.choices,
            'tx_status_choices': Transaction.TransactionStatus.choices,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        """Execute or reject a pending transfer request."""
        action = request.POST.get('action')
        tr_id = request.POST.get('transfer_id')
        tr = get_object_or_404(GroupWalletTransferRequest, pk=tr_id)
        if action == 'execute':
            try:
                tr.execute()
                messages.success(request, f'Transfer executed successfully.')
            except Exception as e:
                messages.error(request, f'Execution failed: {e}')
        elif action == 'reject':
            tr.status = GroupWalletTransferRequest.STATUS_REJECTED
            tr.save()
            messages.success(request, 'Transfer request rejected.')
        return redirect('dashboard:finance')


# ─────────────────────────────────────────────────────────────────────────────
# 5. CAMPAIGNS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class CampaignsView(View):
    template_name = 'dashboard/campaigns.html'
    PAGE_SIZE = 25

    def get(self, request):
        q = request.GET.get('q', '').strip()
        c_type = request.GET.get('campaign_type', '')
        page = max(int(request.GET.get('page', 1)), 1)

        qs = FundCampaign.objects.select_related('group', 'beneficiary', 'created_by').order_by('-created_at')
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(group__name__icontains=q))
        if c_type:
            qs = qs.filter(campaign_type=c_type)

        total = qs.count()
        offset = (page - 1) * self.PAGE_SIZE
        campaigns = qs[offset: offset + self.PAGE_SIZE]
        total_pages = max((total + self.PAGE_SIZE - 1) // self.PAGE_SIZE, 1)

        # Attach amounts
        for c in campaigns:
            try:
                c.raised = c.get_total_raised()
                c.disbursed = c.get_total_disbursed()
                c.balance = c.get_balance()
                c.contributor_count = c.get_contributor_count()
            except Exception:
                c.raised = c.disbursed = c.balance = Decimal('0')
                c.contributor_count = 0

        # Condolence records
        deceased_records = Deceased.objects.select_related(
            'deceased', 'group'
        ).order_by('-date')[:20]

        for d in deceased_records:
            try:
                d.raised = d.get_total_raised()
                d.disbursed = d.get_total_disbursed()
            except Exception:
                d.raised = d.disbursed = 0

        context = {
            'page': 'campaigns',
            'campaigns': campaigns,
            'deceased_records': deceased_records,
            'q': q,
            'campaign_type': c_type,
            'campaign_type_choices': FundCampaign.CAMPAIGN_TYPE_CHOICES,
            'current_page': page,
            'total_pages': total_pages,
            'total': total,
            'page_range': range(max(1, page - 2), min(total_pages + 1, page + 3)),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')
        campaign_id = request.POST.get('campaign_id')
        campaign = get_object_or_404(FundCampaign, pk=campaign_id)
        if action == 'close':
            campaign.close()
            messages.success(request, f'Campaign "{campaign.title}" closed.')
        elif action == 'mark_disbursed':
            campaign.funds_disbursed = True
            campaign.save()
            messages.success(request, f'Campaign marked as disbursed.')
        elif action == 'reopen':
            campaign.contributions_open = True
            campaign.save()
            messages.success(request, f'Campaign reopened.')
        return redirect('dashboard:campaigns')


# ─────────────────────────────────────────────────────────────────────────────
# 6. SMS & NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class SMSView(View):
    template_name = 'dashboard/sms.html'

    def get(self, request):
        packages = SMSCreditPackage.objects.order_by('-is_active', 'price')
        balances = GroupSMSCreditBalance.objects.select_related('group').order_by('-balance')[:30]
        purchases = SMSCreditPurchase.objects.select_related(
            'group', 'package', 'purchased_by'
        ).order_by('-created_at')[:20]

        total_credits_sold = SMSCreditPurchase.objects.aggregate(
            total=Sum('credits_added')
        )['total'] or 0
        total_sms_revenue = SMSCreditPurchase.objects.aggregate(
            total=Sum('amount_paid')
        )['total'] or Decimal('0')

        groups_all = Group.objects.filter(is_active=True).order_by('name')[:200]

        context = {
            'page': 'sms',
            'packages': packages,
            'balances': balances,
            'purchases': purchases,
            'total_credits_sold': total_credits_sold,
            'total_sms_revenue': total_sms_revenue,
            'groups_all': groups_all,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')

        if action == 'create_package':
            name = request.POST.get('name', '').strip()
            credits_count = request.POST.get('credits_count')
            price = request.POST.get('price')
            if name and credits_count and price:
                SMSCreditPackage.objects.create(
                    name=name,
                    credits_count=int(credits_count),
                    price=Decimal(price),
                    is_active=True,
                )
                messages.success(request, f'SMS package "{name}" created.')
            else:
                messages.error(request, 'All fields required.')

        elif action == 'toggle_package':
            pkg_id = request.POST.get('package_id')
            pkg = get_object_or_404(SMSCreditPackage, pk=pkg_id)
            pkg.is_active = not pkg.is_active
            pkg.save()
            messages.success(request, f'Package "{pkg.name}" {"activated" if pkg.is_active else "deactivated"}.')

        elif action == 'delete_package':
            pkg_id = request.POST.get('package_id')
            pkg = get_object_or_404(SMSCreditPackage, pk=pkg_id)
            pkg_name = pkg.name
            pkg.delete()
            messages.success(request, f'Package "{pkg_name}" deleted.')

        elif action == 'broadcast_notification':
            title = request.POST.get('broadcast_title', '').strip()
            message = request.POST.get('broadcast_message', '').strip()
            target = request.POST.get('target', 'all')
            group_id = request.POST.get('group_id', '')

            if not title or not message:
                messages.error(request, 'Title and message are required.')
                return redirect('dashboard:sms')

            try:
                from user.notifications import send_push_notification

                if target == 'all':
                    users = CustomUser.objects.filter(is_active=True)
                elif target == 'group' and group_id:
                    group = get_object_or_404(Group, pk=group_id)
                    user_ids = GroupMembership.objects.filter(
                        group=group, status='active'
                    ).values_list('member__user_id', flat=True)
                    users = CustomUser.objects.filter(id__in=user_ids, is_active=True)
                else:
                    users = CustomUser.objects.none()

                count = 0
                for u in users:
                    try:
                        send_push_notification(
                            user=u,
                            title=title,
                            message=message,
                            notification_type='admin_broadcast',
                        )
                        count += 1
                    except Exception:
                        pass
                messages.success(request, f'Broadcast sent to {count} user(s).')
            except Exception as e:
                messages.error(request, f'Broadcast failed: {e}')

        return redirect('dashboard:sms')


# ─────────────────────────────────────────────────────────────────────────────
# 7. PLATFORM SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class SettingsView(View):
    template_name = 'dashboard/settings.html'

    def get(self, request):
        config = PlatformFeeConfig.get_config()
        groups = Group.objects.filter(is_active=True).order_by('name')[:100]
        context = {
            'page': 'settings',
            'config': config,
            'groups': groups,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        config = PlatformFeeConfig.get_config()
        action = request.POST.get('action', 'save_fees')

        try:
            if action in ('save_fees', 'save_all'):
                config.is_fees_enabled = 'is_fees_enabled' in request.POST
                config.topup_percentage_fee = Decimal(request.POST.get('topup_percentage_fee', '2.50'))
                config.topup_flat_fee = Decimal(request.POST.get('topup_flat_fee', '0.00'))
                config.withdrawal_percentage_fee = Decimal(request.POST.get('withdrawal_percentage_fee', '1.50'))
                config.withdrawal_flat_fee = Decimal(request.POST.get('withdrawal_flat_fee', '5.00'))
                config.group_transfer_percentage_fee = Decimal(request.POST.get('group_transfer_percentage_fee', '1.00'))
                config.group_transfer_flat_fee = Decimal(request.POST.get('group_transfer_flat_fee', '0.00'))

            if action in ('save_saas', 'save_all'):
                config.is_saas_subscriptions_enabled = 'is_saas_subscriptions_enabled' in request.POST
                config.group_pro_monthly_price = Decimal(request.POST.get('group_pro_monthly_price', '150.00'))
                config.komunity_plus_monthly_price = Decimal(request.POST.get('komunity_plus_monthly_price', '35.00'))

            if action in ('save_vendors', 'save_all'):
                config.is_vendor_marketplace_enabled = 'is_vendor_marketplace_enabled' in request.POST
                config.vendor_commission_percentage = Decimal(request.POST.get('vendor_commission_percentage', '10.00'))

            config.save()
            messages.success(request, 'Platform configuration saved successfully.')
        except Exception as e:
            messages.error(request, f'Error saving configuration: {e}')

        return redirect('dashboard:settings')


# ─────────────────────────────────────────────────────────────────────────────
# 8. VENDORS & INSURANCE
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class VendorsView(View):
    template_name = 'dashboard/vendors.html'

    def get(self, request):
        vendors = ServiceVendor.objects.order_by('-is_active', '-created_at')
        bookings = VendorBooking.objects.select_related(
            'vendor', 'user', 'group'
        ).order_by('-created_at')[:20]
        policies = MicroInsurancePolicy.objects.order_by('-is_active', '-created_at')
        enrollments = InsurancePolicyEnrollment.objects.select_related(
            'policy', 'group'
        ).order_by('-created_at')[:20]

        total_booking_revenue = VendorBooking.objects.filter(
            status='COMPLETED'
        ).aggregate(total=Sum('commission_amount'))['total'] or Decimal('0')

        category_choices = ServiceVendor.CATEGORY_CHOICES

        context = {
            'page': 'vendors',
            'vendors': vendors,
            'bookings': bookings,
            'policies': policies,
            'enrollments': enrollments,
            'total_booking_revenue': total_booking_revenue,
            'category_choices': category_choices,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')

        if action == 'create_vendor':
            name = request.POST.get('name', '').strip()
            category = request.POST.get('category', '')
            contact_phone = request.POST.get('contact_phone', '').strip()
            contact_email = request.POST.get('contact_email', '').strip()
            description = request.POST.get('description', '').strip()
            if name and category and contact_phone:
                ServiceVendor.objects.create(
                    name=name, category=category,
                    contact_phone=contact_phone,
                    contact_email=contact_email or None,
                    description=description,
                )
                messages.success(request, f'Vendor "{name}" added.')
            else:
                messages.error(request, 'Name, category, and phone are required.')

        elif action == 'toggle_vendor':
            vid = request.POST.get('vendor_id')
            v = get_object_or_404(ServiceVendor, pk=vid)
            v.is_active = not v.is_active
            v.save()
            messages.success(request, f'Vendor "{v.name}" {"activated" if v.is_active else "deactivated"}.')

        elif action == 'verify_vendor':
            vid = request.POST.get('vendor_id')
            v = get_object_or_404(ServiceVendor, pk=vid)
            v.is_verified = True
            v.save()
            messages.success(request, f'Vendor "{v.name}" verified.')

        elif action == 'create_policy':
            provider_name = request.POST.get('provider_name', '').strip()
            policy_name = request.POST.get('policy_name', '').strip()
            cover_amount = request.POST.get('cover_amount')
            monthly_premium = request.POST.get('monthly_premium')
            if provider_name and policy_name and cover_amount and monthly_premium:
                MicroInsurancePolicy.objects.create(
                    provider_name=provider_name,
                    policy_name=policy_name,
                    cover_amount=Decimal(cover_amount),
                    monthly_premium=Decimal(monthly_premium),
                )
                messages.success(request, f'Policy "{policy_name}" created.')
            else:
                messages.error(request, 'All policy fields are required.')

        return redirect('dashboard:vendors')


# ─────────────────────────────────────────────────────────────────────────────
# 9. ORGANISATIONS
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class OrganisationsListView(View):
    template_name = 'dashboard/organisations.html'
    PAGE_SIZE = 25

    def get(self, request):
        q = request.GET.get('q', '').strip()
        entity_type_filter = request.GET.get('entity_type', '')
        verified_filter = request.GET.get('is_verified', '')
        page = max(int(request.GET.get('page', 1)), 1)

        qs = Organisation.objects.select_related('creator').order_by('-date')

        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q) | Q(registration_number__icontains=q) | Q(email__icontains=q))
        if entity_type_filter:
            qs = qs.filter(entity_type=entity_type_filter)
        if verified_filter == '1':
            qs = qs.filter(is_verified=True)
        elif verified_filter == '0':
            qs = qs.filter(is_verified=False)

        total = qs.count()
        offset = (page - 1) * self.PAGE_SIZE
        organisations = qs[offset: offset + self.PAGE_SIZE]
        total_pages = max((total + self.PAGE_SIZE - 1) // self.PAGE_SIZE, 1)

        for org in organisations:
            try:
                org.wallet_balance = org.get_balance()
            except Exception:
                org.wallet_balance = Decimal('0')

        entity_type_choices = Organisation.ENTITY_TYPE_CHOICES

        context = {
            'page': 'organisations',
            'organisations': organisations,
            'q': q,
            'entity_type_filter': entity_type_filter,
            'verified_filter': verified_filter,
            'entity_type_choices': entity_type_choices,
            'current_page': page,
            'total_pages': total_pages,
            'total': total,
            'page_range': range(max(1, page - 2), min(total_pages + 1, page + 3)),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        action = request.POST.get('action')
        org_id = request.POST.get('org_id')
        org = get_object_or_404(Organisation, pk=org_id)
        if action == 'deactivate':
            org.is_active = False
            org.save()
            messages.success(request, f'Organisation "{org.name}" deactivated.')
        elif action == 'activate':
            org.is_active = True
            org.save()
            messages.success(request, f'Organisation "{org.name}" activated.')
        elif action == 'verify':
            org.is_verified = True
            org.save()
            messages.success(request, f'Organisation "{org.name}" verified.')
        elif action == 'unverify':
            org.is_verified = False
            org.save()
            messages.success(request, f'Organisation "{org.name}" unverified.')
        return redirect(request.META.get('HTTP_REFERER', '/dashboard/organisations/'))


@staff_required
class OrganisationDetailView(View):
    template_name = 'dashboard/organisation_detail.html'

    def get(self, request, pk):
        org = get_object_or_404(Organisation, pk=pk)

        # Balance
        try:
            wallet_balance = org.get_balance()
        except Exception:
            wallet_balance = Decimal('0')

        # Affiliated Campaigns
        campaigns = FundCampaign.objects.filter(organisation=org).order_by('-created_at')

        # Recent Transactions touching this organisation
        recent_transactions = Transaction.objects.filter(
            destination_organisation=org
        ).order_by('-timestamp')[:15]

        # Admins
        admins_list = list(org.admins.all())
        if org.creator and org.creator not in admins_list:
            admins_list.insert(0, org.creator)

        context = {
            'page': 'organisations',
            'org': org,
            'wallet_balance': wallet_balance,
            'campaigns': campaigns,
            'recent_transactions': recent_transactions,
            'admins_list': admins_list,
        }
        return render(request, self.template_name, context)

    def post(self, request, pk):
        org = get_object_or_404(Organisation, pk=pk)
        action = request.POST.get('action')
        if action == 'toggle_verify':
            org.is_verified = not org.is_verified
            org.save()
            messages.success(request, f'Organisation verification updated.')
        elif action == 'toggle_active':
            org.is_active = not org.is_active
            org.save()
            messages.success(request, f'Organisation status updated.')
        return redirect('dashboard:organisation_detail', pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# HTMX / Ajax helpers
# ─────────────────────────────────────────────────────────────────────────────

@staff_required
class QuickStatsView(View):
    """HTMX endpoint: returns live KPIs as JSON for real-time refreshing."""

    def get(self, request):
        try:
            treasury = Wallet.get_treasury_wallet()
            balance = float(treasury.balance)
        except Exception:
            balance = 0.0

        since_today = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tx_today = Transaction.objects.filter(
            timestamp__gte=since_today, status='COMPLETED'
        ).count()
        revenue_today = float(
            PlatformFeeLedger.objects.filter(
                created_at__gte=since_today
            ).aggregate(total=Sum('fee_amount'))['total'] or 0
        )
        pending = GroupWalletTransferRequest.objects.filter(status='PENDING').count()

        return JsonResponse({
            'treasury_balance': balance,
            'tx_today': tx_today,
            'revenue_today': revenue_today,
            'pending_transfers': pending,
        })
