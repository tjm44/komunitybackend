from django.db.models import Q
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from chema.models import Group
from chema.serializers import GroupSerializer
from user.models import Profile
from user.serializers import ProfileSerializer

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def search_api_view(request):
    """
    Search groups and members.
    Query param: q
    """
    from django.db.models import Q
    query = request.GET.get('q', '').strip()
    
    if not query:
        return Response({'groups': [], 'members': []})

    groups = Group.objects.filter(
        Q(name__icontains=query) | 
        Q(description__icontains=query)
    ).distinct()

    members = Profile.objects.filter(
        Q(user__email__icontains=query) | 
        Q(first_name__icontains=query) | 
        Q(surname__icontains=query)
    ).distinct()

    return Response({
        'groups': GroupSerializer(groups, many=True, context={'request': request}).data,
        'members': ProfileSerializer(members, many=True, context={'request': request}).data
    })


