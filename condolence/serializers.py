from rest_framework import serializers
from .models import Contribution, Deceased, FundCampaign, CampaignContribution
from user.serializers import ProfileSerializer
from chema.serializers import GroupSerializer, OrganisationSerializer

class DeceasedSerializer(serializers.ModelSerializer):
    deceased_detail = ProfileSerializer(source='deceased', read_only=True)
    group_detail = GroupSerializer(source='group', read_only=True)
    beneficiary_detail = ProfileSerializer(source='beneficiary', read_only=True)
    total_raised = serializers.SerializerMethodField()
    total_disbursed = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()

    class Meta:
        model = Deceased
        fields = [
            'id', 'deceased', 'deceased_detail', 'group', 'group_detail', 
            'date', 'contributions_open', 'cont_is_active', 'total_raised',
            'beneficiary', 'beneficiary_detail', 'funds_disbursed',
            'total_disbursed', 'balance'
        ]

    def get_total_raised(self, obj):
        return obj.get_total_raised()

    def get_total_disbursed(self, obj):
        return obj.get_total_disbursed()

    def get_balance(self, obj):
        return obj.get_balance()

class ContributionSerializer(serializers.ModelSerializer):
    contributing_member_detail = ProfileSerializer(source='contributing_member', read_only=True)
    deceased_member_detail = DeceasedSerializer(source='deceased_member', read_only=True)

    class Meta:
        model = Contribution
        fields = [
            'id', 'group', 'deceased_member', 'deceased_member_detail',
            'contributing_member', 'contributing_member_detail', 
            'group_admin', 'amount', 'payment_method', 'contribution_date'
        ]


# ─────────────────────────────────────────────────────────────────────────────
# FundCampaign serializers
# ─────────────────────────────────────────────────────────────────────────────

class CampaignContributionSerializer(serializers.ModelSerializer):
    contributing_member_detail = ProfileSerializer(source='contributing_member', read_only=True)
    group_detail = GroupSerializer(source='group', read_only=True)

    class Meta:
        model = CampaignContribution
        fields = [
            'id', 'campaign', 'group', 'group_detail', 'organisation', 'contributing_member',
            'contributing_member_detail', 'amount', 'payment_method',
            'contribution_date', 'note',
        ]
        read_only_fields = ['contributing_member', 'contribution_date']

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if instance.campaign:
            data['campaign_detail'] = {
                'id': instance.campaign.id,
                'title': instance.campaign.title,
                'campaign_type': instance.campaign.campaign_type,
            }
        return data


class FundCampaignSerializer(serializers.ModelSerializer):
    beneficiary_detail = ProfileSerializer(source='beneficiary', read_only=True)
    created_by_detail = ProfileSerializer(source='created_by', read_only=True)
    group_detail = GroupSerializer(source='group', read_only=True)
    organisation_detail = OrganisationSerializer(source='organisation', read_only=True)
    campaign_type = serializers.CharField(required=False, default='custom')
    total_raised = serializers.SerializerMethodField()
    contributor_count = serializers.SerializerMethodField()
    balance = serializers.SerializerMethodField()
    progress_percent = serializers.SerializerMethodField()
    has_contributed = serializers.SerializerMethodField()

    class Meta:
        model = FundCampaign
        fields = [
            'id', 'group', 'group_detail', 'organisation', 'organisation_detail',
            'campaign_type', 'title', 'description',
            'beneficiary', 'beneficiary_detail', 'target_amount',
            'contributions_open', 'funds_disbursed', 'is_public',
            'created_by', 'created_by_detail', 'created_at', 'deadline',
            # computed
            'total_raised', 'contributor_count', 'balance',
            'progress_percent', 'has_contributed',
        ]
        read_only_fields = ['created_by', 'created_at']

    def get_total_raised(self, obj):
        return float(obj.get_total_raised())

    def get_contributor_count(self, obj):
        return obj.get_contributor_count()

    def get_balance(self, obj):
        return float(obj.get_balance())

    def get_progress_percent(self, obj):
        """Returns 0-100 progress towards target, or None if no target set."""
        if not obj.target_amount or float(obj.target_amount) == 0:
            return None
        raised = float(obj.get_total_raised())
        target = float(obj.target_amount)
        return min(round((raised / target) * 100, 1), 100.0)

    def get_has_contributed(self, obj):
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            try:
                profile = request.user.profile
                return obj.campaign_contributions.filter(
                    contributing_member=profile
                ).exists()
            except Exception:
                return False
        return False

    def validate(self, data):
        """
        Enforce:
        1. Either group or organisation must be set, but not both.
        2. Group campaigns must match the group's purpose.
        3. Emergency campaigns are only allowed for verified organisations.
        """
        group = data.get('group', getattr(self.instance, 'group', None))
        organisation = data.get('organisation', getattr(self.instance, 'organisation', None))
        campaign_type = data.get('campaign_type', getattr(self.instance, 'campaign_type', None))

        if not group and not organisation:
            raise serializers.ValidationError("A campaign must be linked to either a Group or an Organisation.")
        if group and organisation:
            raise serializers.ValidationError("A campaign cannot be linked to both a Group and an Organisation.")

        if group:
            # The group is the category: auto-assign campaign_type from group purpose
            valid_types = ['bereavement', 'excess', 'custom']
            group_purpose = getattr(group, 'purpose', 'custom')
            assigned_type = group_purpose if group_purpose in valid_types else 'custom'
            data['campaign_type'] = assigned_type
            campaign_type = assigned_type

        if organisation:
            # Emergency campaigns require verification
            if campaign_type == 'emergency' and not organisation.is_verified:
                raise serializers.ValidationError(
                    "Emergency / Disaster fundraisers are only available to verified Organisations. "
                    "Please submit a verification request in settings."
                )
            if campaign_type in ['bereavement', 'excess']:
                raise serializers.ValidationError(
                    f"Campaigns of type '{campaign_type}' are only available to community Groups."
                )

        if campaign_type not in dict(FundCampaign.CAMPAIGN_TYPE_CHOICES):
            data['campaign_type'] = 'custom'

        return data
