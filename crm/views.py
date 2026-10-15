from datetime import timedelta
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

import random
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group as DjangoGroup
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils.decorators import method_decorator

from chema.models import Group
from .forms import (
    ActivityLogForm,
    AmbassadorOnboardingForm,
    FollowUpTaskForm,
    LeadForm,
    LeadStatusChangeForm,
    QuickCaptureForm,
    ReassignLeadForm,
    TaskCompleteForm,
)
from .models import AcquisitionSource, Activity, Contact, FollowUpTask, Lead, LeadAuditLog
from .permissions import (
    can_edit_lead,
    can_reassign_leads,
    crm_access_required,
    filter_leads_for_user,
    filter_tasks_for_user,
    get_user_crm_role,
    is_crm_admin,
    is_crm_manager,
    is_field_agent,
)
from .services import (
    change_lead_status,
    check_potential_duplicates,
    complete_task,
    log_interaction,
    manually_activate_lead,
    reassign_lead,
)

crm_required = method_decorator(crm_access_required, name='dispatch')


@crm_required
class CRMDashboardView(View):
    """
    Overview analytics dashboard for the community acquisition team.
    Shows pipeline funnel, overdue tasks, key conversion metrics, and recent activities.
    """
    def get(self, request):
        user = request.user
        leads_qs = filter_leads_for_user(Lead.objects.all(), user)

        # Date range filter (default: last 30 days)
        days = int(request.GET.get('days', 30))
        since_date = timezone.now() - timedelta(days=days)

        # High-level pipeline stage metrics
        total_leads = leads_qs.count()
        new_leads_count = leads_qs.filter(created_at__gte=since_date).count()
        contacted_count = leads_qs.filter(status__in=[
            'CONTACTED', 'QUALIFIED', 'DEMO_BOOKED', 'DEMO_COMPLETED',
            'SIGNUP_STARTED', 'REGISTERED', 'GROUP_CREATED_OR_JOINED', 'ACTIVATED'
        ]).count()

        demos_booked = leads_qs.filter(status__in=['DEMO_BOOKED', 'DEMO_COMPLETED', 'SIGNUP_STARTED', 'REGISTERED', 'GROUP_CREATED_OR_JOINED', 'ACTIVATED']).count()
        demos_completed = leads_qs.filter(status__in=['DEMO_COMPLETED', 'SIGNUP_STARTED', 'REGISTERED', 'GROUP_CREATED_OR_JOINED', 'ACTIVATED']).count()
        registered_count = leads_qs.filter(status__in=['REGISTERED', 'GROUP_CREATED_OR_JOINED', 'ACTIVATED']).count()
        activated_count = leads_qs.filter(status='ACTIVATED').count()
        lost_count = leads_qs.filter(status='LOST').count()

        # Follow-up health
        now = timezone.now()
        user_tasks = filter_tasks_for_user(FollowUpTask.objects.all(), user)
        overdue_tasks_count = user_tasks.filter(status='open', due_at__lt=now).count()
        due_today_tasks_count = user_tasks.filter(
            status='open',
            due_at__date=now.date(),
            due_at__gte=now
        ).count()

        # Leads without next action
        leads_without_next_action = leads_qs.filter(
            status__in=['NEW', 'CONTACTED', 'QUALIFIED', 'DEMO_BOOKED', 'DEMO_COMPLETED', 'SIGNUP_STARTED', 'REGISTERED', 'GROUP_CREATED_OR_JOINED']
        ).exclude(
            tasks__status='open'
        ).count()

        # Conversion rates
        activation_rate = round((activated_count / registered_count * 100), 1) if registered_count > 0 else 0.0
        demo_completion_rate = round((demos_completed / demos_booked * 100), 1) if demos_booked > 0 else 0.0

        # Pipeline stage distribution
        pipeline_stages = []
        for code, label in Lead.STATUS_CHOICES:
            stage_count = leads_qs.filter(status=code).count()
            pct = round((stage_count / total_leads * 100), 1) if total_leads > 0 else 0
            pipeline_stages.append({
                'code': code,
                'label': label,
                'count': stage_count,
                'percentage': pct,
            })

        # Recent activities
        recent_activities = Activity.objects.filter(lead__in=leads_qs).select_related('lead', 'created_by')[:10]

        # Overdue / urgent tasks for user
        urgent_tasks = user_tasks.filter(status='open', due_at__lt=now).select_related('lead', 'assigned_to')[:5]

        context = {
            'page': 'crm_dashboard',
            'user_role': get_user_crm_role(user),
            'days': days,
            'total_leads': total_leads,
            'new_leads_count': new_leads_count,
            'contacted_count': contacted_count,
            'demos_booked': demos_booked,
            'demos_completed': demos_completed,
            'registered_count': registered_count,
            'activated_count': activated_count,
            'lost_count': lost_count,
            'overdue_tasks_count': overdue_tasks_count,
            'due_today_tasks_count': due_today_tasks_count,
            'leads_without_next_action': leads_without_next_action,
            'activation_rate': activation_rate,
            'demo_completion_rate': demo_completion_rate,
            'pipeline_stages': pipeline_stages,
            'recent_activities': recent_activities,
            'urgent_tasks': urgent_tasks,
        }
        return render(request, 'crm/dashboard.html', context)


@crm_required
class LeadListView(View):
    """
    Filterable, searchable list of acquisition leads with status markers and pagination.
    """
    def get(self, request):
        user = request.user
        qs = filter_leads_for_user(Lead.objects.all(), user).select_related('assigned_to', 'source')

        # Filters
        query = request.GET.get('q', '').strip()
        status_filter = request.GET.get('status', '').strip()
        community_type = request.GET.get('community_type', '').strip()
        priority = request.GET.get('priority', '').strip()
        assigned_to_id = request.GET.get('assigned_to', '').strip()
        overdue_only = request.GET.get('overdue', '') == '1'
        no_tasks_only = request.GET.get('no_tasks', '') == '1'

        if query:
            qs = qs.filter(
                Q(name__icontains=query) |
                Q(primary_contact_name__icontains=query) |
                Q(phone__icontains=query) |
                Q(area_or_location__icontains=query)
            )

        if status_filter:
            qs = qs.filter(status=status_filter)

        if community_type:
            qs = qs.filter(community_type=community_type)

        if priority:
            qs = qs.filter(priority=priority)

        if assigned_to_id:
            if assigned_to_id == 'unassigned':
                qs = qs.filter(assigned_to__isnull=True)
            else:
                qs = qs.filter(assigned_to_id=assigned_to_id)

        now = timezone.now()
        if overdue_only:
            qs = qs.filter(
                Q(next_follow_up_at__lt=now) |
                Q(tasks__status='open', tasks__due_at__lt=now)
            ).distinct()

        if no_tasks_only:
            qs = qs.exclude(tasks__status='open').exclude(status__in=['ACTIVATED', 'LOST'])

        # Pagination
        paginator = Paginator(qs, 20)
        page_num = request.GET.get('page', 1)
        page_obj = paginator.get_page(page_num)

        context = {
            'page': 'crm_leads',
            'leads': page_obj,
            'total_count': paginator.count,
            'query': query,
            'status_filter': status_filter,
            'community_type_filter': community_type,
            'priority_filter': priority,
            'assigned_to_filter': assigned_to_id,
            'overdue_only': overdue_only,
            'no_tasks_only': no_tasks_only,
            'status_choices': Lead.STATUS_CHOICES,
            'community_type_choices': Lead.COMMUNITY_TYPE_CHOICES,
            'priority_choices': Lead.PRIORITY_CHOICES,
            'can_reassign': can_reassign_leads(user),
        }
        return render(request, 'crm/lead_list.html', context)


@crm_required
class LeadDetailView(View):
    """
    Detailed profile view for a lead with timeline, tasks, linked group, and quick actions.
    """
    def get(self, request, pk):
        user = request.user
        lead = get_object_or_404(Lead, pk=pk)

        # Scoping check: agents may only view assigned/created
        if is_field_agent(user) and not user.is_staff:
            if lead.assigned_to_id != user.id and lead.created_by_id != user.id:
                messages.error(request, "You do not have permission to view this lead.")
                return redirect('crm:lead_list')

        activities = lead.activities.select_related('created_by').all()
        tasks = lead.tasks.select_related('assigned_to').all()
        contacts = lead.contacts.all()
        audit_logs = lead.audit_logs.select_related('changed_by')[:20]

        # Forms for quick actions
        status_form = LeadStatusChangeForm(initial={
            'status': lead.status,
            'loss_reason': lead.loss_reason or '',
            'loss_reason_detail': lead.loss_reason_detail or '',
        })
        activity_form = ActivityLogForm(initial={'occurred_at': timezone.now().strftime('%Y-%m-%dT%H:%M')})
        task_form = FollowUpTaskForm(initial={'due_at': (timezone.now() + timedelta(days=2)).strftime('%Y-%m-%dT%H:%M')})
        reassign_form = ReassignLeadForm(initial={'assigned_to': lead.assigned_to})
        complete_form = TaskCompleteForm()

        # Potential duplicate check
        duplicates = check_potential_duplicates(
            phone=lead.phone,
            email=lead.email,
            name=lead.name,
            area=lead.area_or_location,
            exclude_lead_id=lead.id
        )

        # Available Komunity Groups for linkage (if registered or qualified)
        available_groups = Group.objects.filter(is_active=True).order_by('-date')[:30]

        context = {
            'page': 'crm_leads',
            'lead': lead,
            'activities': activities,
            'tasks': tasks,
            'contacts': contacts,
            'audit_logs': audit_logs,
            'duplicates': duplicates,
            'status_form': status_form,
            'activity_form': activity_form,
            'task_form': task_form,
            'reassign_form': reassign_form,
            'complete_form': complete_form,
            'available_groups': available_groups,
            'can_edit': can_edit_lead(user, lead),
            'can_reassign': can_reassign_leads(user),
            'is_manager': is_crm_manager(user),
        }
        return render(request, 'crm/lead_detail.html', context)


@crm_required
class QuickCaptureView(View):
    """
    Mobile-first quick capture screen for field agents to capture a community in < 1 minute.
    """
    def get(self, request):
        form = QuickCaptureForm()
        # Pre-fill source from URL parameters if available (e.g. ?campaign=XYZ or ?source=1)
        source_id = request.GET.get('source')
        source_code = request.GET.get('code')
        if source_id:
            try:
                form.fields['source'].initial = AcquisitionSource.objects.get(pk=source_id)
            except AcquisitionSource.DoesNotExist:
                pass
        elif source_code:
            source = AcquisitionSource.objects.filter(campaign_code=source_code).first()
            if source:
                form.fields['source'].initial = source

        context = {
            'page': 'crm_quick_capture',
            'form': form,
        }
        return render(request, 'crm/quick_capture.html', context)

    def post(self, request):
        form = QuickCaptureForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            phone = data.get('phone', '')
            name = data.get('name')
            area = data.get('area_or_location', '')

            # Check potential duplicates
            duplicates = check_potential_duplicates(phone=phone, name=name, area=area)
            has_duplicates = duplicates.exists()

            # Create lead
            lead = Lead.objects.create(
                name=name,
                primary_contact_name=data.get('primary_contact_name', ''),
                phone=phone,
                community_type=data.get('community_type', 'stokvel'),
                area_or_location=area,
                source=data.get('source'),
                source_detail=data.get('source_detail', ''),
                consent_to_contact=data.get('consent_to_contact', 'yes'),
                consent_captured_at=timezone.now() if data.get('consent_to_contact') == 'yes' else None,
                consent_method="Field Quick-Capture",
                assigned_to=request.user if request.user.is_staff else None,
                created_by=request.user,
                is_duplicate_flag=has_duplicates,
                next_follow_up_at=data.get('next_follow_up_date'),
            )

            # Log initial field note if entered
            initial_note = data.get('initial_note', '').strip()
            if initial_note:
                Activity.objects.create(
                    lead=lead,
                    activity_type='in_person_visit',
                    subject="Initial Field Contact Captured",
                    summary=initial_note,
                    outcome='follow_up_needed',
                    occurred_at=timezone.now(),
                    created_by=request.user
                )

            # Schedule follow-up task if date set
            follow_up_date = data.get('next_follow_up_date')
            if follow_up_date:
                FollowUpTask.objects.create(
                    lead=lead,
                    title="Follow up after field capture",
                    due_at=follow_up_date,
                    task_type='call',
                    assigned_to=request.user,
                    created_by=request.user,
                    priority='normal'
                )

            # Audit record
            LeadAuditLog.objects.create(
                lead=lead,
                changed_by=request.user,
                action='edit',
                notes="Created via Mobile Quick-Capture" + (" (Potential duplicate flagged)" if has_duplicates else "")
            )

            messages.success(request, f"Lead '{lead.name}' captured successfully!")
            return redirect('crm:quick_capture_success', pk=lead.pk)

        return render(request, 'crm/quick_capture.html', {'page': 'crm_quick_capture', 'form': form})


@crm_required
class QuickCaptureSuccessView(View):
    """Post-capture action screen providing 1-click continuation options."""
    def get(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk)
        duplicates = check_potential_duplicates(phone=lead.phone, name=lead.name, area=lead.area_or_location, exclude_lead_id=lead.id)
        return render(request, 'crm/quick_capture_success.html', {
            'page': 'crm_quick_capture',
            'lead': lead,
            'duplicates': duplicates,
        })


@crm_required
class LeadEditView(View):
    """Comprehensive edit screen for a lead."""
    def get(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk)
        if not can_edit_lead(request.user, lead):
            messages.error(request, "Permission denied to edit this lead.")
            return redirect('crm:lead_detail', pk=pk)
        form = LeadForm(instance=lead)
        return render(request, 'crm/lead_edit.html', {'page': 'crm_leads', 'lead': lead, 'form': form})

    def post(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk)
        if not can_edit_lead(request.user, lead):
            messages.error(request, "Permission denied to edit this lead.")
            return redirect('crm:lead_detail', pk=pk)
        form = LeadForm(request.POST, instance=lead)
        if form.is_valid():
            updated_lead = form.save()
            LeadAuditLog.objects.create(
                lead=updated_lead,
                changed_by=request.user,
                action='edit',
                notes="Lead details updated via edit form"
            )
            messages.success(request, "Lead details updated.")
            return redirect('crm:lead_detail', pk=pk)
        return render(request, 'crm/lead_edit.html', {'page': 'crm_leads', 'lead': lead, 'form': form})


@crm_required
class LeadChangeStatusView(View):
    """Endpoint for executing validated pipeline stage transitions."""
    def post(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk)
        form = LeadStatusChangeForm(request.POST)
        if form.is_valid():
            new_status = form.cleaned_data['status']
            loss_reason = form.cleaned_data.get('loss_reason')
            loss_reason_detail = form.cleaned_data.get('loss_reason_detail', '')
            notes = form.cleaned_data.get('notes', '')
            try:
                change_lead_status(
                    lead=lead,
                    new_status=new_status,
                    user=request.user,
                    loss_reason=loss_reason,
                    loss_reason_detail=loss_reason_detail,
                    notes=notes
                )
                messages.success(request, f"Lead moved to '{lead.get_status_display()}'.")
            except ValidationError as e:
                msg = e.messages[0] if hasattr(e, 'messages') else str(e)
                messages.error(request, f"Status transition rejected: {msg}")
        else:
            errors = form.errors.as_text()
            messages.error(request, f"Validation error: {errors}")

        return redirect('crm:lead_detail', pk=pk)


@crm_required
class LeadReassignView(View):
    """Reassigns lead owner (permission-gated)."""
    def post(self, request, pk):
        if not can_reassign_leads(request.user):
            messages.error(request, "Only CRM managers and administrators may reassign leads.")
            return redirect('crm:lead_detail', pk=pk)

        lead = get_object_or_404(Lead, pk=pk)
        form = ReassignLeadForm(request.POST)
        if form.is_valid():
            new_assignee = form.cleaned_data['assigned_to']
            reason = form.cleaned_data.get('reason', '')
            reassign_lead(lead, new_assignee, reassigned_by=request.user, reason=reason)
            messages.success(request, "Lead ownership reassigned successfully.")
        return redirect('crm:lead_detail', pk=pk)


@crm_required
class LogActivityView(View):
    """Appends an interaction to the lead timeline."""
    def post(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk)
        form = ActivityLogForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            log_interaction(
                lead=lead,
                activity_type=d['activity_type'],
                subject=d['subject'],
                summary=d['summary'],
                outcome=d['outcome'],
                occurred_at=d['occurred_at'],
                user=request.user,
                next_action=d.get('next_action', ''),
                next_action_date=d.get('next_action_date')
            )
            messages.success(request, "Interaction logged successfully.")
        else:
            messages.error(request, f"Error logging activity: {form.errors.as_text()}")

        return redirect('crm:lead_detail', pk=pk)


@crm_required
class CreateTaskView(View):
    """Schedules a new follow-up task on a lead."""
    def post(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk)
        form = FollowUpTaskForm(request.POST)
        if form.is_valid():
            task = form.save(commit=False)
            task.lead = lead
            task.created_by = request.user
            if not task.assigned_to:
                task.assigned_to = lead.assigned_to or request.user
            task.save()

            if not lead.next_follow_up_at or lead.next_follow_up_at > task.due_at:
                lead.next_follow_up_at = task.due_at
                lead.save(update_fields=['next_follow_up_at', 'updated_at'])

            messages.success(request, f"Follow-up task '{task.title}' scheduled.")
        else:
            messages.error(request, f"Error creating task: {form.errors.as_text()}")

        return redirect('crm:lead_detail', pk=pk)


@crm_required
class MyFollowUpsView(View):
    """
    Task management console showing Overdue, Due Today, Upcoming, and Completed tasks.
    """
    def get(self, request):
        user = request.user
        tasks_qs = filter_tasks_for_user(FollowUpTask.objects.all(), user).select_related('lead', 'assigned_to')

        now = timezone.now()
        today = now.date()

        overdue_tasks = tasks_qs.filter(status='open', due_at__lt=now).order_by('due_at')
        due_today_tasks = tasks_qs.filter(status='open', due_at__date=today, due_at__gte=now).order_by('due_at')
        upcoming_tasks = tasks_qs.filter(status='open', due_at__date__gt=today).order_by('due_at')
        completed_tasks = tasks_qs.filter(status='completed').order_by('-completed_at')[:20]

        context = {
            'page': 'crm_followups',
            'overdue_tasks': overdue_tasks,
            'due_today_tasks': due_today_tasks,
            'upcoming_tasks': upcoming_tasks,
            'completed_tasks': completed_tasks,
            'complete_form': TaskCompleteForm(),
        }
        return render(request, 'crm/my_followups.html', context)


@crm_required
class CompleteTaskView(View):
    """Marks a task as completed with optional follow-up creation."""
    def post(self, request, pk):
        task = get_object_or_404(FollowUpTask, pk=pk)
        form = TaskCompleteForm(request.POST)
        if form.is_valid():
            note = form.cleaned_data.get('completion_note', '')
            next_act = form.cleaned_data.get('create_next_action', '')
            next_date = form.cleaned_data.get('next_action_date')
            complete_task(task, user=request.user, completion_note=note, create_next_action=next_act, next_action_date=next_date)
            messages.success(request, f"Task '{task.title}' completed.")
        else:
            complete_task(task, user=request.user)
            messages.success(request, f"Task '{task.title}' completed.")

        next_url = request.POST.get('next') or request.META.get('HTTP_REFERER') or 'crm:my_followups'
        return redirect(next_url)


@crm_required
class LinkGroupView(View):
    """Links a real Komunity Group to the lead and checks activation criteria."""
    def post(self, request, pk):
        lead = get_object_or_404(Lead, pk=pk)
        group_id = request.POST.get('group_id')
        manual_activate = request.POST.get('manual_activate') == '1'
        notes = request.POST.get('notes', '')

        if group_id:
            group = get_object_or_404(Group, pk=group_id)
            lead.converted_komunity_group = group
            lead.converted_at = timezone.now()
            if lead.status in ['NEW', 'CONTACTED', 'QUALIFIED', 'DEMO_BOOKED', 'DEMO_COMPLETED', 'SIGNUP_STARTED', 'REGISTERED']:
                lead.status = 'GROUP_CREATED_OR_JOINED'
            lead.save()

            LeadAuditLog.objects.create(
                lead=lead,
                changed_by=request.user,
                action='edit',
                notes=f"Linked to Komunity Group: {group.name} (ID: {group.id})"
            )

            # If manager requested manual activation
            if manual_activate and is_crm_manager(request.user):
                manually_activate_lead(lead, user=request.user, group=group, notes=notes)
                messages.success(request, f"Group '{group.name}' linked and community manually activated!")
            else:
                messages.success(request, f"Group '{group.name}' successfully linked to lead.")
        else:
            messages.error(request, "Please select a valid group.")

        return redirect('crm:lead_detail', pk=pk)


@crm_required
class DuplicateCheckAPIView(View):
    """HTMX / API endpoint to return matching duplicates in real-time."""
    def get(self, request):
        phone = request.GET.get('phone', '')
        email = request.GET.get('email', '')
        name = request.GET.get('name', '')
        area = request.GET.get('area', '')
        exclude_id = request.GET.get('exclude_id')

        matches = check_potential_duplicates(
            phone=phone,
            email=email,
            name=name,
            area=area,
            exclude_lead_id=exclude_id
        )

        results = []
        for m in matches[:5]:
            results.append({
                'id': m.id,
                'name': m.name,
                'contact': m.primary_contact_name,
                'phone': m.phone,
                'status': m.get_status_display(),
                'owner': m.assigned_to.email if m.assigned_to else 'Unassigned'
            })

        return JsonResponse({'matches': results, 'count': len(results)})


@crm_required
class AmbassadorOnboardingView(View):
    """
    Onboarding and Directory view for Field Ambassadors in the Komunity Admin Portal.
    Allows CRM Managers and Admins to register new field agents, assign them to territories,
    generate welcome toolkits, and monitor ambassador performance.
    """
    def dispatch(self, request, *args, **kwargs):
        if not is_crm_manager(request.user) and not request.user.is_superuser:
            raise PermissionDenied("Only CRM Growth Managers and Admins can onboard ambassadors.")
        return super().dispatch(request, *args, **kwargs)

    def _get_ambassadors_data(self):
        User = get_user_model()
        ambassador_group, _ = DjangoGroup.objects.get_or_create(name='Field Agent')
        alt_group, _ = DjangoGroup.objects.get_or_create(name='Ambassador')

        ambassadors_qs = User.objects.filter(
            Q(groups=ambassador_group) | Q(groups=alt_group)
        ).select_related('profile').distinct().order_by('-date_joined')

        now = timezone.now()
        ambassadors_data = []
        total_leads_all = 0
        total_activations_all = 0

        for amb in ambassadors_qs:
            leads_captured = Lead.objects.filter(created_by=amb).count()
            assigned_leads = Lead.objects.filter(assigned_to=amb)
            assigned_count = assigned_leads.count()
            activated_count = assigned_leads.filter(status='ACTIVATED').count()
            overdue_tasks = FollowUpTask.objects.filter(assigned_to=amb, status='open', due_at__lt=now).count()

            total_leads_all += leads_captured
            total_activations_all += activated_count

            profile = getattr(amb, 'profile', None)
            full_name = profile.full_name if profile else (amb.phone or amb.email or f"User #{amb.id}")
            raw_territory = profile.cultural_background if profile and profile.cultural_background else (profile.bio if profile else "")
            territory = raw_territory
            if territory and territory.startswith("Territory:"):
                territory = territory.split("|")[0].replace("Territory:", "").strip()

            ambassadors_data.append({
                'user': amb,
                'full_name': full_name,
                'phone': amb.phone or '',
                'email': amb.email or '',
                'territory': territory or 'Unassigned',
                'leads_captured': leads_captured,
                'assigned_count': assigned_count,
                'activated_count': activated_count,
                'overdue_tasks': overdue_tasks,
                'is_active': amb.is_active,
                'date_joined': amb.date_joined,
            })

        return ambassadors_data, total_leads_all, total_activations_all

    def get(self, request):
        form = AmbassadorOnboardingForm()
        ambassadors_data, total_leads_all, total_activations_all = self._get_ambassadors_data()
        newly_created = request.session.pop('new_ambassador_toolkit', None)

        context = {
            'page': 'crm_ambassadors',
            'form': form,
            'ambassadors': ambassadors_data,
            'total_ambassadors': len(ambassadors_data),
            'total_leads_all': total_leads_all,
            'total_activations_all': total_activations_all,
            'newly_created': newly_created,
            'user_role': get_user_crm_role(request.user),
        }
        return render(request, 'crm/ambassadors.html', context)

    def post(self, request):
        form = AmbassadorOnboardingForm(request.POST)
        if form.is_valid():
            User = get_user_model()
            phone = form.cleaned_data['phone']
            first_name = form.cleaned_data['first_name'].strip()
            last_name = form.cleaned_data['last_name'].strip()
            email = form.cleaned_data.get('email')
            territory = form.cleaned_data.get('territory', '').strip()
            campaign_source = form.cleaned_data.get('campaign_source')
            notes = form.cleaned_data.get('notes', '').strip()

            raw_password = form.cleaned_data.get('password')
            if not raw_password:
                pin = random.randint(1000, 9999)
                raw_password = f"Komunity{pin}!"

            user = User.objects.create_user(
                phone=phone,
                email=email if email else None,
                password=raw_password
            )
            user.is_staff = True
            user.save()

            agent_group, _ = DjangoGroup.objects.get_or_create(name='Field Agent')
            user.groups.add(agent_group)

            profile = getattr(user, 'profile', None)
            if profile:
                profile.first_name = first_name
                profile.surname = last_name
                profile.phone = phone
                if email:
                    profile.email = email
                profile.cultural_background = territory
                profile.bio = f"Territory: {territory} | {notes}".strip(" | ")
                profile.save()

            # Create quick credentials bundle for the session
            request.session['new_ambassador_toolkit'] = {
                'full_name': f"{first_name} {last_name}",
                'phone': phone,
                'email': email or 'None provided',
                'territory': territory or 'General',
                'password': raw_password,
                'campaign': campaign_source.name if campaign_source else 'Standard Field Visit',
                'quick_capture_url': request.build_absolute_uri('/crm/quick-capture/'),
                'portal_url': request.build_absolute_uri('/crm/'),
            }

            messages.success(request, f"Ambassador {first_name} {last_name} ({phone}) onboarded successfully! See credentials toolkit below.")
            return redirect('crm:ambassadors')

        ambassadors_data, total_leads_all, total_activations_all = self._get_ambassadors_data()
        context = {
            'page': 'crm_ambassadors',
            'form': form,
            'ambassadors': ambassadors_data,
            'total_ambassadors': len(ambassadors_data),
            'total_leads_all': total_leads_all,
            'total_activations_all': total_activations_all,
            'newly_created': None,
            'user_role': get_user_crm_role(request.user),
        }
        return render(request, 'crm/ambassadors.html', context)

