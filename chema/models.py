import os
import random
from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.db.models.signals import post_save
from django.dispatch import receiver
from user.models import Profile

class Group(models.Model):

    GROUP_PURPOSE_CHOICES = [
        ('bereavement', 'Bereavement Fund'),
        ('excess', 'Insurance Excess Fund'),
        ('emergency', 'Emergency / Disaster Fundraiser'),
        ('custom', 'Custom Fund'),
        ('church', 'Church / Religious Group'),
        ('stokvel', 'Stokvel & Rotating Savings'),
        ('student', 'Student Body & Society'),
    ]

    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    description = models.TextField(null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True)
    cover_image = models.ImageField(upload_to='group_cover_images', null=True, blank=True)
    admin = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True, related_name='admin_groups')
    members = models.ManyToManyField(Profile, through='GroupMembership', related_name='groups')
    
    max_members = models.PositiveIntegerField(null=True, blank=True, help_text="Leave blank for unlimited")
    requires_approval = models.BooleanField(default=False, help_text="New members need approval")
    
    # Notification & Governance Settings
    notify_on_member_join = models.BooleanField(default=True, help_text="Notify all members when a new member joins")
    notify_on_member_promote = models.BooleanField(default=True, help_text="Notify all members when a member is promoted to admin")
    notify_on_wallet_transfer = models.BooleanField(default=True, help_text="Notify all members when a transfer is made from the group wallet")
    notify_on_campaign_created = models.BooleanField(default=True, help_text="Notify all members when a new campaign is launched")
    min_disbursement_approvals = models.PositiveSmallIntegerField(
        default=1,
        help_text="Minimum number of admin approvals required before a disbursement/transfer executes (1 = any admin can disburse immediately)"
    )
    
    # Ownership
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='created_groups',null=True, blank=True)
    admins  = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='admin_groups', blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True,null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    # Group Purpose & Fund Type
    purpose = models.CharField(
        max_length=20,
        choices=GROUP_PURPOSE_CHOICES,
        default='bereavement',
        help_text="The primary fund-pooling purpose of this group"
    )
    fund_description = models.TextField(
        null=True, blank=True,
        help_text="Detailed description of the group's fund purpose (used for 'custom' and 'emergency' types)"
    )
    verified_members_only = models.BooleanField(
        default=False,
        help_text="Only allow verified user profiles to join this group"
    )

    # SARS Tax & PBO Exemption Status
    is_pbo_registered = models.BooleanField(
        default=False,
        help_text="Whether this group is a SARS-approved Public Benefit Organisation (PBO)"
    )
    pbo_reference_number = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="SARS PBO Tax Exemption reference number (e.g. 930012345)"
    )
    is_section_18a_approved = models.BooleanField(
        default=False,
        help_text="Approved by SARS to issue Section 18A tax-deductible donation receipts"
    )

    # Recurring Contribution Configuration
    enable_recurring_contributions = models.BooleanField(
        default=False,
        help_text="Whether this group collects recurring scheduled contributions"
    )
    recurring_amount = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text="Amount expected per member per cycle"
    )
    recurring_frequency = models.CharField(
        max_length=20, default='monthly',
        choices=[
            ('monthly', 'Monthly'),
            ('weekly', 'Weekly'),
            ('biweekly', 'Bi-weekly'),
            ('annual', 'Annual'),
        ],
        help_text="Cycle frequency for recurring dues"
    )
    recurring_due_day = models.PositiveSmallIntegerField(
        default=25,
        help_text="Day of the month dues are expected (1-31)"
    )
    recurring_title = models.CharField(
        max_length=150, default='Monthly Contribution', blank=True,
        help_text="Descriptive title for recurring cycles (e.g. Monthly Dues, Stokvel Pool)"
    )
    recurring_reminder_days = models.PositiveSmallIntegerField(
        default=3,
        help_text="Days prior to due date to send reminder notification"
    )

    # Wallet Integration
    external_wallet_id = models.CharField(max_length=100, unique=True, null=True, blank=True)

    def get_admins(self):
        return self.members.filter(groupmembership__is_admin=True)
    
    def get_total_members(self):
        return self.groupmembership_set.filter(status='active').count()

    def get_active_members(self):
        return self.members.filter(groupmembership__status='active')


    def get_balance(self):
        from wallet.models import Transaction
        from django.db.models import Sum
        from decimal import Decimal
        
        # Incoming: Transfers from members
        incoming = Transaction.objects.filter(
            destination_group=self,
            transaction_type='TRANSFER',
            status='COMPLETED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Outgoing: Payouts/Disbursements to members
        outgoing = Transaction.objects.filter(
            destination_group=self,
            transaction_type='PAYOUT_RECEIVED',
            status='COMPLETED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        return incoming - outgoing

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('create_post', args=[str(self.id)])

    def is_admin(self, user):
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        # Creator check
        if user == self.creator:
            return True
        # Admins M2M check
        if user in self.admins.all():
            return True
        # Membership role check
        return self.groupmembership_set.filter(
            member__user=user, 
            role__in=['admin', 'moderator'],
            is_active=True
        ).exists()

    def is_member(self, user):
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        if self.creator_id and user.id == self.creator_id:
            return True
        if self.admins.filter(id=user.id).exists():
            return True
        try:
            profile = user.profile
        except Exception:
            return False
            
        return self.groupmembership_set.filter(
            member=profile, 
            status='active',
            is_active=True
        ).exists()

    def get_admin_count(self):
        """Returns the number of active admins in this group."""
        admin_user_ids = set()
        if self.creator_id:
            admin_user_ids.add(self.creator_id)
        admin_user_ids.update(self.admins.values_list('id', flat=True))
        admin_user_ids.update(
            self.groupmembership_set.filter(role='admin', status='active', is_active=True)
            .values_list('member__user_id', flat=True)
        )
        return max(1, len(admin_user_ids))


    def can_join(self, user):
        """Check if user can join this group"""
        if not user.is_authenticated:
            return False, "You must be logged in to join groups"
        
        if self.is_member(user):
            return False, "You're already a member of this group"
        
        if self.privacy == 'closed':
            return False, "This group is closed to new members"
        
        if self.is_full:
            return False, "This group has reached its maximum capacity"
        
        return True, "Can join"    
    
    def save(self, *args, **kwargs):
        if not self.pk and not self.cover_image:  # Only if it's a new group and no cover image is provided
            try:
                # Try to find default images in STATIC_ROOT first, then project's static folder
                possible_dirs = [
                    os.path.join(settings.BASE_DIR, 'static', 'group_cover_images'),
                    os.path.join(settings.STATIC_ROOT, 'group_cover_images') if hasattr(settings, 'STATIC_ROOT') and settings.STATIC_ROOT else None
                ]
                
                for cover_images_dir in possible_dirs:
                    if cover_images_dir and os.path.exists(cover_images_dir):
                        cover_images = [os.path.join('group_cover_images', file) for file in os.listdir(cover_images_dir) if file.endswith(('.jpg', '.jpeg', '.png', '.gif'))]
                        if cover_images:
                            self.cover_image = random.choice(cover_images)
                            break
            except Exception as e:
                print(f"Error setting default cover image: {e}")
                
        super().save(*args, **kwargs)


class Organisation(models.Model):
    ENTITY_TYPE_CHOICES = [
        ('ngo', 'NGO'),
        ('church', 'Church/Religious Org'),
        ('npo', 'NPO/Charity'),
        ('corporate', 'Corporate/Business'),
        ('other', 'Other Organisation'),
    ]

    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    description = models.TextField(null=True, blank=True)
    email = models.EmailField(null=True, blank=True)
    phone_number = models.CharField(max_length=20, null=True, blank=True)
    date = models.DateTimeField(auto_now_add=True)
    cover_image = models.ImageField(upload_to='organisation_cover_images', null=True, blank=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='created_organisations', null=True, blank=True)
    admins  = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='admin_organisations', blank=True)
    admin2 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='admin2_organisations', null=True, blank=True)
    admin3 = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, related_name='admin3_organisations', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Legal / verification details
    is_verified = models.BooleanField(default=False)
    registration_number = models.CharField(max_length=100, blank=True, null=True)
    entity_type = models.CharField(max_length=50, choices=ENTITY_TYPE_CHOICES, default='ngo')

    # Wallet
    external_wallet_id = models.CharField(max_length=100, unique=True, null=True, blank=True)

    def get_balance(self):
        from wallet.models import Transaction
        from django.db.models import Sum
        from decimal import Decimal
        incoming = Transaction.objects.filter(
            destination_organisation=self,
            transaction_type='TRANSFER',
            status='COMPLETED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        outgoing = Transaction.objects.filter(
            destination_organisation=self,
            transaction_type='PAYOUT_RECEIVED',
            status='COMPLETED'
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        return incoming - outgoing

    def __str__(self):
        return self.name

    def is_admin(self, user):
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        if user == self.creator:
            return True
        if user in self.admins.all():
            return True
        if user == self.admin2 or user == self.admin3:
            return True
        return False




class GroupMembership(models.Model):

    ROLE_CHOICES = [
        ('member', 'Member'),
        ('moderator', 'Moderator'),
        ('admin', 'Admin'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'Pending Approval'),
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('banned', 'Banned'),
    ]
    member      = models.ForeignKey(Profile, on_delete=models.CASCADE)
    group       = models.ForeignKey(Group, on_delete=models.CASCADE)
    is_admin    = models.BooleanField(default=False)
    status   = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    role     = models.CharField(max_length=20, choices=ROLE_CHOICES, default='member')
    date_joined = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey( settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,blank=True,related_name='approved_memberships')
    is_active   = models.BooleanField(default=True)
    is_deceased = models.BooleanField(default=False)
    can_post    = models.BooleanField(default=True)
    can_comment = models.BooleanField(default=True)
    join_message = models.TextField(blank=True, help_text="Message when requesting to join")
    beneficiary_name = models.CharField(max_length=150, blank=True, null=True)
    beneficiary_relationship = models.CharField(max_length=100, blank=True, null=True)
    beneficiary_phone = models.CharField(max_length=30, blank=True, null=True)
    # Insurance Excess / Vehicle Verification Fields (Fraud Prevention)
    vehicle_make_model = models.CharField(max_length=150, blank=True, null=True, help_text="e.g. 2022 Toyota Hilux 2.8 GD-6")
    vehicle_registration = models.CharField(max_length=50, blank=True, null=True, help_text="e.g. CA 987-654")
    insurer_name = models.CharField(max_length=100, blank=True, null=True, help_text="e.g. Santam / OUTsurance")
    policy_number = models.CharField(max_length=100, blank=True, null=True)
    vin_number = models.CharField(max_length=100, blank=True, null=True, help_text="Vehicle Identification / Chassis Number")
    driver_license_number = models.CharField(max_length=100, blank=True, null=True, help_text="Driver / Owner ID Number")
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.member.full_name} in {self.group.name}"
    
    def get_is_admin(self):
        return self.is_admin

    def approve(self, approved_by_user):
        """Approve membership"""
        self.status = 'active'
        self.is_active = True
        self.approved_at = timezone.now()
        self.approved_by = approved_by_user
        self.save()

    def is_admin_or_creator(self):
        return (self.role in ['admin', 'moderator'] or 
                self.user == self.group.creator)    


class Post(models.Model):
    author = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)
    group = models.ForeignKey(Group, on_delete=models.CASCADE, null=True, blank=True)
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE, null=True, blank=True, related_name='posts')
    content = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to='post_images/', null=True, blank=True)
    video = models.FileField(upload_to='post_videos/', null=True, blank=True)
    likes = models.ManyToManyField(Profile, related_name='liked_posts', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    approved = models.BooleanField(default=True, null=True, blank=True)

    def __str__(self):
        return f"{self.author.full_name}: {self.content}"

    def get_likes_count(self):
        return self.likes.count()



class PostImage(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField(upload_to='post_images/')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"Image for {self.post.id}"
    
    class Meta:
        ordering = ['uploaded_at']


class Comment(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE)
    author = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.author.full_name}: {self.content}"

    class Meta:
        ordering = ['-created_at']


class Reply(models.Model):
    author = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='replies', null=True, blank=True)
    content = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)

    def __str__(self):
        return f'Reply by {self.author.full_name} -> {self.content}'

    class Meta:
        verbose_name_plural = "Replies"
        ordering = ['created_at']
        
class Dependent(models.Model):
    guardian = models.ForeignKey(Profile, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100,null=True, blank=True)
    date_of_birth = models.DateField()
    relationship = models.CharField(max_length=100, null=True, blank=True)
    date_added = models.DateTimeField(auto_now_add=True)
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name='dependents',null=True, blank=True)

    def __str__(self):
        return self.name


# ─────────────────────────────────────────────────────────────
# Signal: enforce creator admin membership on every Group save
# ─────────────────────────────────────────────────────────────

@receiver(post_save, sender=Group)
def ensure_creator_is_admin(sender, instance, created, **kwargs):
    """
    Whenever a Group is saved, guarantee the creator has an active admin
    GroupMembership.  This is the single source of truth so that the rule
    is enforced regardless of which code path creates or edits the group.
    """
    if not instance.creator:
        return

    try:
        creator_profile = instance.creator.profile
    except Exception:
        return

    membership, _ = GroupMembership.objects.get_or_create(
        group=instance,
        member=creator_profile,
        defaults={
            'is_admin': True,
            'role': 'admin',
            'status': 'active',
            'is_active': True,
        }
    )

    # Repair any existing membership that lost admin rights
    needs_save = False
    if not membership.is_admin:
        membership.is_admin = True
        needs_save = True
    if membership.role not in ('admin', 'moderator'):
        membership.role = 'admin'
        needs_save = True
    if membership.status != 'active':
        membership.status = 'active'
        needs_save = True
    if not membership.is_active:
        membership.is_active = True
        needs_save = True
    if needs_save:
        membership.save()


class GroupBereavementProfile(models.Model):
    CONTRIBUTION_SCHEDULE_CHOICES = [
        ('monthly', 'Monthly Contribution'),
        ('event_driven', 'Per Bereavement Event'),
        ('annual', 'Annual Contribution'),
    ]

    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='bereavement_profile')
    beneficiary_name = models.CharField(max_length=150, null=True, blank=True)
    beneficiary_relationship = models.CharField(max_length=100, null=True, blank=True)
    beneficiary_phone = models.CharField(max_length=30, null=True, blank=True)
    beneficiary_payout_details = models.TextField(null=True, blank=True, help_text="Bank details or mobile money account for payouts")
    
    allow_dependents = models.BooleanField(default=True)
    max_dependents = models.PositiveIntegerField(default=5, help_text="Maximum allowed dependents per member")
    allowed_dependent_types = models.CharField(max_length=255, default="Spouse, Child, Parent, Sibling, In-Law", help_text="Comma separated allowed relationships")
    
    contribution_schedule = models.CharField(max_length=20, choices=CONTRIBUTION_SCHEDULE_CHOICES, default='event_driven')
    fixed_contribution_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    fixed_claim_payout = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Bereavement Profile - {self.group.name}"


class GroupChurchProfile(models.Model):
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='church_profile')
    denomination = models.CharField(max_length=100, null=True, blank=True)
    branch_parish_name = models.CharField(max_length=150, null=True, blank=True)
    
    enable_faith_pledges = models.BooleanField(default=True)
    enable_tax_receipts = models.BooleanField(default=False)
    enable_bulletin_announcements = models.BooleanField(default=True)
    default_tithe_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Church Profile - {self.group.name}"


class GroupStokvelProfile(models.Model):
    STOKVEL_TYPE_CHOICES = [
        ('rotational_payout', 'Rotational Payout (ROSCA / Mahodisana)'),
        ('savings_and_investment', 'Savings & Investment'),
        ('grocery', 'Grocery & Festive Payout'),
        ('burial', 'Burial Stokvel'),
    ]
    CYCLE_FREQUENCY_CHOICES = [
        ('weekly', 'Weekly'),
        ('biweekly', 'Bi-weekly'),
        ('monthly', 'Monthly'),
        ('annual', 'Annual'),
    ]
    ROTATION_MODE_CHOICES = [
        ('fixed_sequence', 'Fixed Sequence'),
        ('random_draw', 'Random Draw'),
        ('bidding', 'Bidding / Auction'),
        ('request_on_need', 'Request on Need'),
    ]

    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='stokvel_profile')
    stokvel_type = models.CharField(max_length=30, choices=STOKVEL_TYPE_CHOICES, default='rotational_payout')
    cycle_frequency = models.CharField(max_length=20, choices=CYCLE_FREQUENCY_CHOICES, default='monthly')
    contribution_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    payout_rotation_mode = models.CharField(max_length=20, choices=ROTATION_MODE_CHOICES, default='fixed_sequence')
    penalty_late_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    borrowing_allowed = models.BooleanField(default=False)
    payout_target_month = models.CharField(max_length=20, null=True, blank=True, help_text="e.g. December for grocery stokvels")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Stokvel Profile - {self.group.name}"


class GroupStudentProfile(models.Model):
    STUDENT_BODY_TYPE_CHOICES = [
        ('residence_committee', 'Residence Committee'),
        ('faculty_society', 'Faculty & Academic Society'),
        ('student_representative_council', 'Student Representative Council'),
        ('sports_res', 'Residence Sports Club'),
        ('study_group', 'Study & Mutual Aid Group'),
    ]
    FEE_PERIOD_CHOICES = [
        ('per_semester', 'Per Semester'),
        ('annual', 'Annual'),
        ('once_off', 'Once-off'),
    ]

    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='student_profile')
    institution_name = models.CharField(max_length=150, null=True, blank=True)
    campus_name = models.CharField(max_length=100, null=True, blank=True)
    student_body_type = models.CharField(max_length=35, choices=STUDENT_BODY_TYPE_CHOICES, default='faculty_society')
    
    student_id_required = models.BooleanField(default=True, help_text="Require member student registration number")
    membership_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    membership_fee_period = models.CharField(max_length=20, choices=FEE_PERIOD_CHOICES, default='annual')
    
    enable_event_ticketing = models.BooleanField(default=True)
    enable_emergency_relief_fund = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Student Profile - {self.group.name}"


class GroupExcessProfile(models.Model):
    group = models.OneToOneField(Group, on_delete=models.CASCADE, related_name='excess_profile')
    max_excess_payout = models.DecimalField(max_digits=10, decimal_places=2, default=5000.00)
    require_vin_verification = models.BooleanField(default=True, help_text="Require vehicle VIN/chassis number to prevent fraud")
    require_policy_proof = models.BooleanField(default=True, help_text="Require valid insurance policy number")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Excess Profile - {self.group.name}"


@receiver(post_save, sender=Group)
def ensure_group_profile(sender, instance, created, **kwargs):
    """
    Ensure the matching profile object exists for the group based on its purpose.
    """
    if instance.purpose == 'bereavement':
        GroupBereavementProfile.objects.get_or_create(group=instance)
    elif instance.purpose == 'excess':
        GroupExcessProfile.objects.get_or_create(group=instance)
    elif instance.purpose == 'church':
        GroupChurchProfile.objects.get_or_create(group=instance)
    elif instance.purpose == 'stokvel':
        GroupStokvelProfile.objects.get_or_create(group=instance)
    elif instance.purpose == 'student':
        GroupStudentProfile.objects.get_or_create(group=instance)

    # If recurring contributions enabled, ensure current cycle exists
    if instance.enable_recurring_contributions and instance.recurring_amount > 0:
        try:
            instance.ensure_active_cycle()
        except Exception as e:
            print(f"Error ensuring active cycle on group save: {e}")


# =============================================================================
# RECURRING CONTRIBUTION CYCLES & MEMBER PAYMENT LEDGER
# =============================================================================

class ContributionCycle(models.Model):
    CYCLE_STATUS_CHOICES = [
        ('upcoming', 'Upcoming'),
        ('active', 'Active (Open for Payment)'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    group = models.ForeignKey(
        Group, on_delete=models.CASCADE, related_name='contribution_cycles'
    )
    title = models.CharField(max_length=200)
    due_date = models.DateField(help_text="Contribution deadline for this cycle")
    target_amount_per_member = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text="Fixed amount expected from each active member"
    )
    status = models.CharField(
        max_length=20, choices=CYCLE_STATUS_CHOICES, default='active'
    )
    cycle_month = models.PositiveSmallIntegerField(null=True, blank=True)
    cycle_year = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-due_date', '-created_at']
        unique_together = ('group', 'cycle_month', 'cycle_year')

    def __str__(self):
        return f"{self.group.name} – {self.title} (Due {self.due_date})"

    def get_total_expected(self):
        from django.db.models import Sum
        return float(self.payments.aggregate(total=Sum('amount_due'))['total'] or 0.00)

    def get_total_collected(self):
        from django.db.models import Sum
        return float(self.payments.filter(status='paid').aggregate(total=Sum('amount_paid'))['total'] or 0.00)

    def get_paid_count(self):
        return self.payments.filter(status='paid').count()

    def get_unpaid_count(self):
        return self.payments.exclude(status__in=['paid', 'exempt']).count()

    def get_total_members_count(self):
        return self.payments.count()

    def get_progress_percentage(self):
        expected = self.get_total_expected()
        if expected <= 0:
            return 0
        collected = self.get_total_collected()
        return min(100, round((collected / expected) * 100))

    def populate_member_payments(self):
        """
        Populate MemberCyclePayment records for all active members in the group.
        """
        active_memberships = self.group.groupmembership_set.filter(
            status='active', is_active=True
        ).select_related('member')

        for membership in active_memberships:
            MemberCyclePayment.objects.get_or_create(
                cycle=self,
                member=membership.member,
                defaults={
                    'amount_due': self.target_amount_per_member,
                    'amount_paid': 0.00,
                    'status': 'pending',
                }
            )


class MemberCyclePayment(models.Model):
    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending Payment'),
        ('paid', 'Paid'),
        ('overdue', 'Overdue'),
        ('exempt', 'Exempt / Waived'),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('wallet', 'Wallet Balance'),
        ('cash', 'Cash'),
        ('bank_transfer', 'Bank Transfer'),
        ('mobile_money', 'Mobile Money'),
        ('other', 'Other'),
    ]

    cycle = models.ForeignKey(
        ContributionCycle, on_delete=models.CASCADE, related_name='payments'
    )
    member = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name='cycle_payments'
    )
    amount_due = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    status = models.CharField(
        max_length=20, choices=PAYMENT_STATUS_CHOICES, default='pending'
    )
    paid_at = models.DateTimeField(null=True, blank=True)
    transaction = models.ForeignKey(
        'wallet.Transaction', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='cycle_payment'
    )
    payment_method = models.CharField(
        max_length=50, choices=PAYMENT_METHOD_CHOICES, default='wallet'
    )
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['status', 'member__first_name', 'member__surname']
        unique_together = ('cycle', 'member')

    def __str__(self):
        return f"{self.member.full_name} -> {self.cycle.title}: {self.status.upper()} (R{self.amount_paid}/R{self.amount_due})"

    def mark_as_paid(self, amount, transaction=None, method='wallet'):
        self.amount_paid = amount
        self.status = 'paid'
        self.paid_at = timezone.now()
        self.payment_method = method
        if transaction:
            self.transaction = transaction
        self.save()


def ensure_group_active_cycle(self):
    """
    Helper method attached to Group to ensure an active ContributionCycle exists
    for the current month if recurring contributions are enabled.
    """
    if not self.enable_recurring_contributions or self.recurring_amount <= 0:
        return None

    now = timezone.now()
    month = now.month
    year = now.year

    # Calculate due date for current month
    import calendar
    from datetime import date
    max_days = calendar.monthrange(year, month)[1]
    due_day = min(self.recurring_due_day or 25, max_days)
    due_date = date(year, month, due_day)

    month_name = now.strftime('%B')
    title = f"{month_name} {year} {self.recurring_title or 'Contribution'}"

    cycle, created = ContributionCycle.objects.get_or_create(
        group=self,
        cycle_month=month,
        cycle_year=year,
        defaults={
            'title': title,
            'due_date': due_date,
            'target_amount_per_member': self.recurring_amount,
            'status': 'active',
        }
    )

    cycle.populate_member_payments()
    return cycle


# Attach method to Group class
Group.ensure_active_cycle = ensure_group_active_cycle



