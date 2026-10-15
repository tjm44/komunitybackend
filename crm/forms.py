from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import Lead, Contact, Activity, FollowUpTask, AcquisitionSource, normalize_phone

User = get_user_model()


class QuickCaptureForm(forms.Form):
    """
    Fast, mobile-first quick capture form for field agents.
    Designed for under 1 minute field data collection.
    """
    name = forms.CharField(
        label="Community / Group Name",
        max_length=150,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Masibambane Burial Society',
            'class': 'form-input',
            'required': True,
            'autofocus': True,
        })
    )
    primary_contact_name = forms.CharField(
        label="Primary Contact Name",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Mam Nokuthula (Chairperson)',
            'class': 'form-input',
        })
    )
    phone = forms.CharField(
        label="Phone Number (WhatsApp)",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': '082 123 4567',
            'class': 'form-input',
            'type': 'tel',
        })
    )
    community_type = forms.ChoiceField(
        label="Group Type",
        choices=Lead.COMMUNITY_TYPE_CHOICES,
        initial='stokvel',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    area_or_location = forms.CharField(
        label="Area / Township",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Soweto, Meadowlands',
            'class': 'form-input',
        })
    )
    source = forms.ModelChoiceField(
        label="Campaign / Source",
        queryset=AcquisitionSource.objects.filter(is_active=True),
        required=False,
        empty_label="— Select Source / Campaign (Optional) —",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    source_detail = forms.CharField(
        label="Source Detail / QR / Event",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Zone 4 Hall Activation',
            'class': 'form-input',
        })
    )
    consent_to_contact = forms.ChoiceField(
        label="Consent to Contact (POPIA)",
        choices=Lead.CONSENT_CHOICES,
        initial='yes',
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    initial_note = forms.CharField(
        label="Quick Field Note",
        required=False,
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Key pain point, meeting schedule, or observations...',
            'class': 'form-textarea',
        })
    )
    next_follow_up_date = forms.DateTimeField(
        label="Next Follow-up Due (Optional)",
        required=False,
        widget=forms.DateTimeInput(attrs={
            'type': 'datetime-local',
            'class': 'form-input',
        })
    )

    def clean(self):
        cleaned_data = super().clean()
        phone = cleaned_data.get('phone')
        contact_name = cleaned_data.get('primary_contact_name')

        if not phone and not contact_name:
            raise forms.ValidationError("Please provide at least a contact name or phone number.")

        if phone:
            cleaned_data['phone'] = normalize_phone(phone)

        return cleaned_data


class LeadForm(forms.ModelForm):
    """Full create and edit form for Lead objects."""

    class Meta:
        model = Lead
        fields = [
            'name',
            'lead_type',
            'primary_contact_name',
            'phone',
            'email',
            'preferred_contact_method',
            'area_or_location',
            'community_type',
            'estimated_member_count',
            'current_process_or_tool',
            'main_pain_point',
            'source',
            'source_detail',
            'assigned_to',
            'status',
            'priority',
            'consent_to_contact',
            'consent_method',
            'do_not_contact',
            'next_follow_up_at',
            'loss_reason',
            'loss_reason_detail',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input'}),
            'lead_type': forms.Select(attrs={'class': 'form-select'}),
            'primary_contact_name': forms.TextInput(attrs={'class': 'form-input'}),
            'phone': forms.TextInput(attrs={'class': 'form-input', 'type': 'tel'}),
            'email': forms.EmailInput(attrs={'class': 'form-input'}),
            'preferred_contact_method': forms.Select(attrs={'class': 'form-select'}),
            'area_or_location': forms.TextInput(attrs={'class': 'form-input'}),
            'community_type': forms.Select(attrs={'class': 'form-select'}),
            'estimated_member_count': forms.NumberInput(attrs={'class': 'form-input'}),
            'current_process_or_tool': forms.Select(attrs={'class': 'form-select'}),
            'main_pain_point': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3}),
            'source': forms.Select(attrs={'class': 'form-select'}),
            'source_detail': forms.TextInput(attrs={'class': 'form-input'}),
            'assigned_to': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'consent_to_contact': forms.Select(attrs={'class': 'form-select'}),
            'consent_method': forms.TextInput(attrs={'class': 'form-input'}),
            'do_not_contact': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'next_follow_up_at': forms.DateTimeInput(attrs={'class': 'form-input', 'type': 'datetime-local'}),
            'loss_reason': forms.Select(attrs={'class': 'form-select'}),
            'loss_reason_detail': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only show active staff users in assignment dropdown
        self.fields['assigned_to'].queryset = User.objects.filter(is_active=True, is_staff=True).order_by('email', 'phone')


class LeadStatusChangeForm(forms.Form):
    """Form to transition a lead to another pipeline stage."""
    status = forms.ChoiceField(
        choices=Lead.STATUS_CHOICES,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'status-select'})
    )
    loss_reason = forms.ChoiceField(
        choices=[('', '— Select Loss Reason (If marking Lost) —')] + Lead.LOSS_REASON_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select', 'id': 'loss-reason-select'})
    )
    loss_reason_detail = forms.CharField(
        label="Loss Reason Details (Required if Other)",
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'id': 'loss-reason-detail'})
    )
    notes = forms.CharField(
        label="Transition Notes / Context",
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2})
    )

    def clean(self):
        cleaned_data = super().clean()
        status = cleaned_data.get('status')
        loss_reason = cleaned_data.get('loss_reason')
        loss_reason_detail = cleaned_data.get('loss_reason_detail')

        if status == 'LOST':
            if not loss_reason:
                raise forms.ValidationError("Please select a loss reason.")
            if loss_reason == 'other' and not (loss_reason_detail and loss_reason_detail.strip()):
                raise forms.ValidationError("Please explain why this lead is lost when selecting 'Other'.")

        return cleaned_data


class ActivityLogForm(forms.ModelForm):
    """Form to log an interaction on the lead timeline."""

    class Meta:
        model = Activity
        fields = [
            'activity_type',
            'subject',
            'summary',
            'outcome',
            'occurred_at',
            'next_action',
            'next_action_date',
        ]
        widgets = {
            'activity_type': forms.Select(attrs={'class': 'form-select'}),
            'subject': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Call with Treasurer regarding banking'}),
            'summary': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 3, 'placeholder': 'Details of conversation, concerns raised, agreements...'}),
            'outcome': forms.Select(attrs={'class': 'form-select'}),
            'occurred_at': forms.DateTimeInput(attrs={'class': 'form-input', 'type': 'datetime-local'}),
            'next_action': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Schedule demo with committee'}),
            'next_action_date': forms.DateTimeInput(attrs={'class': 'form-input', 'type': 'datetime-local'}),
        }


class FollowUpTaskForm(forms.ModelForm):
    """Form to schedule a follow up task."""

    class Meta:
        model = FollowUpTask
        fields = [
            'title',
            'task_type',
            'due_at',
            'priority',
            'assigned_to',
            'description',
        ]
        widgets = {
            'title': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Follow up on demo feedback'}),
            'task_type': forms.Select(attrs={'class': 'form-select'}),
            'due_at': forms.DateTimeInput(attrs={'class': 'form-input', 'type': 'datetime-local'}),
            'priority': forms.Select(attrs={'class': 'form-select'}),
            'assigned_to': forms.Select(attrs={'class': 'form-select'}),
            'description': forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['assigned_to'].queryset = User.objects.filter(is_active=True, is_staff=True).order_by('email', 'phone')


class TaskCompleteForm(forms.Form):
    """Form to mark a task as completed."""
    completion_note = forms.CharField(
        label="Completion Notes",
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'Result of this action...'})
    )
    create_next_action = forms.CharField(
        label="Schedule Next Task (Optional)",
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Confirm group registration'})
    )
    next_action_date = forms.DateTimeField(
        label="Next Due Date",
        required=False,
        widget=forms.DateTimeInput(attrs={'class': 'form-input', 'type': 'datetime-local'})
    )


class ReassignLeadForm(forms.Form):
    """Form to reassign lead ownership."""
    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True, is_staff=True),
        empty_label="— Unassigned —",
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Reason for reassignment'})
    )


class AmbassadorOnboardingForm(forms.Form):
    """Form to register and onboard a new Field Ambassador directly from the Admin Portal."""
    first_name = forms.CharField(
        label="First Name",
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Thabo'})
    )
    last_name = forms.CharField(
        label="Last Name / Surname",
        max_length=150,
        required=True,
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Molefe'})
    )
    phone = forms.CharField(
        label="Mobile Phone (Login Username)",
        max_length=30,
        required=True,
        help_text="Standard South African mobile number (e.g. 082 123 4567 or +27821234567)",
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': '082 123 4567'})
    )
    email = forms.EmailField(
        label="Email Address (Optional)",
        required=False,
        widget=forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'ambassador@komunity.co.za'})
    )
    territory = forms.CharField(
        label="Assigned Territory / Area",
        max_length=150,
        required=False,
        help_text="Township, suburb or city (e.g. Soweto, Umlazi, Khayelitsha, Tembisa)",
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'e.g. Soweto / Diepkloof'})
    )
    campaign_source = forms.ModelChoiceField(
        label="Assigned Campaign / Source Channel",
        queryset=AcquisitionSource.objects.filter(is_active=True),
        required=False,
        empty_label="— Standard Field Visit —",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    password = forms.CharField(
        label="Initial Password / PIN",
        max_length=100,
        required=False,
        help_text="Leave blank to automatically generate a secure password for this ambassador.",
        widget=forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Leave blank to auto-generate'})
    )
    notes = forms.CharField(
        label="Field Experience / Language Notes",
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-textarea', 'rows': 2, 'placeholder': 'Languages spoken, local network, stokvel ties...'})
    )

    def clean_phone(self):
        raw = self.cleaned_data.get('phone', '')
        normalized = normalize_phone(raw)
        if not normalized or len(normalized) < 10:
            raise forms.ValidationError("Please enter a valid 10-digit South African mobile number.")
        if User.objects.filter(phone=normalized).exists():
            raise forms.ValidationError("A user account with this mobile number already exists.")
        return normalized

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if email and User.objects.filter(email__iexact=email.strip()).exists():
            raise forms.ValidationError("A user account with this email address already exists.")
        return email

