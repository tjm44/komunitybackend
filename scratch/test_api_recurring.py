import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from rest_framework.test import APIRequestFactory, force_authenticate
from chema.models import Group, ContributionCycle, MemberCyclePayment, GroupMembership
from user.models import CustomUser, Profile
from wallet.models import Wallet, Transaction
from api_v1.views import GroupViewSet
from decimal import Decimal

print("Running API test for Recurring Contributions...")

# 1. Setup Admin User and Member User
admin_user, _ = CustomUser.objects.get_or_create(phone="+27820000099", defaults={'is_active': True})
admin_profile = admin_user.profile
admin_profile.first_name = "Admin"
admin_profile.surname = "Chief"
admin_profile.save()

member_user, _ = CustomUser.objects.get_or_create(phone="+27820000098", defaults={'is_active': True})
member_profile = member_user.profile
member_profile.first_name = "Thabo"
member_profile.surname = "Molefe"
member_profile.save()

member_wallet, _ = Wallet.objects.get_or_create(user=member_user, defaults={'balance': 500.00, 'external_wallet_id': f"WAAS_{member_user.id}"})
member_wallet.balance = 500.00
member_wallet.save()

# 2. Setup Group
group, _ = Group.objects.get_or_create(
    name="Soweto Stokvel Club",
    defaults={
        'creator': admin_user,
        'purpose': 'stokvel',
        'enable_recurring_contributions': True,
        'recurring_amount': Decimal('200.00'),
        'recurring_frequency': 'monthly',
        'recurring_due_day': 25,
        'recurring_title': 'Monthly Stokvel Savings',
        'recurring_reminder_days': 3,
    }
)
group.enable_recurring_contributions = True
group.recurring_amount = Decimal('200.00')
group.save()

# Add memberships
GroupMembership.objects.get_or_create(group=group, member=admin_profile, defaults={'role': 'admin', 'status': 'active', 'is_active': True})
GroupMembership.objects.get_or_create(group=group, member=member_profile, defaults={'role': 'member', 'status': 'active', 'is_active': True})

factory = APIRequestFactory()

# Reset any existing payment for test idempotency
MemberCyclePayment.objects.filter(cycle__group=group, member=member_profile).update(status='pending', amount_paid=0.00, paid_at=None, transaction=None)

# Test GET recurring_cycles
req = factory.get(f'/api/v1/groups/{group.id}/recurring_cycles/')
force_authenticate(req, user=member_user)
resp = GroupViewSet.as_view({'get': 'recurring_cycles'})(req, pk=group.id)
assert resp.status_code == 200, f"recurring_cycles failed: {resp.data}"
print("GET recurring_cycles Response:", resp.data.get('summary'))

# Test POST pay_cycle
req = factory.post(f'/api/v1/groups/{group.id}/pay_cycle/', {'amount': 200.00}, format='json')
force_authenticate(req, user=member_user)
resp = GroupViewSet.as_view({'post': 'pay_cycle'})(req, pk=group.id)
assert resp.status_code == 200, f"pay_cycle failed: {resp.data}"
print("POST pay_cycle Response:", resp.data.get('message'))

# Test POST send_cycle_reminder
req = factory.post(f'/api/v1/groups/{group.id}/send_cycle_reminder/', {}, format='json')
force_authenticate(req, user=admin_user)
resp = GroupViewSet.as_view({'post': 'send_cycle_reminder'})(req, pk=group.id)
assert resp.status_code == 200, f"send_cycle_reminder failed: {resp.data}"
print("POST send_cycle_reminder Response:", resp.data.get('message'))

print("All Backend Recurring Contribution API Tests PASSED Successfully!")
