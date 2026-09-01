from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.DashboardHomeView.as_view(), name='home'),

    # Users
    path('users/', views.UsersListView.as_view(), name='users'),
    path('users/<int:pk>/', views.UserDetailView.as_view(), name='user_detail'),

    # Groups
    path('groups/', views.GroupsListView.as_view(), name='groups'),
    path('groups/<int:pk>/', views.GroupDetailView.as_view(), name='group_detail'),

    # Organisations
    path('organisations/', views.OrganisationsListView.as_view(), name='organisations'),
    path('organisations/<int:pk>/', views.OrganisationDetailView.as_view(), name='organisation_detail'),

    # Finance
    path('finance/', views.FinanceView.as_view(), name='finance'),

    # Campaigns
    path('campaigns/', views.CampaignsView.as_view(), name='campaigns'),

    # SMS & Notifications
    path('sms/', views.SMSView.as_view(), name='sms'),

    # Settings
    path('settings/', views.SettingsView.as_view(), name='settings'),

    # Vendors & Insurance
    path('vendors/', views.VendorsView.as_view(), name='vendors'),

    # HTMX Live Stats
    path('api/quick-stats/', views.QuickStatsView.as_view(), name='quick_stats'),
]
