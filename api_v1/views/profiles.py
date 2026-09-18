import os
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from user.models import Profile
from user.serializers import ProfileSerializer
from .base import StandardPagination

class ProfileViewSet(viewsets.ModelViewSet):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        # Allow users to only see their own profile or public profiles
        if self.request.user.is_authenticated:
            return Profile.objects.filter(is_active=True)
        return Profile.objects.none()

    @action(detail=False, methods=['get'])
    def me(self, request):
        serializer = self.get_serializer(request.user.profile)
        return Response(serializer.data)

    def perform_update(self, serializer):
        profile = serializer.save()
        profile.check_completion()
        profile.save()

    @action(detail=True, methods=['post'], url_path='verify-kyc')
    def verify_kyc(self, request, pk=None):
        profile = self.get_object()
        if profile.user != request.user:
            return Response({'error': 'You can only verify your own profile.'}, status=status.HTTP_403_FORBIDDEN)
            
        id_number = request.data.get('id_number')
        id_type = request.data.get('id_type', 'national_id')
        req_first_name = request.data.get('first_name') or profile.first_name
        req_surname = request.data.get('surname') or request.data.get('last_name') or profile.surname
        
        from user.kyc import FlutterwaveKYCProvider
        kyc_result = FlutterwaveKYCProvider.verify_document(
            first_name=req_first_name,
            surname=req_surname,
            id_number=id_number,
            id_type=id_type
        )
        
        if len(kyc_result) == 3:
            success, message, verified_data = kyc_result
        else:
            success, message = kyc_result[:2]
            verified_data = {}
        
        if not success:
            return Response({'error': message}, status=status.HTTP_400_BAD_REQUEST)
            
        # Update profile name with verified identity details if available
        v_first_name = (verified_data or {}).get('first_name') or (verified_data or {}).get('firstName') or req_first_name
        v_surname = (verified_data or {}).get('last_name') or (verified_data or {}).get('lastName') or (verified_data or {}).get('surname') or req_surname

        if v_first_name:
            profile.first_name = v_first_name
        if v_surname:
            profile.surname = v_surname

        profile.is_verified = True
        profile.check_completion()
        profile.save()

        serializer = self.get_serializer(profile)
        return Response({
            'status': 'verified',
            'message': message,
            'full_name': profile.full_name,
            'first_name': profile.first_name,
            'surname': profile.surname,
            'profile': serializer.data
        }, status=status.HTTP_200_OK)

