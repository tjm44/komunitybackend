from functools import wraps
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import redirect


def is_crm_admin(user):
    """Check if user has full CRM administrator privileges."""
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.groups.filter(name='CRM Admin').exists()


def is_crm_manager(user):
    """Check if user is a Community/Growth Manager or CRM Admin."""
    if not user or not user.is_authenticated:
        return False
    if is_crm_admin(user):
        return True
    return user.groups.filter(name__in=['CRM Manager', 'Growth Manager']).exists()


def is_leadership_readonly(user):
    """Check if user is in leadership read-only role."""
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name='CRM Leadership Read-only').exists()


def is_field_agent(user):
    """Check if user is a Field Agent / Ambassador."""
    if not user or not user.is_authenticated:
        return False
    return user.groups.filter(name__in=['Field Agent', 'Ambassador']).exists()


def get_user_crm_role(user):
    """Returns the primary CRM role label for display and routing."""
    if not user or not user.is_authenticated:
        return "Anonymous"
    if user.is_superuser or user.groups.filter(name='CRM Admin').exists():
        return "CRM Admin"
    if user.groups.filter(name__in=['CRM Manager', 'Growth Manager']).exists():
        return "Growth Manager"
    if user.groups.filter(name='CRM Leadership Read-only').exists():
        return "Leadership (Read-Only)"
    if user.groups.filter(name='CRM Marketing').exists():
        return "Marketing"
    if user.groups.filter(name__in=['Field Agent', 'Ambassador']).exists():
        return "Field Agent"
    if user.is_staff:
        # Default staff user
        return "Growth Manager"
    return "Field Agent"


def filter_leads_for_user(queryset, user):
    """
    Applies role-based queryset scoping:
    - Admins, Managers, and Leadership see all team leads.
    - Field agents strictly see leads assigned to them or unassigned leads they created.
    """
    if not user or not user.is_authenticated:
        return queryset.none()

    if is_crm_manager(user) or is_leadership_readonly(user) or user.is_superuser:
        return queryset

    # If user is specifically a field agent (or non-manager staff), restrict to assigned/created
    if is_field_agent(user) or not user.is_staff:
        return queryset.filter(Q(assigned_to=user) | (Q(created_by=user) & Q(assigned_to__isnull=True)))

    # Fallback for staff
    return queryset


def filter_tasks_for_user(queryset, user):
    """Scopes tasks to the user unless they have manager/admin visibility."""
    if not user or not user.is_authenticated:
        return queryset.none()

    if is_crm_manager(user) or user.is_superuser:
        return queryset

    return queryset.filter(Q(assigned_to=user) | Q(created_by=user))


def can_reassign_leads(user):
    """Determines if user has rights to reassign leads."""
    return is_crm_manager(user) or user.is_superuser


def can_edit_lead(user, lead):
    """Determines if user can edit this specific lead."""
    if not user or not user.is_authenticated:
        return False
    if is_crm_manager(user) or user.is_superuser:
        return True
    if is_leadership_readonly(user):
        return False
    # Field agent can edit if assigned or created
    return lead.assigned_to_id == user.id or (lead.created_by_id == user.id and lead.assigned_to_id is None)


def crm_access_required(view_func):
    """Decorator ensuring user is authenticated and permitted for CRM."""
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/admin/login/?next=' + request.path)
        # Any staff user or member of a CRM group is allowed access
        if not request.user.is_staff and not request.user.groups.filter(name__startswith='CRM').exists() and not is_field_agent(request.user):
            raise PermissionDenied("You do not have permission to access the Komunity CRM.")
        return view_func(request, *args, **kwargs)
    return _wrapped_view
