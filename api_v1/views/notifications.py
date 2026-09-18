from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from user.models import DeviceToken, Notification
from user.serializers import DeviceTokenSerializer, NotificationSerializer
from .base import StandardPagination

class DeviceTokenViewSet(viewsets.ModelViewSet):
    queryset = DeviceToken.objects.all()
    serializer_class = DeviceTokenSerializer
    # Only authenticated users can register tokens
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return DeviceToken.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        # If token exists for this user, just update updated_at (handled by auto_now)
        # But since token is unique, we might need to handle integrity error or use update_or_create logic manually
        # OR we can let the frontend handle it by checking if it exists?
        # Better: use create to get_or_create.
        pass

    @action(detail=False, methods=['post'])
    def register(self, request):
        token = request.data.get('token')
        platform = request.data.get('platform')
        
        if not token:
            return Response({'error': 'Token is required'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Update or create
        # Ensure token is unique globally and assigned to current user
        device_token, created = DeviceToken.objects.update_or_create(
            token=token,
            defaults={'user': request.user, 'platform': platform, 'is_active': True}
        )
        
        return Response({'status': 'registered', 'created': created})

class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).order_by('-created_at')

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save()
        return Response({'status': 'marked as read', 'id': notification.id})

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        updated_count = Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return Response({'status': 'all marked as read', 'count': updated_count})

    @action(detail=False, methods=['get'])
    def unread_count(self, request):
        count = Notification.objects.filter(recipient=request.user, is_read=False).count()
        return Response({'unread_count': count})


