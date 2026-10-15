from django.contrib import admin
from .models import AcquisitionSource, Lead, Contact, Activity, FollowUpTask, LeadAuditLog


class ContactInline(admin.TabularInline):
    model = Contact
    extra = 1


class ActivityInline(admin.TabularInline):
    model = Activity
    extra = 0
    readonly_fields = ['occurred_at', 'created_at']


class FollowUpTaskInline(admin.TabularInline):
    model = FollowUpTask
    extra = 0


@admin.register(AcquisitionSource)
class AcquisitionSourceAdmin(admin.ModelAdmin):
    list_display = ['name', 'source_type', 'campaign_code', 'is_active', 'created_at']
    list_filter = ['source_type', 'is_active']
    search_fields = ['name', 'campaign_code', 'location_or_area']


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ['name', 'primary_contact_name', 'phone', 'community_type', 'status', 'priority', 'assigned_to', 'created_at']
    list_filter = ['status', 'community_type', 'priority', 'consent_to_contact', 'do_not_contact']
    search_fields = ['name', 'primary_contact_name', 'phone', 'area_or_location']
    inlines = [ContactInline, ActivityInline, FollowUpTaskInline]


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ['lead', 'activity_type', 'subject', 'outcome', 'occurred_at', 'created_by']
    list_filter = ['activity_type', 'outcome']
    search_fields = ['subject', 'summary', 'lead__name']


@admin.register(FollowUpTask)
class FollowUpTaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'lead', 'task_type', 'assigned_to', 'due_at', 'status', 'priority']
    list_filter = ['status', 'priority', 'task_type']
    search_fields = ['title', 'lead__name']


@admin.register(LeadAuditLog)
class LeadAuditLogAdmin(admin.ModelAdmin):
    list_display = ['lead', 'action', 'old_value', 'new_value', 'changed_by', 'timestamp']
    list_filter = ['action']
    search_fields = ['lead__name', 'notes']
