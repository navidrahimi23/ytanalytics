from django.urls import path
from . import views
# Import from main views module (not from views package)
from dashboard.views import youtube_search_view, youtube_channel_view  # Use absolute import

urlpatterns = [
    # Core dashboard pages
    path('dashboard/', views.dashboard, name='dashboard'),
    path('dashboard/edit-profile/', views.edit_profile, name='edit_profile'),
    path('dashboard/profile/<str:username>/', views.view_profile, name='view_profile'),
    
    # YouTube data functionality
    path('dashboard/statistics/', views.statistics_view, name='statistics'),
    path('dashboard/video/<str:video_id>/', views.video_statistics, name='video_statistics'),
    path('dashboard/connect-accounts/', views.connect_accounts_view, name='connect_accounts'),
    path('dashboard/disconnect-<str:platform>/', views.disconnect_platform, name='disconnect_platform'),
    
    # YouTube specific views
    path('youtube/connect/', views.connect_youtube, name='youtube_connect'),
    path('youtube/disconnect/', views.disconnect_platform, name='youtube_disconnect'),
    path('youtube/analytics/', views.dashboard_view, name='youtube_analytics'),
    path('youtube/video/<str:video_id>/', views.video_statistics, name='youtube_video_detail'),
    path('youtube/metrics/', views.get_channel_analytics, name='api_youtube_metrics'),
    
    # YouTube search functionality
    path('search/', youtube_search_view, name='youtube_search'),
    path('channel/<str:channel_id>/', youtube_channel_view, name='youtube_channel'),
    
    # Network and connections
    path('network/', views.network_view, name='network'),
    path('network/connect/<str:username>/', views.connect_user, name='connect_user'),
    path('network/disconnect/<str:username>/', views.disconnect_user, name='disconnect_user'),
    path('toggle-link/<str:username>/', views.toggle_link, name='toggle_link'),
    
    # Legal pages
    path('terms/', views.terms_of_service, name='terms'),
    path('privacy/', views.privacy_policy, name='privacy'),
    path('about-us/', views.about_us, name='about_us'),
]
