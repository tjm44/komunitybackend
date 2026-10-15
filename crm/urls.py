from django.urls import path
from . import views

app_name = 'crm'

urlpatterns = [
    path('', views.CRMDashboardView.as_view(), name='dashboard'),
    path('leads/', views.LeadListView.as_view(), name='lead_list'),
    path('leads/<int:pk>/', views.LeadDetailView.as_view(), name='lead_detail'),
    path('leads/<int:pk>/edit/', views.LeadEditView.as_view(), name='lead_edit'),
    path('leads/<int:pk>/status/', views.LeadChangeStatusView.as_view(), name='lead_change_status'),
    path('leads/<int:pk>/reassign/', views.LeadReassignView.as_view(), name='lead_reassign'),
    path('leads/<int:pk>/activity/', views.LogActivityView.as_view(), name='lead_log_activity'),
    path('leads/<int:pk>/task/add/', views.CreateTaskView.as_view(), name='lead_create_task'),
    path('leads/<int:pk>/link-group/', views.LinkGroupView.as_view(), name='lead_link_group'),

    path('quick-capture/', views.QuickCaptureView.as_view(), name='quick_capture'),
    path('quick-capture/<int:pk>/success/', views.QuickCaptureSuccessView.as_view(), name='quick_capture_success'),

    path('follow-ups/', views.MyFollowUpsView.as_view(), name='my_followups'),
    path('tasks/<int:pk>/complete/', views.CompleteTaskView.as_view(), name='task_complete'),

    # Ambassadors Management & Onboarding
    path('ambassadors/', views.AmbassadorOnboardingView.as_view(), name='ambassadors'),

    # API / HTMX
    path('api/duplicate-check/', views.DuplicateCheckAPIView.as_view(), name='duplicate_check'),
]
