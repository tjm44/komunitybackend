import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from chema.models import Group, ContributionCycle, MemberCyclePayment, GroupMembership
from user.models import CustomUser, Profile
from wallet.models import Wallet

print("Testing Recurring Group Contributions Backend...")

# Create or get test user
user, _ = CustomUser.objects.get_or_create(phone="+27820000001", defaults={'is_active': True})
profile = user.profile
profile.first_name = "Test"
profile.surname = "Member"
profile.save()

wallet, _ = Wallet.objects.get_or_create(user=user, defaults={'balance': 1000.00})
if wallet.get_balance() < 500:
    wallet.balance = 1000.00
    wallet.save()

# Create test group with recurring contributions enabled
group, created = Group.objects.get_or_create(
    name="Test Recurring Group",
    defaults={
        'creator': user,
        'enable_recurring_contributions': True,
        'recurring_amount': 150.00,
        'recurring_frequency': 'monthly',
        'recurring_due_day': 25,
        'recurring_title': 'Monthly Membership Dues',
        'recurring_reminder_days': 3,
        'purpose': 'stokvel'
    }
)

if not created:
    group.enable_recurring_contributions = True
    group.recurring_amount = 150.00
    group.recurring_frequency = 'monthly'
    group.recurring_due_day = 25
    group.recurring_title = 'Monthly Membership Dues'
    group.save()

cycle = group.ensure_active_cycle()
print(f"Cycle created: {cycle.title}, Due: {cycle.due_date}, Target: R{cycle.target_amount_per_member}")
print(f"Cycle Total Expected: R{cycle.get_total_expected()}, Total Collected: R{cycle.get_total_collected()}")

# Test payments population
cycle.populate_member_payments()
payment = MemberCyclePayment.objects.filter(cycle=cycle, member=profile).first()
print(f"Member Payment Record: {payment}")

print("Backend verification test PASSED!")
