from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import Lead, Activity, FollowUpTask, LeadAuditLog, normalize_phone


# Controlled pipeline transitions and validation
VALID_PIPELINE_STAGES = [
    'NEW',
    'CONTACTED',
    'QUALIFIED',
    'DEMO_BOOKED',
    'DEMO_COMPLETED',
    'SIGNUP_STARTED',
    'REGISTERED',
    'GROUP_CREATED_OR_JOINED',
    'ACTIVATED',
    'NURTURE',
    'LOST',
]


def check_potential_duplicates(phone=None, email=None, name=None, area=None, exclude_lead_id=None):
    """
    Identifies potential duplicate leads based on normalized phone, email,
    or similar community name + location.
    """
    matches = Lead.objects.none()
    clauses = Q()

    normalized = normalize_phone(phone) if phone else ""
    if normalized and len(normalized) >= 7:
        clauses |= Q(phone__icontains=normalized)

    if email and email.strip():
        clauses |= Q(email__iexact=email.strip())

    if name and len(name.strip()) >= 3:
        name_clause = Q(name__iexact=name.strip())
        if area and area.strip():
            name_clause &= Q(area_or_location__iexact=area.strip())
        clauses |= name_clause

    if clauses:
        qs = Lead.objects.filter(clauses)
        if exclude_lead_id:
            qs = qs.exclude(id=exclude_lead_id)
        return qs.distinct()

    return matches


@transaction.atomic
def change_lead_status(lead, new_status, user=None, loss_reason=None, loss_reason_detail="", notes=""):
    """
    Transitions a lead to a new pipeline stage with full validation,
    audit trail logging, and activity timeline creation.
    """
    if new_status not in VALID_PIPELINE_STAGES:
        raise ValidationError(f"Invalid pipeline status: '{new_status}'")

    old_status = lead.status
    if old_status == new_status:
        return lead

    # If transitioning to LOST, validate reason
    if new_status == 'LOST':
        if not loss_reason:
            raise ValidationError("A loss reason is mandatory when marking a lead as LOST.")
        if loss_reason == 'other' and not loss_reason_detail.strip():
            raise ValidationError("Please provide details for 'Other' loss reason.")
        lead.loss_reason = loss_reason
        lead.loss_reason_detail = loss_reason_detail
    else:
        # Clear loss reasons if moving out of LOST (e.g. manager correction)
        if old_status == 'LOST':
            lead.loss_reason = None
            lead.loss_reason_detail = ""

    # Check activation preconditions if setting ACTIVATED directly
    if new_status == 'ACTIVATED':
        if not lead.is_active_community and not lead.is_manually_activated:
            raise ValidationError("Lead cannot be marked ACTIVATED without a verified linked group (>=5 members, recent activity) or manager verification.")
        if not lead.converted_at:
            lead.converted_at = timezone.now()

    if new_status in ['REGISTERED', 'GROUP_CREATED_OR_JOINED'] and not lead.converted_at:
        lead.converted_at = timezone.now()

    lead.status = new_status
    lead.full_clean()
    lead.save(update_fields=['status', 'loss_reason', 'loss_reason_detail', 'converted_at', 'updated_at'])

    # Create immutable audit log
    LeadAuditLog.objects.create(
        lead=lead,
        changed_by=user,
        action='marked_lost' if new_status == 'LOST' else 'status_change',
        old_value=old_status,
        new_value=new_status,
        notes=notes or (f"Reason: {loss_reason}" if new_status == 'LOST' else f"Stage changed to {new_status}")
    )

    # Append activity to timeline
    Activity.objects.create(
        lead=lead,
        activity_type='status_change',
        subject=f"Pipeline stage updated: {old_status} → {new_status}",
        summary=notes or f"Moved from {old_status} to {new_status}." + (f" Loss reason: {loss_reason}." if new_status == 'LOST' else ""),
        outcome='not_interested' if new_status == 'LOST' else ('converted' if new_status == 'ACTIVATED' else 'completed'),
        occurred_at=timezone.now(),
        created_by=user
    )

    return lead


@transaction.atomic
def reassign_lead(lead, new_assignee, reassigned_by=None, reason=""):
    """
    Reassigns lead to a new agent or manager, recording an audit log and activity timeline event.
    """
    old_assignee = lead.assigned_to
    if old_assignee == new_assignee:
        return lead

    old_name = old_assignee.email or old_assignee.phone if old_assignee else "Unassigned"
    new_name = new_assignee.email or new_assignee.phone if new_assignee else "Unassigned"

    lead.assigned_to = new_assignee
    lead.save(update_fields=['assigned_to', 'updated_at'])

    LeadAuditLog.objects.create(
        lead=lead,
        changed_by=reassigned_by,
        action='reassigned',
        old_value=str(old_name),
        new_value=str(new_name),
        notes=reason
    )

    Activity.objects.create(
        lead=lead,
        activity_type='owner_reassigned',
        subject=f"Owner changed: {old_name} → {new_name}",
        summary=reason or f"Lead assigned to {new_name}.",
        outcome='completed',
        occurred_at=timezone.now(),
        created_by=reassigned_by
    )

    return lead


@transaction.atomic
def log_interaction(lead, activity_type, subject, summary, outcome, occurred_at=None, user=None, next_action="", next_action_date=None):
    """
    Appends an interaction record to the timeline and updates lead contact timestamps.
    """
    occurred_at = occurred_at or timezone.now()

    activity = Activity.objects.create(
        lead=lead,
        activity_type=activity_type,
        subject=subject,
        summary=summary,
        outcome=outcome,
        occurred_at=occurred_at,
        created_by=user,
        next_action=next_action,
        next_action_date=next_action_date
    )

    # Update lead last_contacted_at if this was a contact attempt/interaction
    if activity_type in ['call', 'whatsapp', 'in_person_visit', 'demo', 'meeting']:
        lead.last_contacted_at = occurred_at
        # If lead was NEW, automatically progress to CONTACTED
        if lead.status == 'NEW':
            change_lead_status(lead, 'CONTACTED', user=user, notes=f"Automatic transition after first interaction: {subject}")

    if next_action_date:
        lead.next_follow_up_at = next_action_date

    lead.save(update_fields=['last_contacted_at', 'next_follow_up_at', 'updated_at'])

    # If next action is specified with a date, optionally create a FollowUpTask
    if next_action and next_action_date:
        FollowUpTask.objects.create(
            lead=lead,
            title=next_action,
            due_at=next_action_date,
            task_type='call' if activity_type == 'call' else ('whatsapp' if activity_type == 'whatsapp' else 'other'),
            assigned_to=lead.assigned_to or user,
            created_by=user,
            priority=lead.priority
        )

    return activity


@transaction.atomic
def complete_task(task, user=None, completion_note="", create_next_action="", next_action_date=None):
    """
    Marks a follow-up task as completed and optionally creates the next action.
    """
    task.status = 'completed'
    task.completed_at = timezone.now()
    task.completed_by = user
    task.completion_note = completion_note
    task.save(update_fields=['status', 'completed_at', 'completed_by', 'completion_note', 'updated_at'])

    # Append activity
    Activity.objects.create(
        lead=task.lead,
        activity_type='note',
        subject=f"Follow-up task completed: {task.title}",
        summary=completion_note or f"Task '{task.title}' marked as completed.",
        outcome='completed',
        occurred_at=timezone.now(),
        created_by=user
    )

    # Next action if provided
    if create_next_action and next_action_date:
        FollowUpTask.objects.create(
            lead=task.lead,
            title=create_next_action,
            due_at=next_action_date,
            assigned_to=task.lead.assigned_to or user,
            created_by=user,
            priority=task.lead.priority
        )
        task.lead.next_follow_up_at = next_action_date
        task.lead.save(update_fields=['next_follow_up_at', 'updated_at'])

    return task


@transaction.atomic
def manually_activate_lead(lead, user=None, group=None, notes=""):
    """
    Allows a manager to manually verify and activate a community lead.
    """
    if group:
        lead.converted_komunity_group = group
    lead.is_manually_activated = True
    lead.status = 'ACTIVATED'
    lead.converted_at = timezone.now()
    lead.save(update_fields=['is_manually_activated', 'converted_komunity_group', 'status', 'converted_at', 'updated_at'])

    LeadAuditLog.objects.create(
        lead=lead,
        changed_by=user,
        action='activated',
        old_value='Manual Verification',
        new_value='ACTIVATED',
        notes=notes or "Manually activated by manager"
    )

    Activity.objects.create(
        lead=lead,
        activity_type='status_change',
        subject="Community Activated (Manual Verification)",
        summary=notes or "Manager verified community activation.",
        outcome='converted',
        occurred_at=timezone.now(),
        created_by=user
    )

    return lead
