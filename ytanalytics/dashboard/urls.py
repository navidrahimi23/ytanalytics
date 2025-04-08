from django.urls import path
from . import views
from . import api_views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),  # The dashboard view
    path('about/', views.about, name='about'),  # About page
    path('edit-profile/', views.edit_profile, name='edit_profile'),
    path('youtube-search/', views.youtube_search_view, name='youtube_search'),
    path('youtube-channel/<str:channel_id>/', views.youtube_channel_view, name='youtube_channel'),
    path('api/search-youtube/', views.search_youtube_api, name='search_youtube_api'),
    path('api/channel/<str:channel_id>/stats/', views.channel_stats_api, name='channel_stats_api'),
    path('api/statistics/', api_views.statistics_api, name='statistics_api'),
    path('statistics/', views.statistics_view, name='statistics'),  # Statistics page
    path('network/', views.network_view, name='network'),
    path('network/connect/<str:username>/', views.connect_user, name='connect_user'),
    path('network/disconnect/<str:username>/', views.disconnect_user, name='disconnect_user'),
    path('connect-accounts/', views.connect_accounts, name='connect_accounts'),
    path('disconnect-<str:platform>/', views.disconnect_platform, name='disconnect_platform'),
]
