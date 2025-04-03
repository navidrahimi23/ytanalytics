from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from social_django.utils import load_strategy
from social_django.models import UserSocialAuth

def dashboard(request):
    if request.user.is_authenticated:
        # Check if user has seen the login success page
        if not request.session.get('has_seen_login_success'):
            request.session['has_seen_login_success'] = True
            return redirect('login_success')
        # Your logic to fetch YouTube analytics here
        return render(request, 'dashboard.html')
    else:
        return render(request, 'home.html')

def about(request):
    return render(request, 'about.html')

def custom_logout(request):
    """Custom logout view that handles both Django and social auth logout."""
    try:
        # Get the user's social auth instance
        social_auth = UserSocialAuth.objects.get(user=request.user)
        # Load the strategy and disconnect
        strategy = load_strategy()
        social_auth.disconnect(strategy)
    except (UserSocialAuth.DoesNotExist, AttributeError):
        pass  # User doesn't have social auth
    
    # Perform Django logout
    logout(request)
    return redirect('/home/')

def home_view(request):
    """Smart home view that shows different templates based on authentication status."""
    if request.user.is_authenticated:
        return render(request, 'auth/home-logged-in.html')
    else:
        return render(request, 'dashboard/home.html')

# Create your views here.
import google.auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

