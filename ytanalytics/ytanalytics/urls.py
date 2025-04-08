"""
URL configuration for ytanalytics project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView, TemplateView
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from dashboard.views import (
    about, dashboard, home_view, custom_logout, dashboard_view, statistics_view,
    edit_profile, view_profile, signup_view, connect_youtube, connect_tiktok, 
    connect_instagram, youtube_search_view, youtube_channel_view, terms_of_service, 
    privacy_policy, connect_accounts_view, disconnect_platform, toggle_link, 
    network_view, user_directory, permissions_info, grant_analytics_access, 
    search_youtube_api, serve_dns_txt, video_statistics, analytics_info, request_analytics_access
)
from dashboard.api_views import statistics_api

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', RedirectView.as_view(url='/home/')),
    path('home/', home_view, name='home'),
    path('about/', about, name='about'),
    
    # Authentication URLs
    path('login/', auth_views.LoginView.as_view(template_name='auth/login.html'), name='login'),
    path('logout/', custom_logout, name='logout'),
    path('signup/', signup_view, name='signup'),
    
    # Social Auth
    path('auth/', include('social_django.urls', namespace='social')),
    
    # Dashboard and Profile
    path('dashboard/', login_required(dashboard), name='dashboard'),
    path('dashboard/statistics/', login_required(statistics_view), name='statistics'),
    path('edit-profile/', login_required(edit_profile), name='edit_profile'),
    path('profile/<str:username>/', login_required(view_profile), name='view_profile'),
    path('users/', login_required(user_directory), name='user_directory'),
    
    # Platform Connections
    path('connect/youtube/', connect_youtube, name='connect_youtube'),
    path('connect/tiktok/', connect_tiktok, name='connect_tiktok'),
    path('connect/instagram/', connect_instagram, name='connect_instagram'),
    path('connect-accounts/', login_required(connect_accounts_view), name='connect_accounts'),
    path('disconnect/<str:platform>/', login_required(disconnect_platform), name='disconnect_platform'),
    
    # Legal and Info Pages
    path('terms/', terms_of_service, name='terms'),
    path('privacy/', privacy_policy, name='privacy'),
    
    # Network Features
    path('link/<str:username>/', login_required(toggle_link), name='toggle_link'),
    path('network/', login_required(network_view), name='network'),
    
    # YouTube Features
    path('youtube/search/', login_required(youtube_search_view), name='youtube_search'),
    path('youtube/channel/<str:channel_id>/', login_required(youtube_channel_view), name='youtube_channel'),
    path('api/search-youtube/', login_required(search_youtube_api), name='search_youtube_api'),
    path('api/statistics/', login_required(statistics_api), name='statistics_api'),
    
    # Utilities
    path('.well-known/acme-challenge/<path:token>', serve_dns_txt, name='dns_txt'),
    path('statistics/<str:video_id>/', video_statistics, name='video_statistics'),
    path('analytics-info/', analytics_info, name='analytics_info'),
    path('request-analytics-access/', request_analytics_access, name='request_analytics_access'),
    
    # Permissions flow
    path('permissions-info/', login_required(permissions_info), name='permissions_info'),
    path('grant-analytics-access/', login_required(grant_analytics_access), name='grant_analytics_access'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
