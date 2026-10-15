from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group as AuthGroup
from django.test import TestCase, Client
from django.urls import reverse

from crm.models import Lead, FollowUpTask
from crm.permissions import (
    filter_leads_for_user,
    filter_tasks_for_user,
    can_reassign_leads,
    is_crm_admin,
    is_crm_manager,
    is_field_agent,
)

User = get_user_model()


class PermissionTests(TestCase):
    def setUp(self):
        # Create roles
        self.field_agent_group = AuthGroup.objects.create(name='Field Agent')
        self.manager_group = AuthGroup.objects.create(name='CRM Manager')
        self.admin_group = AuthGroup.objects.create(name='CRM Admin')

        # Create users
        self.admin_user = User.objects.create_user(phone='0820000001', email='admin@komunity.app', password='password123', is_staff=True, is_superuser=True)
        self.manager_user = User.objects.create_user(phone='0820000002', email='manager@komunity.app', password='password123', is_staff=True)
        self.manager_user.groups.add(self.manager_group)

        self.agent1 = User.objects.create_user(phone='0820000003', email='agent1@komunity.app', password='password123', is_staff=False)
        self.agent1.groups.add(self.field_agent_group)

        self.agent2 = User.objects.create_user(phone='0820000004', email='agent2@komunity.app', password='password123', is_staff=False)
        self.agent2.groups.add(self.field_agent_group)

        self.public_user = User.objects.create_user(phone='0820000005', email='public@komunity.app', password='password123', is_staff=False)

        # Create leads
        self.lead_agent1 = Lead.objects.create(name="Agent 1 Lead", phone="0821111111", assigned_to=self.agent1)
        self.lead_agent2 = Lead.objects.create(name="Agent 2 Lead", phone="0822222222", assigned_to=self.agent2)

    def test_role_check_helpers(self):
        self.assertTrue(is_crm_admin(self.admin_user))
        self.assertTrue(is_crm_manager(self.manager_user))
        self.assertTrue(is_field_agent(self.agent1))
        self.assertFalse(is_crm_manager(self.agent1))

        self.assertTrue(can_reassign_leads(self.admin_user))
        self.assertTrue(can_reassign_leads(self.manager_user))
        self.assertFalse(can_reassign_leads(self.agent1))

    def test_field_agent_lead_scoping(self):
        all_leads = Lead.objects.all()

        agent1_leads = filter_leads_for_user(all_leads, self.agent1)
        self.assertEqual(agent1_leads.count(), 1)
        self.assertEqual(agent1_leads.first(), self.lead_agent1)

        manager_leads = filter_leads_for_user(all_leads, self.manager_user)
        self.assertEqual(manager_leads.count(), 2)

    def test_url_access_controls(self):
        client = Client()

        # Unauthenticated redirects to login
        resp = client.get(reverse('crm:lead_list'))
        self.assertEqual(resp.status_code, 302)

        # Authenticated manager can access
        client.force_login(self.manager_user)
        resp_manager = client.get(reverse('crm:lead_list'))
        self.assertEqual(resp_manager.status_code, 200)

        # Agent can access assigned leads
        client.force_login(self.agent1)
        resp_agent = client.get(reverse('crm:lead_list'))
        self.assertEqual(resp_agent.status_code, 200)

        # Reassign endpoint rejects field agent
        resp_reassign = client.post(
            reverse('crm:lead_reassign', args=[self.lead_agent1.id]),
            {'assigned_to': self.agent2.id}
        )
        # Should redirect back with error message, lead remains assigned to agent1
        self.lead_agent1.refresh_from_db()
        self.assertEqual(self.lead_agent1.assigned_to, self.agent1)
