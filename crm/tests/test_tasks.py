from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from crm.models import Lead, FollowUpTask

User = get_user_model()


class TaskViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            phone='0828880001',
            email='agent@komunity.app',
            password='password123',
            is_staff=True
        )
        self.client = Client()
        self.client.force_login(self.staff_user)
        self.lead = Lead.objects.create(name="Stokvel for Tasks", phone="0824445555", assigned_to=self.staff_user)

    def test_create_task_post(self):
        due = timezone.now() + timedelta(days=2)
        resp = self.client.post(
            reverse('crm:lead_create_task', args=[self.lead.id]),
            {
                'title': 'Call treasurer for constitution',
                'task_type': 'call',
                'due_at': due.strftime('%Y-%m-%dT%H:%M'),
                'priority': 'normal',
                'assigned_to': self.staff_user.id
            }
        )
        self.assertEqual(resp.status_code, 302)
        task = FollowUpTask.objects.filter(lead=self.lead).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.title, 'Call treasurer for constitution')

    def test_my_followups_view_categorization(self):
        now = timezone.now()
        # Overdue
        FollowUpTask.objects.create(lead=self.lead, title="Overdue Item", due_at=now - timedelta(days=2), status='open', assigned_to=self.staff_user)
        # Due today
        FollowUpTask.objects.create(lead=self.lead, title="Today Item", due_at=now + timedelta(hours=2), status='open', assigned_to=self.staff_user)
        # Upcoming
        FollowUpTask.objects.create(lead=self.lead, title="Future Item", due_at=now + timedelta(days=3), status='open', assigned_to=self.staff_user)
        # Completed
        FollowUpTask.objects.create(lead=self.lead, title="Done Item", due_at=now - timedelta(days=1), status='completed', assigned_to=self.staff_user)

        resp = self.client.get(reverse('crm:my_followups'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Overdue Item")
        self.assertContains(resp, "Today Item")
        self.assertContains(resp, "Future Item")
        self.assertContains(resp, "Done Item")

    def test_complete_task_endpoint(self):
        task = FollowUpTask.objects.create(
            lead=self.lead,
            title="Pending Checkup",
            due_at=timezone.now() + timedelta(days=1),
            status='open',
            assigned_to=self.staff_user
        )

        resp = self.client.post(
            reverse('crm:task_complete', args=[task.id]),
            {'completion_note': 'Followed up successfully, all agreed.'}
        )
        self.assertEqual(resp.status_code, 302)
        task.refresh_from_db()
        self.assertEqual(task.status, 'completed')
        self.assertEqual(task.completion_note, 'Followed up successfully, all agreed.')
