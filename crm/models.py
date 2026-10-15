import re
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def normalize_phone(phone_str):
    """Normalize phone numbers for deduplication and South African formatting."""
    if not phone_str:
        return ""
    # Strip spaces, hyphens, parentheses
    cleaned = re.sub(r'[\s\-\(\)\.]+', '', str(phone_str))
    # Replace leading +27 with 0 for national standard
    if cleaned.startswith('+27'):
        cleaned = '0' + cleaned[3:]
    elif cleaned.startswith('27') and len(cleaned) == 11:
        cleaned = '0' + cleaned[2:]
    return cleaned


class AcquisitionSource(models.Model):
    """Represents a campaign or source channel for community acquisition."""

    SOURCE_TYPE_CHOICES = [
        ('field_visit', 'Field Visit'),
        ('referral', 'Referral'),
        ('church', 'Church / Faith Community'),
        ('mall_activation', 'Mall Activation'),
        ('stokvel_event', 'Stokvel Event / AGM'),
        ('social_media', 'Social Media'),
        ('paid_ad', 'Paid Advertisement'),
        ('website', 'Website / Landing Page'),
        ('qr_code', 'QR Code'),
        ('partnership', 'Strategic Partnership'),
        ('other', 'Other'),
    ]

    name = models.CharField(max_length=150)
    source_type = models.CharField(max_length=30, choices=SOURCE_TYPE_CHOICES, default='field_visit')
    campaign_code = models.CharField(max_length=60, unique=True, null=True, blank=True)
    location_or_area = models.CharField(max_length=150, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='owned_crm_sources')
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Acquisition Source'
        verbose_name_plural = 'Acquisition Sources'

    def __str__(self):
        return f"{self.name} ({self.get_source_type_display()})"


class Lead(models.Model):
    """Represents a prospective community or organisation in the acquisition pipeline."""

    LEAD_TYPE_CHOICES = [
        ('community', 'Community / Group'),
        ('individual', 'Individual'),
        ('organization', 'Organisation / NGO'),
        ('partner', 'Partner'),
    ]

    COMMUNITY_TYPE_CHOICES = [
        ('stokvel', 'Stokvel & Savings Club'),
        ('burial_society', 'Burial Society'),
        ('church', 'Church / Faith Group'),
        ('family_group', 'Family Group'),
        ('savings_group', 'Savings Group'),
        ('mutual_aid', 'Mutual Aid / Support Group'),
        ('sports_club', 'Sports Club'),
        ('school', 'School / Parent Body'),
        ('other', 'Other'),
    ]

    PREFERRED_METHOD_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('phone', 'Phone Call'),
        ('sms', 'SMS'),
        ('email', 'Email'),
        ('in_person', 'In Person'),
        ('other', 'Other'),
    ]

    CURRENT_PROCESS_CHOICES = [
        ('whatsapp', 'WhatsApp Group'),
        ('spreadsheet', 'Excel / Spreadsheet'),
        ('paper_ledger', 'Paper Ledger / Notebook'),
        ('bank_app', 'Bank App Only'),
        ('other', 'Other'),
    ]

    STATUS_CHOICES = [
        ('NEW', '1. New (Captured)'),
        ('CONTACTED', '2. Contacted'),
        ('QUALIFIED', '3. Qualified'),
        ('DEMO_BOOKED', '4. Demo Booked'),
        ('DEMO_COMPLETED', '5. Demo Completed'),
        ('SIGNUP_STARTED', '6. Signup Started'),
        ('REGISTERED', '7. Registered'),
        ('GROUP_CREATED_OR_JOINED', '8. Group Created / Joined'),
        ('ACTIVATED', '9. Activated'),
        ('NURTURE', '10. Nurture (Not Ready)'),
        ('LOST', '11. Lost'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
    ]

    CONSENT_CHOICES = [
        ('yes', 'Consent Given'),
        ('no', 'Consent Declined'),
        ('unknown', 'Unknown / Not Captured'),
    ]

    LOSS_REASON_CHOICES = [
        ('not_interested', 'Not Interested'),
        ('no_response', 'No Response / Unreachable'),
        ('wrong_fit', 'Wrong Fit / Out of Scope'),
        ('technical_barrier', 'Technical Barrier / Digital Literacy'),
        ('trust_concern', 'Trust / Security Concern'),
        ('pricing_concern', 'Pricing Concern'),
        ('timing', 'Bad Timing / Try Later'),
        ('duplicate', 'Duplicate Record'),
        ('other', 'Other Reason'),
    ]

    # Core Identifiers
    lead_type = models.CharField(max_length=20, choices=LEAD_TYPE_CHOICES, default='community')
    name = models.CharField(max_length=150, help_text="Community, Group, or Organisation Name")
    primary_contact_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True, null=True)
    preferred_contact_method = models.CharField(max_length=20, choices=PREFERRED_METHOD_CHOICES, default='whatsapp')
    area_or_location = models.CharField(max_length=150, blank=True, help_text="Township, suburb, or city (e.g. Soweto, Umlazi)")

    # Community Profile
    community_type = models.CharField(max_length=30, choices=COMMUNITY_TYPE_CHOICES, default='stokvel')
    estimated_member_count = models.PositiveIntegerField(null=True, blank=True)
    current_process_or_tool = models.CharField(max_length=30, choices=CURRENT_PROCESS_CHOICES, default='whatsapp')
    main_pain_point = models.TextField(blank=True)

    # Attribution & Pipeline
    source = models.ForeignKey(AcquisitionSource, on_delete=models.SET_NULL, null=True, blank=True, related_name='leads')
    source_detail = models.CharField(max_length=150, blank=True, help_text="Specific event, QR code or referrer details")
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_crm_leads'
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='NEW')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='normal')

    # POPIA, Consent & Compliance
    consent_to_contact = models.CharField(max_length=10, choices=CONSENT_CHOICES, default='unknown')
    consent_captured_at = models.DateTimeField(null=True, blank=True)
    consent_method = models.CharField(max_length=50, blank=True, help_text="e.g. Verbal in field, QR Opt-in, WhatsApp confirmation")
    do_not_contact = models.BooleanField(default=False, help_text="Strict POPIA opt-out flag")

    # Dates & Conversions
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    next_follow_up_at = models.DateTimeField(null=True, blank=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    converted_komunity_group = models.ForeignKey(
        'chema.Group',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='crm_leads'
    )
    is_manually_activated = models.BooleanField(default=False)

    # Loss Tracking
    loss_reason = models.CharField(max_length=30, choices=LOSS_REASON_CHOICES, null=True, blank=True)
    loss_reason_detail = models.TextField(blank=True)

    # Audit & Tracking
    is_duplicate_flag = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_crm_leads'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['assigned_to']),
            models.Index(fields=['next_follow_up_at']),
            models.Index(fields=['phone']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.name} [{self.get_status_display()}]"

    def clean(self):
        super().clean()
        if self.status == 'LOST':
            if not self.loss_reason:
                raise ValidationError({'loss_reason': "A loss reason is mandatory when marking a lead as LOST."})
            if self.loss_reason == 'other' and not self.loss_reason_detail.strip():
                raise ValidationError({'loss_reason_detail': "Please provide details explaining why this lead is lost."})
        if self.phone:
            self.phone = normalize_phone(self.phone)

    @property
    def normalized_phone(self):
        return normalize_phone(self.phone)

    @property
    def is_active_community(self):
        """Checks if lead meets activation criteria (5+ members & activity within 14 days or manual verify)."""
        if self.is_manually_activated:
            return True
        if not self.converted_komunity_group:
            return False
        group = self.converted_komunity_group
        active_members_count = group.get_total_members()
        if active_members_count < 5:
            return False
        # Check action in 14 days
        cutoff = timezone.now() - timezone.timedelta(days=14)
        has_post = group.post_set.filter(created_at__gte=cutoff).exists() if hasattr(group, 'post_set') else False
        from wallet.models import Transaction
        has_tx = Transaction.objects.filter(destination_group=group, timestamp__gte=cutoff, status='COMPLETED').exists()
        return has_post or has_tx

    @property
    def next_open_task(self):
        return self.tasks.filter(status='open').order_by('due_at').first()

    @property
    def is_overdue(self):
        if self.next_follow_up_at and self.next_follow_up_at < timezone.now():
            return True
        open_task = self.next_open_task
        if open_task and open_task.is_overdue:
            return True
        return False


class Contact(models.Model):
    """Optional individual contact associated with a lead/community."""

    ROLE_CHOICES = [
        ('chairperson', 'Chairperson'),
        ('treasurer', 'Treasurer'),
        ('secretary', 'Secretary'),
        ('pastor', 'Pastor / Minister'),
        ('coordinator', 'Coordinator / Organiser'),
        ('member', 'Member / Elder'),
        ('other', 'Other'),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='contacts')
    name = models.CharField(max_length=150)
    role = models.CharField(max_length=30, choices=ROLE_CHOICES, default='chairperson')
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True, null=True)
    preferred_contact_method = models.CharField(max_length=20, choices=Lead.PREFERRED_METHOD_CHOICES, default='whatsapp')
    is_primary = models.BooleanField(default=False)
    consent_to_contact = models.CharField(max_length=10, choices=Lead.CONSENT_CHOICES, default='unknown')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-is_primary', 'name']

    def __str__(self):
        return f"{self.name} ({self.get_role_display()}) — {self.lead.name}"

    def clean(self):
        super().clean()
        if self.phone:
            self.phone = normalize_phone(self.phone)


class Activity(models.Model):
    """Append-only interaction and event timeline record."""

    ACTIVITY_TYPE_CHOICES = [
        ('call', 'Phone Call'),
        ('whatsapp', 'WhatsApp Chat / Voice Note'),
        ('in_person_visit', 'In-Person Field Visit'),
        ('demo', 'Product Demo'),
        ('meeting', 'Meeting / AGM'),
        ('note', 'Internal Note'),
        ('status_change', 'Pipeline Stage Change'),
        ('owner_reassigned', 'Lead Reassigned'),
        ('system', 'System Event'),
    ]

    OUTCOME_CHOICES = [
        ('no_answer', 'No Answer / Unreachable'),
        ('interested', 'Interested'),
        ('demo_booked', 'Demo Booked'),
        ('follow_up_needed', 'Follow-up Needed'),
        ('not_interested', 'Not Interested'),
        ('converted', 'Converted / Activated'),
        ('completed', 'Action Completed'),
        ('other', 'Other'),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='activities')
    activity_type = models.CharField(max_length=30, choices=ACTIVITY_TYPE_CHOICES, default='call')
    subject = models.CharField(max_length=200)
    summary = models.TextField()
    outcome = models.CharField(max_length=30, choices=OUTCOME_CHOICES, default='follow_up_needed')
    occurred_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    next_action = models.CharField(max_length=200, blank=True)
    next_action_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-occurred_at', '-created_at']
        verbose_name_plural = 'Activities'

    def __str__(self):
        return f"[{self.get_activity_type_display()}] {self.subject} — {self.lead.name}"


class FollowUpTask(models.Model):
    """Follow-up action item assigned to an agent."""

    TASK_TYPE_CHOICES = [
        ('call', 'Follow-up Call'),
        ('whatsapp', 'WhatsApp Follow-up'),
        ('visit', 'Field Visit'),
        ('demo', 'Product Demo Session'),
        ('document_collection', 'Collect KYC / Constitution'),
        ('activation_check', 'Check Group Activity / Activation'),
        ('other', 'Other Task'),
    ]

    STATUS_CHOICES = [
        ('open', 'Open'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('normal', 'Normal'),
        ('high', 'High'),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    task_type = models.CharField(max_length=30, choices=TASK_TYPE_CHOICES, default='call')
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='crm_tasks'
    )
    due_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='normal')

    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='completed_crm_tasks'
    )
    completion_note = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_crm_tasks'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['status', 'due_at']

    def __str__(self):
        return f"{self.title} ({self.get_status_display()}) — {self.lead.name}"

    @property
    def is_overdue(self):
        return self.status == 'open' and self.due_at < timezone.now()


class LeadAuditLog(models.Model):
    """Immutable audit trail for status and owner changes on a lead."""

    ACTION_CHOICES = [
        ('status_change', 'Pipeline Stage Change'),
        ('reassigned', 'Owner Reassigned'),
        ('activated', 'Community Activated'),
        ('marked_lost', 'Marked as Lost'),
        ('opt_out', 'POPIA Opt-Out Enforced'),
        ('edit', 'Lead Details Modified'),
    ]

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name='audit_logs')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    old_value = models.CharField(max_length=200, blank=True)
    new_value = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"[{self.action}] {self.lead.name}: {self.old_value} -> {self.new_value} ({self.timestamp:%Y-%m-%d %H:%M})"
