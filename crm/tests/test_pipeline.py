from datetime import timedelta
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from crm.models import Activity, FollowUpTask, Lead, LeadAuditLog
from crm.services import (
    change_lead_status,
    check_potential_duplicates,
    complete_task,
    log_interaction,
    manually_activate_lead,
    reassign_lead,
)

User = get_user_model()


class PipelineServiceTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user(
            phone='0821000001',
            email='manager@komunity.app',
            password='testpassword123',
            is_staff=True
        )
        self.agent = User.objects.create_user(
            phone='0821000002',
            email='agent@komunity.app',
            password='testpassword123',
            is_staff=True
        )

    def test_pipeline_transitions_and_audit_logging(self):
        lead = Lead.objects.create(
            name="Zenzele Savings Club",
            phone="0825551111",
            assigned_to=self.agent,
            created_by=self.agent
        )
        self.assertEqual(lead.status, 'NEW')

        # 1. Log intro call -> should auto-progress to CONTACTED
        activity = log_interaction(
            lead=lead,
            activity_type='call',
            subject="Intro call with chair",
            summary="Chairperson was very receptive, interested in digital record keeping.",
            outcome='interested',
            user=self.agent
        )
        lead.refresh_from_db()
        self.assertEqual(lead.status, 'CONTACTED')
        self.assertIsNotNone(lead.last_contacted_at)

        # Audit log should be recorded
        audit = LeadAuditLog.objects.filter(lead=lead, action='status_change').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.old_value, 'NEW')
        self.assertEqual(audit.new_value, 'CONTACTED')

        # 2. Advance to DEMO_BOOKED
        change_lead_status(lead, 'DEMO_BOOKED', user=self.agent, notes="Demo set for Friday")
        lead.refresh_from_db()
        self.assertEqual(lead.status, 'DEMO_BOOKED')

        # 3. Transition to LOST requires reason
        with self.assertRaises(ValidationError):
            change_lead_status(lead, 'LOST', user=self.agent)

        change_lead_status(
            lead,
            'LOST',
            user=self.agent,
            loss_reason='not_interested',
            notes="Decided to stick with traditional book"
        )
        lead.refresh_from_db()
        self.assertEqual(lead.status, 'LOST')
        self.assertEqual(lead.loss_reason, 'not_interested')

        # Check audit log for marked_lost
        audit_lost = LeadAuditLog.objects.filter(lead=lead, action='marked_lost').first()
        self.assertIsNotNone(audit_lost)

        # 4. Manager correction: moving out of LOST clears loss reasons
        change_lead_status(lead, 'NURTURE', user=self.manager, notes="Re-evaluating in Q4")
        lead.refresh_from_db()
        self.assertEqual(lead.status, 'NURTURE')
        self.assertIsNone(lead.loss_reason)

    def test_reassignment_service(self):
        lead = Lead.objects.create(
            name="Meadowlands Church Choir",
            phone="0824442222",
            assigned_to=self.agent
        )

        reassign_lead(lead, self.manager, reassigned_by=self.manager, reason="Senior relationship transfer")
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.manager)

        # Verify audit log and timeline activity
        audit = LeadAuditLog.objects.filter(lead=lead, action='reassigned').first()
        self.assertIsNotNone(audit)
        self.assertIn("agent@komunity.app", audit.old_value)
        self.assertIn("manager@komunity.app", audit.new_value)

        act = Activity.objects.filter(lead=lead, activity_type='owner_reassigned').first()
        self.assertIsNotNone(act)

    def test_complete_task_with_next_action(self):
        lead = Lead.objects.create(name="Task Lead", phone="0831112233")
        task = FollowUpTask.objects.create(
            lead=lead,
            title="Call to confirm demo time",
            due_at=timezone.now() + timedelta(days=1),
            status='open'
        )

        next_due = timezone.now() + timedelta(days=4)
        complete_task(
            task=task,
            user=self.agent,
            completion_note="Demo confirmed for Thursday 3PM",
            create_next_action="Run live demo session",
            next_action_date=next_due
        )

        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        self.assertIsNotNone(task.completed_at)

        # New follow-up task should have been created
        new_task = FollowUpTask.objects.filter(lead=lead, title="Run live demo session").first()
        self.assertIsNotNone(new_task)
        self.assertEqual(new_task.status, 'open')
        lead.refresh_from_db()
        self.assertEqual(lead.next_follow_up_at, next_due)

    def test_duplicate_detection(self):
        Lead.objects.create(
            name="Bambanani Burial Society",
            phone="0827778888",
            area_or_location="Soweto"
        )

        # Exact phone match
        dups1 = check_potential_duplicates(phone="0827778888")
        self.assertEqual(dups1.count(), 1)

        # Formatted phone match (+27 vs 0)
        dups2 = check_potential_duplicates(phone="+27 82 777 8888")
        self.assertEqual(dups2.count(), 1)

        # Name and area match
        dups3 = check_potential_duplicates(name="Bambanani Burial Society", area="Soweto")
        self.assertEqual(dups3.count(), 1)

        # Unrelated query
        dups4 = check_potential_duplicates(phone="0830000000", name="Durban Soccer")
        self.assertEqual(dups4.count(), 0)
