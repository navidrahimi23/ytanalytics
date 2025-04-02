from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

def dashboard(request):
    if request.user.is_authenticated:
        # Your logic to fetch YouTube analytics here
        return render(request, 'dashboard.html')
    else:
        return render(request, 'home.html')

def about(request):
    return render(request, 'about.html')

# Create your views here.
import google.auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

