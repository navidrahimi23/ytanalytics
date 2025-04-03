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
from dashboard.views import custom_logout, home_view

urlpatterns = [
    path('admin/', admin.site.urls),
    path('auth/', include('social_django.urls', namespace='social')),  # Handles Google OAuth login
    path('dashboard/', login_required(TemplateView.as_view(template_name='auth/dashboard.html')), name='dashboard'),  # Dashboard for authenticated users
    path('accounts/', include('django.contrib.auth.urls')),  # Django authentication URLs
    path('home/', home_view, name='home'),  # Smart home route that handles both authenticated and unauthenticated users
    path('logout/', custom_logout, name='logout'),  # Custom logout view
    path('', RedirectView.as_view(url='/home/', permanent=True)),  # Redirect root to home
]
