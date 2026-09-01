from django.contrib import admin
from .models import (
    Group, Organisation, GroupMembership, Post, PostImage, Comment, Reply, Dependent,
    GroupBereavementProfile, GroupChurchProfile, GroupStokvelProfile, GroupStudentProfile,
    ContributionCycle, MemberCyclePayment
)


# ─────────────────────────────────────────────────────────────
#  Inline classes for nested editing
# ─────────────────────────────────────────────────────────────

class GroupMembershipInline(admin.TabularInline):
    model = GroupMembership
    extra = 0
    fields = ('member', 'role', 'status', 'is_admin', 'is_active', 'date_joined')
    readonly_fields = ('date_joined',)
    raw_id_fields = ('member',)


class GroupBereavementProfileInline(admin.StackedInline):
    model = GroupBereavementProfile
    extra = 0
    can_delete = False


class GroupChurchProfileInline(admin.StackedInline):
    model = GroupChurchProfile
    extra = 0
    can_delete = False


class GroupStokvelProfileInline(admin.StackedInline):
    model = GroupStokvelProfile
    extra = 0
    can_delete = False


class GroupStudentProfileInline(admin.StackedInline):
    model = GroupStudentProfile
    extra = 0
    can_delete = False





class PostImageInline(admin.TabularInline):
    model = PostImage
    extra = 0
    readonly_fields = ('uploaded_at',)


# ─────────────────────────────────────────────────────────────
#  Group
# ─────────────────────────────────────────────────────────────

@admin.register(Group)
class GroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'purpose', 'creator', 'total_members', 'group_balance', 'is_active', 'created_at')
    search_fields = ('name', 'creator__email', 'description')
    list_filter = ('purpose', 'is_active', 'requires_approval', 'verified_members_only', 'created_at')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('admin', 'creator')
    inlines = [
        GroupMembershipInline,
        GroupBereavementProfileInline,
        GroupChurchProfileInline,
        GroupStokvelProfileInline,
        GroupStudentProfileInline,
    ]
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'description', 'cover_image', 'is_active')
        }),
        ('Purpose & Fund Type', {
            'fields': ('purpose', 'fund_description')
        }),
        ('Ownership', {
            'fields': ('creator', 'admin', 'admins')
        }),
        ('Recurring Contributions', {
            'fields': (
                'enable_recurring_contributions',
                'recurring_amount',
                'recurring_frequency',
                'recurring_due_day',
                'recurring_title',
                'recurring_reminder_days',
            )
        }),
        ('Settings', {
            'fields': ('max_members', 'requires_approval', 'verified_members_only')
        }),
        ('Wallet Integration', {
            'fields': ('external_wallet_id',),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )

    def total_members(self, obj):
        return obj.get_total_members()
    total_members.short_description = 'Members'

    def group_balance(self, obj):
        return f"R {obj.get_balance()}"
    group_balance.short_description = 'Balance'


# ─────────────────────────────────────────────────────────────
#  Organisation
# ─────────────────────────────────────────────────────────────

@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ('name', 'entity_type', 'is_verified', 'is_active', 'creator', 'created_at')
    search_fields = ('name', 'description', 'registration_number', 'creator__email', 'email', 'phone_number')
    list_filter = ('entity_type', 'is_verified', 'is_active', 'created_at')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('creator', 'admin2', 'admin3')
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'description', 'cover_image', 'is_active')
        }),
        ('Contact Info', {
            'fields': ('email', 'phone_number')
        }),
        ('Legal Details', {
            'fields': ('entity_type', 'registration_number', 'is_verified')
        }),
        ('Ownership', {
            'fields': ('creator', 'admins', 'admin2', 'admin3')
        }),
        ('Wallet', {
            'fields': ('external_wallet_id',),
            'classes': ('collapse',),
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',),
        }),
    )


# ─────────────────────────────────────────────────────────────
#  GroupMembership
# ─────────────────────────────────────────────────────────────

@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ('member', 'group', 'role', 'status', 'is_admin', 'is_active', 'is_deceased', 'date_joined')
    search_fields = ('member__first_name', 'member__surname', 'group__name')
    list_filter = ('role', 'status', 'is_admin', 'is_active', 'is_deceased', 'date_joined')
    date_hierarchy = 'date_joined'
    readonly_fields = ('date_joined', 'approved_at')
    raw_id_fields = ('member', 'group', 'approved_by')


# ─────────────────────────────────────────────────────────────
#  Post
# ─────────────────────────────────────────────────────────────

@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('author', 'group', 'organisation', 'approved', 'created_at', 'likes_count')
    search_fields = ('content', 'author__first_name', 'author__surname', 'group__name')
    list_filter = ('approved', 'created_at', 'group')
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at',)
    raw_id_fields = ('author', 'group', 'organisation')
    inlines = [PostImageInline]

    def likes_count(self, obj):
        return obj.likes.count()
    likes_count.short_description = 'Likes'


# ─────────────────────────────────────────────────────────────
#  Comment
# ─────────────────────────────────────────────────────────────

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('author', 'post', 'short_content', 'created_at')
    search_fields = ('content', 'author__first_name', 'author__surname')
    list_filter = ('created_at',)
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at',)
    raw_id_fields = ('author', 'post')

    def short_content(self, obj):
        return obj.content[:60] + ('…' if len(obj.content) > 60 else '')
    short_content.short_description = 'Content'


# ─────────────────────────────────────────────────────────────
#  Reply
# ─────────────────────────────────────────────────────────────

@admin.register(Reply)
class ReplyAdmin(admin.ModelAdmin):
    list_display = ('author', 'comment', 'short_content', 'created_at')
    search_fields = ('content', 'author__first_name', 'author__surname')
    list_filter = ('created_at',)
    date_hierarchy = 'created_at'
    readonly_fields = ('created_at',)
    raw_id_fields = ('author', 'comment')

    def short_content(self, obj):
        return (obj.content or '')[:60] + ('…' if obj.content and len(obj.content) > 60 else '')
    short_content.short_description = 'Content'


# ─────────────────────────────────────────────────────────────
#  Dependent
# ─────────────────────────────────────────────────────────────

@admin.register(Dependent)
class DependentAdmin(admin.ModelAdmin):
    list_display = ('name', 'guardian', 'relationship', 'date_of_birth', 'group', 'date_added')
    search_fields = ('name', 'guardian__first_name', 'guardian__surname', 'relationship')
    list_filter = ('relationship', 'date_added', 'group')
    readonly_fields = ('date_added',)
    raw_id_fields = ('guardian', 'group')


# ─────────────────────────────────────────────────────────────
#  Recurring Contribution Cycles & Member Payments
# ─────────────────────────────────────────────────────────────

class MemberCyclePaymentInline(admin.TabularInline):
    model = MemberCyclePayment
    extra = 0
    fields = ('member', 'amount_due', 'amount_paid', 'status', 'paid_at', 'payment_method')
    readonly_fields = ('paid_at',)
    raw_id_fields = ('member', 'transaction')


@admin.register(ContributionCycle)
class ContributionCycleAdmin(admin.ModelAdmin):
    list_display = ('title', 'group', 'due_date', 'target_amount_per_member', 'status', 'total_collected_display', 'paid_progress')
    list_filter = ('status', 'due_date', 'group')
    search_fields = ('title', 'group__name')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('group',)
    inlines = [MemberCyclePaymentInline]

    def total_collected_display(self, obj):
        return f"R {obj.get_total_collected():.2f} / R {obj.get_total_expected():.2f}"
    total_collected_display.short_description = 'Collected / Expected'

    def paid_progress(self, obj):
        return f"{obj.get_paid_count()} / {obj.get_total_members_count()} ({obj.get_progress_percentage()}%)"
    paid_progress.short_description = 'Members Paid'


@admin.register(MemberCyclePayment)
class MemberCyclePaymentAdmin(admin.ModelAdmin):
    list_display = ('member', 'cycle', 'amount_due', 'amount_paid', 'status', 'paid_at', 'payment_method')
    list_filter = ('status', 'payment_method', 'cycle__group')
    search_fields = ('member__first_name', 'member__surname', 'cycle__title', 'cycle__group__name')
    readonly_fields = ('created_at', 'updated_at')
    raw_id_fields = ('cycle', 'member', 'transaction')



