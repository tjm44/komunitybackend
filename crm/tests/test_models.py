from datetime import timedelta
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from chema.models import Group, GroupMembership, Post
from user.models import Profile
from crm.models import AcquisitionSource, Activity, Contact, FollowUpTask, Lead, normalize_phone

User = get_user_model()


class LeadModelTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            phone='0821110001',
            email='agent@komunity.app',
            password='testpassword123',
            is_staff=True
        )

    def test_phone_normalization(self):
        self.assertEqual(normalize_phone('+27 82 123 4567'), '0821234567')
        self.assertEqual(normalize_phone('27821234567'), '0821234567')
        self.assertEqual(normalize_phone('(082) 123-4567'), '0821234567')
        self.assertEqual(normalize_phone('082 123 4567'), '0821234567')

    def test_lead_creation_defaults(self):
        lead = Lead.objects.create(
            name="Soweto Ladies Stokvel",
            phone="0821234567",
            created_by=self.staff_user
        )
        self.assertEqual(lead.status, 'NEW')
        self.assertEqual(lead.priority, 'normal')
        self.assertEqual(lead.lead_type, 'community')
        self.assertEqual(lead.community_type, 'stokvel')
        self.assertEqual(lead.consent_to_contact, 'unknown')
        self.assertFalse(lead.do_not_contact)
        self.assertFalse(lead.is_active_community)

    def test_lead_lost_requires_reason(self):
        lead = Lead(
            name="Test Society",
            phone="0821234567",
            status="LOST"
        )
        with self.assertRaises(ValidationError):
            lead.full_clean()

        # With reason 'other', detail is required
        lead.loss_reason = 'other'
        lead.loss_reason_detail = ''
        with self.assertRaises(ValidationError):
            lead.full_clean()

        # Valid with detail
        lead.loss_reason_detail = 'Group dissolved and split funds'
        lead.full_clean()
        lead.save()
        self.assertEqual(lead.status, 'LOST')

    def test_task_is_overdue(self):
        lead = Lead.objects.create(name="Task Lead", phone="0821234567")
        past_due = timezone.now() - timedelta(days=2)
        future_due = timezone.now() + timedelta(days=2)

        overdue_task = FollowUpTask.objects.create(
            lead=lead,
            title="Overdue follow up",
            due_at=past_due,
            status='open'
        )
        upcoming_task = FollowUpTask.objects.create(
            lead=lead,
            title="Upcoming follow up",
            due_at=future_due,
            status='open'
        )

        self.assertTrue(overdue_task.is_overdue)
        self.assertFalse(upcoming_task.is_overdue)

        # Once completed, it is no longer overdue
        overdue_task.status = 'completed'
        overdue_task.save()
        self.assertFalse(overdue_task.is_overdue)

    def test_activation_logic_against_real_group_records(self):
        lead = Lead.objects.create(name="Khayelitsha Burial Society", phone="0839998888")
        self.assertFalse(lead.is_active_community)

        # Create real Komunity Group
        group = Group.objects.create(
            name="Khayelitsha Burial Society Group",
            creator=self.staff_user,
            purpose='bereavement'
        )
        lead.converted_komunity_group = group
        lead.save()

        # Still false because < 5 members
        self.assertFalse(lead.is_active_community)

        # Add 4 additional active members to reach 5 (creator is already member 1)
        for i in range(1, 5):
            u = User.objects.create_user(phone=f'084000000{i}', password='pw')
            p = Profile.objects.get(user=u)
            GroupMembership.objects.create(
                group=group,
                member=p,
                status='active',
                is_active=True
            )

        self.assertGreaterEqual(group.get_total_members(), 5)

        # Still false because no recent core action within 14 days
        self.assertFalse(lead.is_active_community)

        # Add a Post to the group within 14 days
        creator_profile = Profile.objects.get(user=self.staff_user)
        Post.objects.create(
            author=creator_profile,
            group=group,
            content="Welcome to our new Komunity digital burial register!"
        )

        # Now group meets all criteria (>=5 members and core activity in 14 days)
        self.assertTrue(lead.is_active_community)

        # Also test manual activation override
        lead_manual = Lead.objects.create(name="Manual Activated Lead", is_manually_activated=True)
        self.assertTrue(lead_manual.is_active_community)
