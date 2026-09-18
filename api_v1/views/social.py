from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from chema.models import Comment, GroupMembership, Post, PostImage, Reply
from chema.serializers import (
    CommentSerializer, PostImageSerializer, PostSerializer, ReplySerializer
)
from user.models import Profile
from user.notifications import send_push_notification
from .base import IsAuthorOrReadOnly, IsPostImageAuthorOrReadOnly, StandardPagination

class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.filter(approved=True).order_by('-created_at')
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardPagination

    def get_permissions(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return [permissions.IsAuthenticated(), IsAuthorOrReadOnly()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        queryset = Post.objects.filter(approved=True).order_by('-created_at')
        
        # Only filter by group_id on list action to avoid 404s on detail/action endpoints
        if self.action == 'list':
            group_id = self.request.query_params.get('group_id')
            if group_id:
                queryset = queryset.filter(group_id=group_id)
            elif self.request.user.is_authenticated:
                # Audit: Default to active group
                active_mem = GroupMembership.objects.filter(
                    member=self.request.user.profile, 
                    is_active=True
                ).first()
                if active_mem:
                    queryset = queryset.filter(group=active_mem.group)
                else:
                    queryset = queryset.none()
        return queryset

    @action(detail=True, methods=['post'])
    def like(self, request, pk=None):
        post = self.get_object()
        try:
            profile = request.user.profile
        except Exception:
            return Response({'error': 'Profile not found'}, status=status.HTTP_400_BAD_REQUEST)
            
        if post.likes.filter(id=profile.id).exists():
            post.likes.remove(profile)
            liked = False
        else:
            post.likes.add(profile)
            liked = True
        return Response({
            'liked': liked,
            'likes_count': post.get_likes_count()
        })

    def perform_create(self, serializer):
        try:
            profile = self.request.user.profile
            post = serializer.save(author=profile)
            
            # Notify group members (limited to 20 for performance)
            if post.group:
                members = post.group.members.filter(groupmembership__status='active').exclude(id=profile.id)[:20]
                for member in members:
                    send_push_notification(
                        user=member.user, # Profile -> User
                        title=f"New Post in {post.group.name}",
                        message=f"{profile.full_name} posted: {post.content[:40]}{'...' if len(post.content) > 40 else ''}",
                        notification_type="new_post",
                        data={'post_id': post.id, 'group_id': post.group.id}
                    )

        except Exception as e:
            # Handle potential missing profile or notification errors
            print(f"Error in post creation/notification: {e}")
            if not serializer.instance: # If save failed before
                 serializer.save()

class PostImageViewSet(viewsets.ModelViewSet):
    queryset = PostImage.objects.all()
    serializer_class = PostImageSerializer
    permission_classes = [permissions.IsAuthenticated, IsPostImageAuthorOrReadOnly]

class CommentViewSet(viewsets.ModelViewSet):
    queryset = Comment.objects.all().order_by('-created_at')
    serializer_class = CommentSerializer
    permission_classes = [permissions.IsAuthenticated, IsAuthorOrReadOnly]

    def get_queryset(self):
        queryset = Comment.objects.all().order_by('-created_at')
        post_id = self.request.query_params.get('post_id')
        if post_id:
            queryset = queryset.filter(post_id=post_id)
        return queryset

    def perform_create(self, serializer):
        try:
            profile = self.request.user.profile
            serializer.save(author=profile)
        except Exception:
            serializer.save()

class ReplyViewSet(viewsets.ModelViewSet):
    queryset = Reply.objects.all().order_by('created_at')
    serializer_class = ReplySerializer
    permission_classes = [permissions.IsAuthenticated, IsAuthorOrReadOnly]

    def perform_create(self, serializer):
        try:
            profile = self.request.user.profile
            serializer.save(author=profile)
        except Exception:
            serializer.save()

