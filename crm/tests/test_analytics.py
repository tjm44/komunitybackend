from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from crm.models import Lead, FollowUpTask

User = get_user_model()


class AnalyticsDashboardTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            phone='0827770001',
            email='manager@komunity.app',
            password='password123',
            is_staff=True
        )
        self.client = Client()
        self.client.force_login(self.staff_user)

    def test_empty_dashboard(self):
        resp = self.client.get(reverse('crm:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "CRM Acquisition Pipeline")
        self.assertContains(resp, "Total pipeline: 0 leads")

    def test_dashboard_metrics(self):
        # 1 New
        Lead.objects.create(name="Lead 1", phone="0820000001", status="NEW")
        # 1 Demo Booked
        Lead.objects.create(name="Lead 2", phone="0820000002", status="DEMO_BOOKED")
        # 1 Registered
        Lead.objects.create(name="Lead 3", phone="0820000003", status="REGISTERED")
        # 1 Activated
        Lead.objects.create(name="Lead 4", phone="0820000004", status="ACTIVATED", is_manually_activated=True)

        resp = self.client.get(reverse('crm:dashboard'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Total pipeline: 4 leads")
        # 2 Registered or Activated leads (Lead 3 and Lead 4 count as registered+)
        self.assertContains(resp, "Registered Groups")
        self.assertContains(resp, "Activated Communities")

    def test_duplicate_check_api(self):
        Lead.objects.create(name="Mamelodi Ladies Club", phone="0825556677", area_or_location="Mamelodi")

        resp = self.client.get(reverse('crm:duplicate_check'), {'phone': '0825556677'})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['matches'][0]['name'], 'Mamelodi Ladies Club')
