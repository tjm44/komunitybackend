from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group as AuthGroup
from django.test import TestCase, Client
from django.urls import reverse

from crm.models import AcquisitionSource, Lead
from crm.forms import AmbassadorOnboardingForm

User = get_user_model()


class AmbassadorOnboardingTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.manager_group, _ = AuthGroup.objects.get_or_create(name='CRM Manager')
        self.field_agent_group, _ = AuthGroup.objects.get_or_create(name='Field Agent')

        # Manager user
        self.manager = User.objects.create_user(
            phone='0820000010',
            email='manager@komunity.app',
            password='password123',
            is_staff=True
        )
        self.manager.groups.add(self.manager_group)

        # Standard field agent user
        self.agent = User.objects.create_user(
            phone='0820000011',
            email='agent@komunity.app',
            password='password123',
            is_staff=False
        )
        self.agent.groups.add(self.field_agent_group)

        # Public user (no CRM rights)
        self.public_user = User.objects.create_user(
            phone='0820000012',
            email='public@komunity.app',
            password='password123'
        )

        # Campaign source
        self.campaign = AcquisitionSource.objects.create(
            name="Soweto Blitz 2026",
            source_type="field_visit",
            location_or_area="Soweto"
        )

    def test_ambassador_onboarding_form_valid(self):
        form_data = {
            'first_name': 'Sipho',
            'last_name': 'Mhlongo',
            'phone': '083 456 7890',
            'email': 'sipho@example.com',
            'territory': 'Soweto / Dobsonville',
            'campaign_source': self.campaign.id,
            'password': '',
            'notes': 'Experienced community organiser'
        }
        form = AmbassadorOnboardingForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['phone'], '0834567890')

    def test_ambassador_onboarding_form_duplicate_phone(self):
        form_data = {
            'first_name': 'Duplicate',
            'last_name': 'User',
            'phone': '082 000 0010',  # Already exists for self.manager
        }
        form = AmbassadorOnboardingForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)

    def test_get_ambassadors_page_permissions(self):
        url = reverse('crm:ambassadors')

        # Public user cannot access
        self.client.force_login(self.public_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        # Field agent cannot manage other ambassadors
        self.client.force_login(self.agent)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        # Manager can access
        self.client.force_login(self.manager)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Field Ambassadors")
        self.assertContains(response, "Onboard Ambassador")

    def test_post_onboard_new_ambassador(self):
        url = reverse('crm:ambassadors')
        self.client.force_login(self.manager)

        post_data = {
            'first_name': 'Zanele',
            'last_name': 'Khumalo',
            'phone': '071 987 6543',
            'email': 'zanele@example.com',
            'territory': 'Umlazi / Section C',
            'campaign_source': self.campaign.id,
            'password': '',
            'notes': 'Stokvel chairperson connections'
        }
        response = self.client.post(url, data=post_data, follow=True)
        self.assertEqual(response.status_code, 200)

        # Verify user was created with group and profile
        new_user = User.objects.get(phone='0719876543')
        self.assertTrue(new_user.is_staff)
        self.assertTrue(new_user.groups.filter(name='Field Agent').exists())
        self.assertEqual(new_user.profile.first_name, 'Zanele')
        self.assertEqual(new_user.profile.surname, 'Khumalo')
        self.assertEqual(new_user.profile.cultural_background, 'Umlazi / Section C')

        # Verify welcome toolkit card is rendered
        self.assertContains(response, "Ambassador Onboarded Successfully!")
        self.assertContains(response, "Zanele Khumalo")
        self.assertContains(response, "0719876543")
