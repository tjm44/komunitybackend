from decimal import Decimal
from datetime import date
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from chema.models import (
    Group, GroupMembership, ContributionCycle, MemberCyclePayment,
    GroupBereavementProfile, GroupStokvelProfile
)
from user.models import Profile

User = get_user_model()


class ChemaModelTests(TestCase):
    def setUp(self):
        self.creator = User.objects.create_user(
            phone='0821112222',
            email='creator@komunity.app',
            password='testpassword123'
        )
        self.member_user = User.objects.create_user(
            phone='0823334444',
            email='member@komunity.app',
            password='testpassword123'
        )
        # Profile is created by signal on User creation
        self.creator_profile = Profile.objects.get(user=self.creator)
        self.member_profile = Profile.objects.get(user=self.member_user)

    def test_group_creation_and_creator_admin_signal(self):
        """Verify that creating a group automatically makes the creator an active admin."""
        group = Group.objects.create(
            name='Test Stokvel Group',
            description='A test group for stokvel savings',
            creator=self.creator,
            purpose='stokvel'
        )

        membership = GroupMembership.objects.filter(group=group, member=self.creator_profile).first()
        self.assertIsNotNone(membership)
        self.assertTrue(membership.is_admin)
        self.assertEqual(membership.role, 'admin')
        self.assertEqual(membership.status, 'active')
        self.assertTrue(group.is_admin(self.creator))
        self.assertTrue(group.is_member(self.creator))

    def test_group_profiles_created_automatically(self):
        """Verify group profile is auto-created on save based on purpose."""
        group_stokvel = Group.objects.create(
            name='Mzansi Stokvel',
            creator=self.creator,
            purpose='stokvel'
        )
        self.assertTrue(GroupStokvelProfile.objects.filter(group=group_stokvel).exists())

        group_bereavement = Group.objects.create(
            name='Family Bereavement Fund',
            creator=self.creator,
            purpose='bereavement'
        )
        self.assertTrue(GroupBereavementProfile.objects.filter(group=group_bereavement).exists())

    def test_contribution_cycle_and_member_dues_tracking(self):
        """Test recurring contribution cycles, member payment population, and dues tracking."""
        group = Group.objects.create(
            name='Monthly Savings Club',
            creator=self.creator,
            purpose='stokvel',
            enable_recurring_contributions=True,
            recurring_amount=Decimal('500.00'),
            recurring_frequency='monthly',
            recurring_due_day=25
        )

        # Add active member
        GroupMembership.objects.create(
            group=group,
            member=self.member_profile,
            status='active',
            is_active=True,
            role='member'
        )

        # Create cycle
        cycle = ContributionCycle.objects.create(
            group=group,
            title='October 2026 Dues',
            due_date=date(2026, 10, 25),
            target_amount_per_member=Decimal('500.00'),
            status='active',
            cycle_month=10,
            cycle_year=2026
        )

        # Populate member payments
        cycle.populate_member_payments()

        # Both creator and member should have payment records
        payments = MemberCyclePayment.objects.filter(cycle=cycle)
        self.assertEqual(payments.count(), 2)

        # Initially 0 collected
        self.assertEqual(cycle.get_total_expected(), 1000.00)
        self.assertEqual(cycle.get_total_collected(), 0.00)
        self.assertEqual(cycle.get_paid_count(), 0)
        self.assertEqual(cycle.get_unpaid_count(), 2)
        self.assertEqual(cycle.get_progress_percentage(), 0)

        # Mark creator's payment as paid
        creator_payment = payments.get(member=self.creator_profile)
        creator_payment.mark_as_paid(Decimal('500.00'), method='wallet')

        # Check updated tracking stats
        self.assertEqual(cycle.get_paid_count(), 1)
        self.assertEqual(cycle.get_unpaid_count(), 1)
        self.assertEqual(cycle.get_total_collected(), 500.00)
        self.assertEqual(cycle.get_progress_percentage(), 50)
