from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from crm.models import Lead, Activity, AcquisitionSource

User = get_user_model()


class LeadViewTests(TestCase):
    def setUp(self):
        self.staff_user = User.objects.create_user(
            phone='0829990001',
            email='staff@komunity.app',
            password='password123',
            is_staff=True
        )
        self.client = Client()
        self.client.force_login(self.staff_user)

    def test_quick_capture_success(self):
        source = AcquisitionSource.objects.create(name="Zone 4 Mall Activation", source_type="mall_activation")

        data = {
            'name': 'Meadowlands Stokvel Society',
            'primary_contact_name': 'Nomasonto Dlamini',
            'phone': '082 555 4321',
            'community_type': 'stokvel',
            'area_or_location': 'Meadowlands, Soweto',
            'source': source.id,
            'consent_to_contact': 'yes',
            'initial_note': 'Met chairperson at mall activation booth, requested follow-up.',
        }

        resp = self.client.post(reverse('crm:quick_capture'), data)
        self.assertEqual(resp.status_code, 302)

        lead = Lead.objects.filter(name='Meadowlands Stokvel Society').first()
        self.assertIsNotNone(lead)
        self.assertEqual(lead.phone, '0825554321')
        self.assertEqual(lead.assigned_to, self.staff_user)
        self.assertEqual(lead.consent_to_contact, 'yes')

        # Check initial activity logged
        act = Activity.objects.filter(lead=lead).first()
        self.assertIsNotNone(act)
        self.assertIn("mall activation booth", act.summary)

    def test_quick_capture_requires_contact_or_phone(self):
        # Missing both contact name and phone
        data = {
            'name': 'Anonymous Stokvel',
            'primary_contact_name': '',
            'phone': '',
            'community_type': 'stokvel',
        }
        resp = self.client.post(reverse('crm:quick_capture'), data)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please provide at least a contact name or phone number")

    def test_lead_list_search_and_filtering(self):
        Lead.objects.create(name="Alexandra Burial Club", area_or_location="Alexandra", status="NEW")
        Lead.objects.create(name="Diepsloot Savings", area_or_location="Diepsloot", status="QUALIFIED")

        # Search query
        resp_q = self.client.get(reverse('crm:lead_list'), {'q': 'Alexandra'})
        self.assertContains(resp_q, "Alexandra Burial Club")
        self.assertNotContains(resp_q, "Diepsloot Savings")

        # Status filter
        resp_s = self.client.get(reverse('crm:lead_list'), {'status': 'QUALIFIED'})
        self.assertContains(resp_s, "Diepsloot Savings")
        self.assertNotContains(resp_s, "Alexandra Burial Club")

    def test_lead_detail_and_status_change(self):
        lead = Lead.objects.create(name="Orlando Sports Club", phone="0821239876", status="NEW")

        # Detail view renders
        resp = self.client.get(reverse('crm:lead_detail', args=[lead.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Orlando Sports Club")

        # Post status transition to QUALIFIED
        resp_post = self.client.post(
            reverse('crm:lead_change_status', args=[lead.id]),
            {'status': 'QUALIFIED', 'notes': 'Meets group criteria'}
        )
        self.assertEqual(resp_post.status_code, 302)
        lead.refresh_from_db()
        self.assertEqual(lead.status, 'QUALIFIED')
