from rest_framework import serializers
from .models import (
    Group, GroupMembership, Post, PostImage, Comment, Reply, Dependent, Organisation,
    GroupBereavementProfile, GroupChurchProfile, GroupStokvelProfile, GroupStudentProfile, GroupSportsProfile, GroupExcessProfile,
    ContributionCycle, MemberCyclePayment
)
from user.serializers import ProfileSerializer

class MemberCyclePaymentSerializer(serializers.ModelSerializer):
    member_detail = ProfileSerializer(source='member', read_only=True)

    class Meta:
        model = MemberCyclePayment
        fields = [
            'id', 'cycle', 'member', 'member_detail',
            'amount_due', 'amount_paid', 'status',
            'paid_at', 'payment_method', 'reminder_sent_at',
            'notes', 'created_at', 'updated_at'
        ]


class ContributionCycleSerializer(serializers.ModelSerializer):
    total_expected = serializers.SerializerMethodField()
    total_collected = serializers.SerializerMethodField()
    paid_count = serializers.SerializerMethodField()
    unpaid_count = serializers.SerializerMethodField()
    total_members_count = serializers.SerializerMethodField()
    progress_percentage = serializers.SerializerMethodField()
    payments = MemberCyclePaymentSerializer(many=True, read_only=True)
    my_payment = serializers.SerializerMethodField()

    class Meta:
        model = ContributionCycle
        fields = [
            'id', 'group', 'title', 'due_date', 'target_amount_per_member',
            'status', 'cycle_month', 'cycle_year', 'created_at', 'updated_at',
            'total_expected', 'total_collected', 'paid_count', 'unpaid_count',
            'total_members_count', 'progress_percentage', 'payments', 'my_payment'
        ]

    def get_total_expected(self, obj):
        return obj.get_total_expected()

    def get_total_collected(self, obj):
        return obj.get_total_collected()

    def get_paid_count(self, obj):
        return obj.get_paid_count()

    def get_unpaid_count(self, obj):
        return obj.get_unpaid_count()

    def get_total_members_count(self, obj):
        return obj.get_total_members_count()

    def get_progress_percentage(self, obj):
        return obj.get_progress_percentage()

    def get_my_payment(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                payment = obj.payments.filter(member=request.user.profile).first()
                if payment:
                    return MemberCyclePaymentSerializer(payment, context=self.context).data
            except Exception:
                return None
        return None


class GroupMembershipSerializer(serializers.ModelSerializer):
    member_detail = ProfileSerializer(source='member', read_only=True)

    class Meta:
        model = GroupMembership
        fields = [
            'id', 'member', 'member_detail', 'group', 'is_admin', 
            'status', 'role', 'date_joined', 'is_active', 'is_deceased',
            'join_message', 'beneficiary_name', 'beneficiary_relationship', 'beneficiary_phone',
            'vehicle_make_model', 'vehicle_registration', 'insurer_name', 'policy_number', 'vin_number', 'driver_license_number'
        ]


class GroupBereavementProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupBereavementProfile
        fields = [
            'beneficiary_name', 'beneficiary_relationship', 'beneficiary_phone', 'beneficiary_payout_details',
            'allow_dependents', 'max_dependents', 'allowed_dependent_types',
            'contribution_schedule', 'fixed_contribution_amount', 'fixed_claim_payout'
        ]


class GroupExcessProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupExcessProfile
        fields = [
            'max_excess_payout', 'require_vin_verification', 'require_policy_proof'
        ]


class GroupChurchProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupChurchProfile
        fields = [
            'denomination', 'branch_parish_name',
            'enable_faith_pledges', 'enable_tax_receipts', 'enable_bulletin_announcements', 'default_tithe_amount'
        ]


class GroupStokvelProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupStokvelProfile
        fields = [
            'stokvel_type', 'cycle_frequency', 'contribution_amount',
            'payout_rotation_mode', 'penalty_late_fee', 'borrowing_allowed', 'payout_target_month'
        ]


class GroupStudentProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupStudentProfile
        fields = [
            'institution_name', 'campus_name', 'student_body_type',
            'student_id_required', 'membership_fee', 'membership_fee_period',
            'enable_event_ticketing', 'enable_emergency_relief_fund'
        ]


class GroupSportsProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = GroupSportsProfile
        fields = [
            'sport_category', 'club_level',
            'membership_fee', 'dues_frequency', 'match_fee_per_game',
            'kit_equipment_fund_enabled'
        ]


class GroupSerializer(serializers.ModelSerializer):
    total_members = serializers.IntegerField(source='get_total_members', read_only=True)
    balance = serializers.DecimalField(source='get_balance', max_digits=10, decimal_places=2, read_only=True)
    is_admin = serializers.SerializerMethodField()
    is_selected = serializers.SerializerMethodField()
    unread_posts_count = serializers.SerializerMethodField()
    membership_status = serializers.SerializerMethodField()
    active_cycle = serializers.SerializerMethodField()
    my_cycle_status = serializers.SerializerMethodField()

    # Profile serializers
    bereavement_profile = GroupBereavementProfileSerializer(required=False, allow_null=True)
    church_profile = GroupChurchProfileSerializer(required=False, allow_null=True)
    stokvel_profile = GroupStokvelProfileSerializer(required=False, allow_null=True)
    student_profile = GroupStudentProfileSerializer(required=False, allow_null=True)
    sports_profile = GroupSportsProfileSerializer(required=False, allow_null=True)

    class Meta:
        model = Group
        fields = [
            'id', 'name', 'is_active', 'description', 'cover_image', 
            'total_members', 'requires_approval', 'created_at', 'is_admin', 'balance',
            'is_selected', 'unread_posts_count', 'membership_status',
            # Fund purpose fields
            'purpose', 'fund_description', 'verified_members_only',
            # Recurring Contribution settings
            'enable_recurring_contributions', 'recurring_amount', 'recurring_frequency',
            'recurring_due_day', 'recurring_title', 'recurring_reminder_days',
            'active_cycle', 'my_cycle_status',
            # Notification settings
            'notify_on_member_join', 'notify_on_member_promote', 
            'notify_on_wallet_transfer', 'notify_on_campaign_created',
            # Governance
            'min_disbursement_approvals',
            # Type-specific Profiles
            'bereavement_profile', 'church_profile', 'stokvel_profile',
            'student_profile', 'sports_profile',
        ]

    def get_is_selected(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                membership = GroupMembership.objects.filter(group=obj, member=request.user.profile).first()
                return membership.is_active if membership else False
            except Exception:
                return False
        return False

    def get_is_admin(self, obj):
        request = self.context.get('request')
        if request and request.user:
            return obj.is_admin(request.user)
        return False

    def get_unread_posts_count(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                membership = GroupMembership.objects.filter(group=obj, member=request.user.profile).first()
                if not membership:
                    return 0
                
                query = Post.objects.filter(group=obj, approved=True)
                if membership.last_viewed_at:
                    query = query.filter(created_at__gt=membership.last_viewed_at)
                
                # Exclude own posts from unread count
                query = query.exclude(author=request.user.profile)
                
                return query.count()
            except Exception:
                return 0
        return 0

    def get_membership_status(self, obj):
        """Returns 'active', 'pending', or null if user is not a member."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                membership = GroupMembership.objects.filter(group=obj, member=request.user.profile).first()
                if membership:
                    return membership.status
            except Exception:
                pass
        return None

    def get_active_cycle(self, obj):
        if not obj.enable_recurring_contributions:
            return None
        cycle = obj.contribution_cycles.filter(status='active').first()
        if not cycle and obj.recurring_amount > 0:
            cycle = obj.ensure_active_cycle()
        if cycle:
            return {
                'id': cycle.id,
                'title': cycle.title,
                'due_date': cycle.due_date.isoformat() if cycle.due_date else None,
                'target_amount_per_member': float(cycle.target_amount_per_member),
                'status': cycle.status,
                'total_expected': cycle.get_total_expected(),
                'total_collected': cycle.get_total_collected(),
                'paid_count': cycle.get_paid_count(),
                'unpaid_count': cycle.get_unpaid_count(),
                'progress_percentage': cycle.get_progress_percentage(),
            }
        return None

    def get_my_cycle_status(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated or not obj.enable_recurring_contributions:
            return None
        cycle = obj.contribution_cycles.filter(status='active').first()
        if not cycle:
            return None
        payment = cycle.payments.filter(member=request.user.profile).first()
        if payment:
            return {
                'id': payment.id,
                'status': payment.status,
                'amount_due': float(payment.amount_due),
                'amount_paid': float(payment.amount_paid),
                'paid_at': payment.paid_at.isoformat() if payment.paid_at else None,
                'payment_method': payment.payment_method,
            }
        return None

    def create(self, validated_data):
        bereavement_data = validated_data.pop('bereavement_profile', None)
        church_data = validated_data.pop('church_profile', None)
        stokvel_data = validated_data.pop('stokvel_profile', None)
        student_data = validated_data.pop('student_profile', None)
        sports_data = validated_data.pop('sports_profile', None)

        group = super().create(validated_data)

        if bereavement_data and hasattr(group, 'bereavement_profile'):
            for attr, val in bereavement_data.items():
                setattr(group.bereavement_profile, attr, val)
            group.bereavement_profile.save()

        if church_data and hasattr(group, 'church_profile'):
            for attr, val in church_data.items():
                setattr(group.church_profile, attr, val)
            group.church_profile.save()

        if stokvel_data and hasattr(group, 'stokvel_profile'):
            for attr, val in stokvel_data.items():
                setattr(group.stokvel_profile, attr, val)
            group.stokvel_profile.save()

        if student_data and hasattr(group, 'student_profile'):
            for attr, val in student_data.items():
                setattr(group.student_profile, attr, val)
            group.student_profile.save()

        if sports_data and hasattr(group, 'sports_profile'):
            for attr, val in sports_data.items():
                setattr(group.sports_profile, attr, val)
            group.sports_profile.save()

        return group

    def update(self, instance, validated_data):
        bereavement_data = validated_data.pop('bereavement_profile', None)
        church_data = validated_data.pop('church_profile', None)
        stokvel_data = validated_data.pop('stokvel_profile', None)
        student_data = validated_data.pop('student_profile', None)
        sports_data = validated_data.pop('sports_profile', None)

        group = super().update(instance, validated_data)

        if bereavement_data:
            prof, _ = GroupBereavementProfile.objects.get_or_create(group=group)
            for attr, val in bereavement_data.items():
                setattr(prof, attr, val)
            prof.save()

        if church_data:
            prof, _ = GroupChurchProfile.objects.get_or_create(group=group)
            for attr, val in church_data.items():
                setattr(prof, attr, val)
            prof.save()

        if stokvel_data:
            prof, _ = GroupStokvelProfile.objects.get_or_create(group=group)
            for attr, val in stokvel_data.items():
                setattr(prof, attr, val)
            prof.save()

        if student_data:
            prof, _ = GroupStudentProfile.objects.get_or_create(group=group)
            for attr, val in student_data.items():
                setattr(prof, attr, val)
            prof.save()

        if sports_data:
            prof, _ = GroupSportsProfile.objects.get_or_create(group=group)
            for attr, val in sports_data.items():
                setattr(prof, attr, val)
            prof.save()

        return group

class OrganisationSerializer(serializers.ModelSerializer):
    balance = serializers.DecimalField(source='get_balance', max_digits=10, decimal_places=2, read_only=True)
    is_admin = serializers.SerializerMethodField()
    admin2_detail = ProfileSerializer(source='admin2.profile', read_only=True)
    admin3_detail = ProfileSerializer(source='admin3.profile', read_only=True)

    class Meta:
        model = Organisation
        fields = [
            'id', 'name', 'is_active', 'description', 'cover_image',
            'created_at', 'is_admin', 'balance',
            # Registry fields
            'is_verified', 'registration_number', 'entity_type',
            'email', 'phone_number', 'admin2', 'admin3',
            'admin2_detail', 'admin3_detail',
        ]

    def get_is_admin(self, obj):
        request = self.context.get('request')
        if request and request.user:
            return obj.is_admin(request.user)
        return False

class PostImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = PostImage
        fields = ['id', 'post', 'image', 'uploaded_at']

from chema.models import Comment

class ReplySerializer(serializers.ModelSerializer):
    author_detail = ProfileSerializer(source='author', read_only=True)
    comment = serializers.PrimaryKeyRelatedField(queryset=Comment.objects.all())

    class Meta:
        model = Reply
        fields = ['id', 'author', 'author_detail', 'content', 'created_at', 'comment']
        read_only_fields = ['author', 'created_at']

class CommentSerializer(serializers.ModelSerializer):
    author_detail = ProfileSerializer(source='author', read_only=True)
    replies = ReplySerializer(many=True, read_only=True)

    class Meta:
        model = Comment
        fields = ['id', 'post', 'author', 'author_detail', 'content', 'created_at', 'replies']
        read_only_fields = ['author']

class PostSerializer(serializers.ModelSerializer):
    author_detail = ProfileSerializer(source='author', read_only=True)
    images = PostImageSerializer(many=True, read_only=True)
    comment_count = serializers.SerializerMethodField()
    likes_count = serializers.SerializerMethodField()
    has_liked = serializers.SerializerMethodField()

    class Meta:
        model = Post
        fields = [
            'id', 'author', 'author_detail', 'group', 'organisation', 'content', 
            'images', 'video', 'created_at', 'approved', 'comment_count',
            'likes_count', 'has_liked'
        ]
        read_only_fields = ['author']

    def get_comment_count(self, obj):
        return Comment.objects.filter(post=obj).count()

    def get_likes_count(self, obj):
        return obj.get_likes_count()

    def get_has_liked(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                profile = request.user.profile
                return obj.likes.filter(id=profile.id).exists()
            except Exception:
                return False
        return False

class DependentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dependent
        fields = [
            'id', 'guardian', 'name', 'date_of_birth', 
            'relationship', 'group'
        ]
