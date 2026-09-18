import uuid
from decimal import Decimal
from datetime import timedelta

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

from chema.models import Group, GroupMembership
from condolence.models import CampaignContribution, Contribution, Deceased, FundCampaign
from condolence.serializers import (
    CampaignContributionSerializer, ContributionSerializer, DeceasedSerializer,
    FundCampaignSerializer
)
from user.models import Profile
from user.notifications import send_push_notification
from wallet.models import (
    GroupWalletTransferRequest, PlatformFeeConfig, PlatformFeeLedger,
    Transaction, Transaction as WalletTransaction, Wallet
)
from wallet.serializers import (
    GroupWalletTransferRequestSerializer, TransactionSerializer
)
from .base import StandardPagination
from .auth import verify_user_pin

User = get_user_model()

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
