from django.urls import path
from . import views
from . import api_views

urlpatterns = [
    path('dashboard/', views.dashboard, name='dashboard'),  # Changed from '' to 'dashboard/'
    path('dashboard/edit-profile/', views.edit_profile, name='edit_profile'),
    path('dashboard/profile/<str:username>/', views.view_profile, name='view_profile'),
    path('dashboard/profile/<str:username>/toggle-link/', views.toggle_link, name='toggle_link'),
    path('dashboard/youtube-search/', views.youtube_search_view, name='youtube_search'),
    path('dashboard/youtube-channel/<str:channel_id>/', views.youtube_channel_view, name='youtube_channel'),
    path('dashboard/api/search-youtube/', views.search_youtube_api, name='search_youtube_api'),
    path('dashboard/api/channel/<str:channel_id>/stats/', views.channel_stats_api, name='channel_stats_api'),
    path('dashboard/api/statistics/', api_views.statistics_api, name='statistics_api'),
    path('dashboard/statistics/', views.statistics_view, name='statistics'),
    path('network/', views.network_view, name='network'),
    path('network/connect/<str:username>/', views.connect_user, name='connect_user'),
    path('network/disconnect/<str:username>/', views.disconnect_user, name='disconnect_user'),
    path('dashboard/connect-accounts/', views.connect_accounts_view, name='connect_accounts'),
    path('dashboard/disconnect-<str:platform>/', views.disconnect_platform, name='disconnect_platform'),
    path('terms/', views.terms_of_service, name='terms'),
    path('privacy/', views.privacy_policy, name='privacy'),
    path('about-us/', views.about_us, name='about_us'),
]
