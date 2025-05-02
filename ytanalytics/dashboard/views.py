from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout, login, authenticate
from social_django.utils import load_strategy
from social_django.models import UserSocialAuth
from .youtube_analytics import get_youtube_analytics, TimeRange
from .youtube_api import search_channel, get_channel_stats, get_recent_video_stats
import json
from django.contrib import messages
from .forms import UserProfileForm, ExtendedProfileForm, SignUpForm
from django.http import JsonResponse, FileResponse
from django.template.loader import render_to_string
from django.contrib.auth.models import User
from django.db import models
from requests_oauthlib import OAuth2Session
from .models import UserProfile, ChannelStats, Link
import os
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Q
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from rest_framework.response import Response

logger = logging.getLogger(__name__)

# Signal handler to automatically connect YouTube channel after OAuth login
@receiver(post_save, sender=UserSocialAuth)
def connect_youtube_from_oauth(sender, instance, created, **kwargs):
    """
    Automatically connect YouTube channel to user profile after successful OAuth login
    """
    if instance.provider == 'google-oauth2':
        try:
            user = instance.user
            profile = user.userprofile
            
            # Check if the user has granted analytics access
            scope = instance.extra_data.get('scope', '')
            if 'https://www.googleapis.com/auth/yt-analytics.readonly' in scope:
                profile.has_analytics_access = True
                logger.info(f"Analytics access granted for user: {user.username}")
            
            # If this is the first time user logs in with Google or profile has no channel yet
            if created or not profile.youtube_channel or not profile.youtube_connected:
                logger.info(f"Attempting to fetch YouTube channel info for user: {user.username}")
                
                # Get tokens
                access_token = instance.get_access_token(load_strategy())
                refresh_token = instance.extra_data.get('refresh_token')
                client_id = instance.extra_data.get('client_id')
                client_secret = instance.extra_data.get('client_secret')
                
                # Create credentials object
                credentials = Credentials(
                    token=access_token,
                    refresh_token=refresh_token,
                    token_uri='https://oauth2.googleapis.com/token',
                    client_id=client_id,
                    client_secret=client_secret
                )
                
                # Try to get YouTube channel info
                youtube = build('youtube', 'v3', credentials=credentials)
                channels_response = youtube.channels().list(
                    part='snippet',
                    mine=True
                ).execute()
                
                # If channel exists, save it to user profile
                if channels_response.get('items'):
                    channel_info = channels_response['items'][0]
                    channel_id = channel_info['id']
                    channel_title = channel_info['snippet']['title']
                    
                    logger.info(f"Found YouTube channel for {user.username}: {channel_title} ({channel_id})")
                    
                    # Store channel information
                    profile.youtube_channel = channel_id
                    profile.youtube_connected = True
                    profile.save()
                    
                    logger.info(f"Successfully connected YouTube channel for user: {user.username}")
            else:
                # Save the profile if only analytics access changed
                profile.save()
        except Exception as e:
            logger.error(f"Error connecting YouTube channel for user {instance.user.username}: {str(e)}")
            # Don't block the login flow if this fails

@login_required
def dashboard(request):
    """Main dashboard view with YouTube analytics overview"""
    user = request.user
    profile = user.userprofile
    
    # Check if YouTube is connected
    if not profile.youtube_connected:
        # Display dashboard with connection prompt
        return render(request, 'auth/dashboard.html', {
            'user': user,
            'profile': profile,
            'youtube_connected': False
        })
    
    # Get analytics data
    analytics_data = get_youtube_analytics(user)
    
    # Get recent videos
    recent_videos = get_recent_videos(user, limit=10)
    
    # Prepare dashboard context - Simplify analytics and correct video key
    context = {
        'user': user,
        'profile': profile,
        'youtube_connected': True,
        'analytics_result': analytics_data, # Pass the whole result
        'videos': recent_videos # Use the key 'videos' as expected by the template
    }
    
    return render(request, 'auth/dashboard.html', context)

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

@login_required
def dashboard_view(request):
    """View for the dashboard with user information and navigation."""
    analytics_result = get_youtube_analytics(request.user)
    
    # Check if user has analytics permission
    has_analytics_permission = False
    needs_analytics_permission = False
    try:
        social = request.user.social_auth.get(provider='google-oauth2')
        # Check if user has the analytics scope in their token
        if 'https://www.googleapis.com/auth/yt-analytics.readonly' in social.extra_data.get('scope', ''):
            has_analytics_permission = True
        else:
            # Only set needs_analytics_permission if user is connected but missing the scope
            if request.user.userprofile.youtube_connected:
                needs_analytics_permission = True
    except Exception as e:
        logger.error(f"Error checking analytics permission: {str(e)}")
        # If we can't check permissions but user is connected, suggest getting permission
        if request.user.userprofile.youtube_connected:
            needs_analytics_permission = True
    
    # Check if the result indicates we need analytics permission
    if analytics_result and analytics_result.get('needs_analytics_permission'):
        needs_analytics_permission = True
    
        
        # If user has a channel and data is available, process it
        if analytics_result.get('has_data'):
            basic_metrics = analytics_result.get('basic_metrics', [])
            
            dates = []
            views = []
            watch_time = []
            subscribers = []

            for row in basic_metrics:
                dates.append(row[0])  # Date
                views.append(row[1])  # Views
                watch_time.append(row[2])  # Estimated minutes watched
                # The index for subscribers might be different depending on the API response
                if len(row) > 4:
                    subscribers.append(row[4])  # Subscribers gained
                else:
                    subscribers.append(0)  # Default to 0 if not available

            # Calculate totals for summary display
            total_views = sum(views) if views else 0
            total_watch_time = sum(watch_time) if watch_time else 0
            watch_time_hours = round(total_watch_time / 60, 1)  # Convert minutes to hours
            total_subscribers = sum(subscribers) if subscribers else 0

@login_required
def statistics_view(request):
    """View for the statistics page. Passes initial data to the template."""
    
    profile = request.user.userprofile
    analytics_result = None
    
    # If user has connected their YouTube account, fetch initial analytics data (e.g., last 30 days)
    if profile.youtube_connected:
        try:
            # Get analytics data for the user with a default time range
            # The template JS will fetch updates for different ranges
            analytics_result = get_youtube_analytics(request.user) # Uses default days=30
            logger.info(f"Fetched initial analytics for statistics page for user {request.user.username}")
        except Exception as e:
            logger.error(f"Error fetching initial analytics for user {request.user.username} statistics: {str(e)}")
            # Don't crash the page load, JS will handle showing an error
            analytics_result = {'success': False, 'message': f"Error loading initial analytics: {str(e)}"}
    else:
        # If YouTube not connected, indicate this
        analytics_result = {'success': False, 'message': 'YouTube not connected'}
        logger.info(f"User {request.user.username} attempted to view statistics without connecting YouTube")
        
    context = {
        'analytics_result': analytics_result, # Pass initial result (or error state)
        'youtube_connected': profile.youtube_connected # Let template know connection status
    }
    
    return render(request, 'auth/statistics.html', context)

@login_required
def edit_profile(request):
    user = request.user
    logger.info(f"Edit profile accessed by user: {user.username}")
    
    try:
        # Get or create UserProfile
        try:
            user_profile = UserProfile.objects.get(user=user)
        except UserProfile.DoesNotExist:
            user_profile = UserProfile.objects.create(user=user)
        
        if request.method == 'POST':
            logger.debug(f"Processing POST request for edit_profile from user: {user.username}")
            logger.debug(f"Request headers: {request.headers}")
            
            # Create forms with POST data
            user_form = ExtendedProfileForm(request.POST, instance=user)
            profile_form = UserProfileForm(request.POST, request.FILES, instance=user_profile)
            
            if user_form.is_valid() and profile_form.is_valid():
                # Save the forms
                user_form.save()
                profile_form.save()
                
                # Handle the AJAX request
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    logger.info(f"Profile updated successfully for user: {user.username}")
                    return JsonResponse({
                        'success': True, 
                        'username': user.username
                    })
                
                # For non-AJAX requests
                messages.success(request, 'Your profile was successfully updated!')
                return redirect('dashboard')
            else:
                # Form validation failed
                logger.warning(f"Form validation failed for user: {user.username}")
                logger.warning(f"User form errors: {user_form.errors}")
                logger.warning(f"Profile form errors: {profile_form.errors}")
                
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    # For AJAX requests, render form with errors
                    html = render_to_string('auth/profile_form.html', {
                        'user_form': user_form,
                        'profile_form': profile_form,
                        'user': user,  # Add user context to ensure form has initial values
                    }, request=request)  # Pass request to ensure CSRF token is included
                    
                    return JsonResponse({
                        'success': False,
                        'html': html
                    })
                
                # For non-AJAX requests
                return render(request, 'auth/profile.html', {
                    'user_form': user_form,
                    'profile_form': profile_form
                })
        else:
            # GET request - initial form load
            user_form = ExtendedProfileForm(instance=user)
            profile_form = UserProfileForm(instance=user_profile)
            
            if request.GET.get('ajax') == '1':
                # For AJAX requests to load the form
                logger.info(f"Loading profile form via AJAX for user: {user.username}")
                html = render_to_string('auth/profile_form.html', {
                    'user_form': user_form,
                    'profile_form': profile_form,
                    'user': user,  # Add user context to ensure form has initial values
                }, request=request)  # Pass request to ensure CSRF token is included
                
                # Debug logging
                logger.debug(f"Submit button in HTML: {'submit-button' in html}")
                logger.debug(f"CSRF token in HTML: {'csrfmiddlewaretoken' in html}")
                
                return JsonResponse({
                    'html': html
                })
        
        # Standard (non-AJAX) GET request
        return render(request, 'auth/profile.html', {
            'user_form': user_form,
            'profile_form': profile_form,
        })
    except Exception as e:
        logger.error(f"Error in edit_profile: {str(e)}", exc_info=True)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': False,
                'error': str(e)
            }, status=500)
        messages.error(request, f"An error occurred: {str(e)}")
        return redirect('dashboard')

@login_required
def view_profile(request, username):
    """View for displaying a user's profile."""
    # Get the requested user or return 404 if not found
    user = get_object_or_404(User, username=username)
    is_own_profile = (request.user == user)
    
    # Get link status between current user and profile user
    link_status = None
    if not is_own_profile:
        link_status = Link.get_link_status(request.user, user)
    
    # In a future implementation, we could check privacy settings here
    # For now, all profiles are viewable
    
    # Get YouTube analytics data 
    analytics_result = None
    show_private_stats = is_own_profile or link_status == 'mutual'
    recent_videos = []
    
    if show_private_stats:
        try:
            # For mutual links, get all-time analytics instead of just 30 days
            if not is_own_profile and link_status == 'mutual':
                analytics_result = get_youtube_analytics(user, time_range=TimeRange.ALL_TIME)  # Use TimeRange.ALL_TIME instead of days=9999
                # Get recent videos for mutually linked users
                if user.userprofile.youtube_connected and user.userprofile.youtube_channel:
                    recent_videos = get_recent_videos(user, limit=10)
            else:
                analytics_result = get_youtube_analytics(user)
            
            # --- Remove calculation logic - totals are now in analytics_result ---
            # Calculate summary metrics if data is available
            # if analytics_result and analytics_result.get('has_data') and analytics_result.get('basic_metrics'):
            #     basic_metrics = analytics_result.get('basic_metrics', [])
            #     
            #     # Calculate totals
            #     total_views = sum(row[1] for row in basic_metrics)
            #     total_watch_time = sum(row[2] for row in basic_metrics)
            #     watch_time_hours = round(total_watch_time / 60, 1)  # Convert minutes to hours
            #     
            #     # Sum subscribers gained (may be at index 4)
            #     total_subscribers = 0
            #     if basic_metrics and len(basic_metrics[0]) > 4:
            #         total_subscribers = sum(row[4] for row in basic_metrics)
            #     
            #     # Add summary metrics to the result
            #     analytics_result['total_views'] = total_views
            #     analytics_result['watch_time_hours'] = watch_time_hours
            #     analytics_result['subscribers'] = total_subscribers
                
        except Exception as e:
            analytics_result = None
            messages.warning(request, f"Couldn't load YouTube analytics: {str(e)}")
    
    context = {
        'profile_user': user,  # Using 'profile_user' to avoid confusion with 'user' in templates
        'is_own_profile': is_own_profile,
        'link_status': link_status,
        'analytics_result': analytics_result,
        'has_channel': analytics_result.get('has_channel', False) if analytics_result else False,
        'has_data': analytics_result.get('has_data', False) if analytics_result else False,
        'channel_name': analytics_result.get('channel_name') if analytics_result else None,
        'analytics_message': analytics_result.get('message') if analytics_result else 'Could not load analytics data',
        'show_private_stats': show_private_stats,
        'recent_videos': recent_videos,
    }
    
    return render(request, 'auth/view_profile.html', context)

@login_required
def toggle_link(request, username):
    """Toggle link status with another user"""
    target_user = get_object_or_404(User, username=username)
    
    # Don't allow linking to self
    if request.user == target_user:
        messages.error(request, "You cannot link to yourself.")
        return redirect('view_profile', username=username)
    
    # Check if link already exists
    link_exists = Link.is_linked(request.user, target_user)
    
    if link_exists:
        # Remove the link
        Link.objects.filter(from_user=request.user, to_user=target_user).delete()
        messages.success(request, f"You've unlinked from {username}.")
    else:
        # Create the link
        Link.objects.create(from_user=request.user, to_user=target_user)
        messages.success(request, f"You've linked to {username}.")
        
        # Check if it's now a mutual link
        if Link.is_linked(target_user, request.user):
            messages.success(request, f"You and {username} are now mutually linked!")
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        link_status = Link.get_link_status(request.user, target_user)
        return JsonResponse({
            'success': True,
            'link_status': link_status,
        })
    
    return redirect('view_profile', username=username)

@login_required
def network_view(request):
    search_query = request.GET.get('search', '')
    
    # Get mutually connected users using Link model
    from .models import Link
    linked_user_objects = Link.get_linked_users(request.user)
    linked_profiles = UserProfile.objects.filter(user__in=linked_user_objects)
    
    # Add YouTube analytics data to the linked profiles
    for profile in linked_profiles:
        # Initialize with default values from model methods
        profile.subscribers = profile.get_subscribers()
        profile.total_views = profile.get_views()
        profile.video_count = profile.get_video_count()
        
        # Try to get analytics data if the user has a YouTube channel
        if profile.youtube_connected and profile.youtube_channel:
            try:
                # Always use a long timeframe to get comprehensive data
                analytics_result = get_youtube_analytics(profile.user, time_range=TimeRange.ALL_TIME)
                if analytics_result and analytics_result.get('has_data', False):
                    # --- Remove calculation logic - Use values directly from analytics_result ---
                    # total_views = profile.total_views # Keep default if calculation fails
                    # subscribers = profile.subscribers # Keep default
                    # if analytics_result.get('basic_metrics'):
                    #     basic_metrics = analytics_result.get('basic_metrics', [])
                    #     try:
                    #         total_views = sum(row[1] for row in basic_metrics if len(row) > 1)
                    #         # Sum subscribers gained (check index exists)
                    #         if basic_metrics and len(basic_metrics[0]) > 4:
                    #             subscribers = sum(row[4] for row in basic_metrics)
                    #         else:
                    #             # Fallback if subscriber data isn't in the expected column
                    #             subscribers = analytics_result.get('total_subscribers', profile.subscribers) # Attempt fallback
                    #     except (IndexError, TypeError) as calc_e:
                    #         logger.error(f"Error calculating stats for {profile.user.username} in network_view: {calc_e}")

                    # Update profile attributes with calculated values
                    profile.subscribers = analytics_result.get('subscribers', profile.subscribers) # Get lifetime total
                    profile.total_views = analytics_result.get('views', profile.total_views) # Get lifetime total
            except Exception as e:
                logger.error(f"Error fetching analytics for user {profile.user.username}: {str(e)}")
    
    # Search functionality
    search_results = []
    if search_query:
        search_results = User.objects.filter(
            Q(username__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        ).exclude(id=request.user.id)
        
        # Convert to profiles and add link status
        search_profiles = []
        for user in search_results:
            profile = user.userprofile
            profile.link_status = Link.get_link_status(request.user, user)
            
            # Add the same data for search results
            profile.subscribers = profile.get_subscribers()
            profile.total_views = profile.get_views()
            profile.video_count = profile.get_video_count()
            
            # Try to get analytics data if the user has a YouTube channel
            if profile.youtube_connected and profile.youtube_channel:
                try:
                    analytics_result = get_youtube_analytics(profile.user, time_range=TimeRange.ALL_TIME)
                    if analytics_result and analytics_result.get('has_data', False):
                        # --- Remove calculation logic - Use values directly from analytics_result ---
                        # total_views = profile.total_views # Keep default if calculation fails
                        # subscribers = profile.subscribers # Keep default
                        # if analytics_result.get('basic_metrics'):
                        #     basic_metrics = analytics_result.get('basic_metrics', [])
                        #     try:
                        #         total_views = sum(row[1] for row in basic_metrics if len(row) > 1)
                        #         # Sum subscribers gained (check index exists)
                        #         if basic_metrics and len(basic_metrics[0]) > 4:
                        #             subscribers = sum(row[4] for row in basic_metrics)
                        #         else:
                        #             # Fallback if subscriber data isn't in the expected column
                        #             subscribers = analytics_result.get('total_subscribers', profile.subscribers) # Attempt fallback
                        #     except (IndexError, TypeError) as calc_e:
                        #         logger.error(f"Error calculating stats for {profile.user.username} in network_view: {calc_e}")

                        # Update profile attributes with calculated values
                        profile.subscribers = analytics_result.get('subscribers', profile.subscribers) # Get lifetime total
                        profile.total_views = analytics_result.get('views', profile.total_views) # Get lifetime total
                except Exception as e:
                    logger.error(f"Error fetching analytics for search result {profile.user.username}: {str(e)}")
            
            search_profiles.append(profile)
        
        search_results = search_profiles
    
    context = {
        'linked_users': linked_profiles,
        'search_query': search_query,
        'search_results': search_results,
    }
    
    return render(request, 'auth/network.html', context)

@login_required
def connect_user(request, username):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'})
        
    target_user = get_object_or_404(User, username=username)
    if target_user == request.user:
        return JsonResponse({'success': False, 'error': 'Cannot connect with yourself'})
    
    # Create link using the Link model
    from .models import Link
    Link.objects.get_or_create(from_user=request.user, to_user=target_user)
    
    # Also add to M2M field for backward compatibility
    request.user.userprofile.connected_users.add(target_user)
    
    return JsonResponse({'success': True})

@login_required
def disconnect_user(request, username):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid method'})
        
    target_user = get_object_or_404(User, username=username)
    
    # Remove link using the Link model
    from .models import Link
    Link.objects.filter(from_user=request.user, to_user=target_user).delete()
    
    # Also remove from M2M field for backward compatibility
    request.user.userprofile.connected_users.remove(target_user)
    
    return JsonResponse({'success': True})

@login_required
def user_directory(request):
    """View for displaying a directory of all users."""
    # Get all users except the current one
    users = User.objects.exclude(id=request.user.id).select_related('userprofile')
    
    # Simple search functionality
    search_query = request.GET.get('search', '')
    if search_query:
        users = users.filter(
            models.Q(username__icontains=search_query) | 
            models.Q(first_name__icontains=search_query) | 
            models.Q(last_name__icontains=search_query)
        )
    
    context = {
        'users': users,
        'search_query': search_query
    }
    
    return render(request, 'auth/user_directory.html', context)

def signup_view(request):
    if request.method == 'POST':
        form = SignUpForm(request.POST)
        try:
            if form.is_valid():
                user = form.save()
                raw_password = form.cleaned_data.get('password1')
                user = authenticate(username=user.username, password=raw_password)
                login(request, user)
                messages.success(request, 'Account created successfully!')
                logger.info(f"New user account created: {user.username}")
                return redirect('dashboard')
            else:
                # Log form errors for debugging
                logger.warning(f"Signup form validation failed: {form.errors}")
                for field, errors in form.errors.items():
                    for error in errors:
                        messages.error(request, f"{field}: {error}")
        except Exception as e:
            logger.error(f"Error in signup process: {str(e)}", exc_info=True)
            messages.error(request, f"An error occurred while creating your account: {str(e)}")
    else:
        form = SignUpForm()
    return render(request, 'auth/signup.html', {'form': form})

@login_required
def connect_accounts_view(request):
    """View for the account connections page"""
    profile = request.user.userprofile
    
    # Check if user has analytics permissions
    has_analytics_permission = False
    if profile.youtube_connected:
        try:
            social = request.user.social_auth.get(provider='google-oauth2')
            scopes = social.extra_data.get('scope', '').split(' ')
            if 'https://www.googleapis.com/auth/yt-analytics.readonly' in scopes:
                has_analytics_permission = True
        except Exception as e:
            logger.error(f"Error checking analytics permissions: {str(e)}")
    
    return render(request, 'dashboard/connect_accounts.html', {
        'user': request.user,
        'profile': profile,
        'has_analytics_permission': has_analytics_permission
    })

@login_required
def connect_youtube(request):
    """Handle YouTube OAuth connection"""
    if request.GET.get('code'):
        # Handle OAuth callback
        profile = request.user.userprofile
        profile.youtube_connected = True
        
        # Check if we have analytics permissions
        try:
            social = request.user.social_auth.get(provider='google-oauth2')
            scopes = social.extra_data.get('scope', '').split(' ')
            
            # Check if analytics scope is present
            if 'https://www.googleapis.com/auth/yt-analytics.readonly' in scopes:
                profile.has_analytics_access = True
        except Exception as e:
            logger.error(f"Error checking analytics permissions: {str(e)}")
        
        profile.save()
        messages.success(request, 'YouTube account connected successfully!')
        return redirect('dashboard')
    
    # Build the OAuth URL with explicit scopes
    auth_url = '/auth/login/google-oauth2/'
    
    # Include only needed scopes for basic analytics
    scopes = [
        'https://www.googleapis.com/auth/youtube.readonly',
        'https://www.googleapis.com/auth/yt-analytics.readonly'
    ]
    
    # URL encode the scopes
    import urllib.parse
    encoded_scopes = urllib.parse.quote(' '.join(scopes))
    
    # Build the complete auth URL
    auth_url += f'?scope={encoded_scopes}&next=/dashboard/'
    
    return redirect(auth_url)

@login_required
def connect_tiktok(request):
    """Handle TikTok OAuth connection"""
    if request.GET.get('code'):
        # Handle TikTok OAuth callback
        profile = request.user.userprofile
        profile.tiktok_connected = True
        profile.save()
        messages.success(request, 'TikTok account connected successfully!')
        return redirect('edit_profile')
    return redirect('tiktok_oauth_url')

@login_required
def connect_instagram(request):
    """Handle Instagram OAuth connection"""
    if request.GET.get('code'):
        # Handle Instagram OAuth callback
        profile = request.user.userprofile
        profile.instagram_connected = True
        profile.save()
        messages.success(request, 'Instagram account connected successfully!')
        return redirect('edit_profile')
    return redirect('instagram_oauth_url')

def serve_dns_txt(request):
    file_path = os.path.join(settings.BASE_DIR, 'dns.txt')
    return FileResponse(open(file_path, 'rb'), content_type='text/plain')

def terms_of_service(request):
    """View for Terms of Service page."""
    return render(request, 'terms_of_service.html')

def privacy_policy(request):
    """View for Privacy Policy page."""
    return render(request, 'privacy_policy.html')

@login_required
@require_POST
def disconnect_platform(request, platform):
    """Disconnect a specific platform"""
    if platform not in ['youtube', 'tiktok', 'instagram']:
        return JsonResponse({'success': False, 'message': 'Invalid platform'})
    
    profile = request.user.userprofile
    
    if platform == 'youtube':
        profile.youtube_connected = False
        profile.youtube_channel = ''
        profile.has_analytics_access = False
        
        # Also remove OAuth token if present
        try:
            social_auth = request.user.social_auth.filter(provider='google-oauth2')
            if social_auth.exists():
                social_auth.delete()
        except Exception as e:
            logger.error(f"Error removing OAuth token: {str(e)}")
    
    profile.save()
    
    return JsonResponse({
        'success': True,
        'message': f'{platform.title()} account disconnected successfully'
    })

@login_required
def youtube_search_view(request):
    """View for searching YouTube channels by name or ID without OAuth"""
    search_query = request.GET.get('q', '')
    search_results = None
    
    if search_query:
        # Perform search if a query is provided
        search_results = search_channel(search_query)
    
    context = {
        'search_query': search_query,
        'search_results': search_results,
        'user_profile': request.user.userprofile,
    }
    
    return render(request, 'auth/youtube_search.html', context)

@login_required
def youtube_channel_view(request, channel_id):
    """View detailed analytics for a specific YouTube channel"""
    # Get channel information
    channel_data = get_channel_stats(channel_id)
    
    # If channel data was successfully fetched, store it for historical tracking
    if channel_data.get('success') and channel_data.get('channel'):
        channel = channel_data.get('channel')
        today = timezone.now().date()
        
        # Check if this is the first time we're recording stats for this channel
        if ChannelStats.objects.filter(channel_id=channel_id).count() <= 1:
            view_count = int(channel.get('view_count', 0))
            subscriber_count = int(channel.get('subscriber_count', 0))
            
            # Create 10 days of sample data with slight variations
            for days_ago in range(1, 11):
                sample_date = today - timedelta(days=days_ago)
                
                # Slightly decrease the counts as we go back in time (5-10% drop per day)
                view_decrease = int(view_count * (0.05 + (0.005 * days_ago)))
                sub_decrease = int(subscriber_count * (0.05 + (0.005 * days_ago)))
                
                historical_views = max(0, view_count - view_decrease)
                historical_subscribers = max(0, subscriber_count - sub_decrease)
                
                # Save the historical stats
                ChannelStats.objects.get_or_create(
                    channel_id=channel_id,
                    date=sample_date,
                    defaults={
                        'views': historical_views,
                        'subscribers': historical_subscribers
                    }
                )
        
        # Save or update today's stats
        ChannelStats.objects.update_or_create(
            channel_id=channel_id,
            date=today,
            defaults={
                'views': channel.get('view_count', 0),
                'subscribers': channel.get('subscriber_count', 0)
            }
        )
    
    # Get recent videos and their stats
    videos_data = get_recent_video_stats(channel_id)
    
    # Prepare data for charts
    video_titles = []
    view_counts = []
    like_counts = []
    comment_counts = []
    
    if videos_data.get('success') and videos_data.get('videos'):
        videos = videos_data['videos']
        
        # Sort videos by published date (newest first) for consistency
        videos.sort(key=lambda x: x['published_at'], reverse=True)
        
        # Extract data for charts (limit to 10 videos)
        for video in videos[:10]:
            video_titles.append(video['title'])
            view_counts.append(int(video['view_count']))
            like_counts.append(int(video['like_count']))
            comment_counts.append(int(video['comment_count']))
    
    # Create context with all data
    context = {
        'channel': channel_data.get('channel') if channel_data.get('success') else None,
        'videos': videos_data.get('videos') if videos_data.get('success') else [],
        'error': channel_data.get('error') or videos_data.get('error'),
        'error_message': channel_data.get('message') or videos_data.get('message'),
        'user_profile': request.user.userprofile,
        'chart_data': {
            'video_titles': json.dumps(video_titles),
            'view_counts': json.dumps(view_counts),
            'like_counts': json.dumps(like_counts),
            'comment_counts': json.dumps(comment_counts),
        },
        'has_historical_data': ChannelStats.objects.filter(channel_id=channel_id).count() > 1
    }
    
    return render(request, 'auth/youtube_channel_details.html', context)

@login_required
def search_youtube_api(request):
    """AJAX endpoint to search YouTube channels"""
    search_query = request.GET.get('q', '')
    
    if not search_query:
        return JsonResponse({'success': False, 'message': 'No search query provided'})
    
    results = search_channel(search_query)
    
    return JsonResponse(results)

@login_required
@require_GET
def channel_stats_api(request, channel_id):
    """API endpoint for getting channel stats over time"""
    time_range = request.GET.get('range', '1month')
    metric = request.GET.get('metric', 'subscribers')
    
    # Calculate date range based on request
    today = timezone.now().date()
    if time_range == '1day':
        start_date = today - timedelta(days=1)
    elif time_range == '10days':
        start_date = today - timedelta(days=10)
    elif time_range == '1month':
        start_date = today - timedelta(days=30)
    elif time_range == '6months':
        start_date = today - timedelta(days=180)
    elif time_range == '1year':
        start_date = today - timedelta(days=365)
    else:  # All time
        start_date = None
    
    # Query for stats in the date range
    query = ChannelStats.objects.filter(channel_id=channel_id)
    if start_date:
        query = query.filter(date__gte=start_date)
    
    # Order by date ascending to show trend properly
    stats = query.order_by('date')
    
    # Format data for response
    dates = [stat.date.strftime('%Y-%m-%d') for stat in stats]
    
    if metric == 'subscribers':
        values = [stat.subscribers for stat in stats]
        metric_label = 'Subscribers'
    else:  # views
        values = [stat.views for stat in stats]
        metric_label = 'Views'
    
    # If we have less than 2 data points, we don't have enough for a trend
    if len(dates) < 2:
        return JsonResponse({
            'success': False,
            'message': 'Not enough historical data available for the selected time range.',
            'data_points': len(dates)
        })
    
    return JsonResponse({
        'success': True,
        'channel_id': channel_id,
        'dates': dates,
        'values': values,
        'metric': metric,
        'metric_label': metric_label,
        'time_range': time_range
    })


@login_required
def channel_history_api(request, channel_id):
    """API endpoint to retrieve historical stats for a channel"""
    metric = request.GET.get('metric', 'subscribers')
    time_range = request.GET.get('range', '30')
    
    try:
        time_range = int(time_range)
    except ValueError:
        time_range = 30
    
    # Get historical stats from the database
    cutoff_date = timezone.now() - timedelta(days=time_range)
    stats = ChannelStats.objects.filter(channel_id=channel_id, date__gte=cutoff_date).order_by('date')
    
    # Format dates and extract the requested metric
    dates = [stat.date.strftime('%Y-%m-%d') for stat in stats]
    
    if metric == 'subscribers':
        values = [stat.subscribers for stat in stats]
        metric_label = 'Subscribers'
    else:  # views
        values = [stat.views for stat in stats]
        metric_label = 'Views'
    
    # If we have less than 2 data points, we don't have enough for a trend
    if len(dates) < 2:
        return JsonResponse({
            'success': False,
            'message': 'Not enough historical data available for the selected time range.',
            'data_points': len(dates)
        })
    
    return JsonResponse({
        'success': True,
        'channel_id': channel_id,
        'dates': dates,
        'values': values,
        'metric': metric,
        'metric_label': metric_label,
        'time_range': time_range
    })

def empty_stats_response():
    """Helper function to return empty stats with success flag"""
    return JsonResponse({
        'success': True,
        'subscribers': 0,
        'subscriber_change': 0,
        'views': 0,
        'view_change': 0,
        'watch_time': 0,
        'watch_time_change': 0,
        'likes': 0,
        'like_change': 0,
        'dates': [],
        'views_data': [],
        'watch_time_data': [],
        'subscribers_data': []
    })

@login_required
def permissions_info(request):
    """View for the permissions info page explaining why we need analytics access"""
    return render(request, 'auth/permissions_info.html')


def get_video_details(user, video_id):
    """Get detailed information about a specific video"""
    from .youtube_analytics import get_user_credentials
    from .youtube_api import build_youtube_service
    
    try:
        profile = user.userprofile
        
        # If user has no YouTube channel, return None
        if not profile.youtube_connected:
            return None
            
        # Get credentials for API access
        credentials = get_user_credentials(user)
        if not credentials:
            return None
        
        # Build YouTube API client
        youtube = build('youtube', 'v3', credentials=credentials)
        
        # Get video details
        video_response = youtube.videos().list(
            part='snippet,statistics,contentDetails',
            id=video_id
        ).execute()
        
        if not video_response.get('items'):
            return None
            
        # Process video data
        item = video_response['items'][0]
        snippet = item['snippet']
        statistics = item['statistics']
        content_details = item['contentDetails']
        
        # Parse duration
        duration = content_details['duration']
        duration_str = parse_duration(duration)
        
        # Get video published date
        published_date = datetime.fromisoformat(snippet['publishedAt'].replace('Z', '+00:00'))
        
        # Return formatted video details
        return {
            'id': video_id,
            'title': snippet['title'],
            'description': snippet.get('description', ''),
            'channel_id': snippet['channelId'],
            'channel_title': snippet['channelTitle'],
            'published_at': published_date,
            'published_at_str': published_date.strftime('%b %d, %Y'),
            'thumbnail': snippet['thumbnails']['high']['url'],
            'views': int(statistics.get('viewCount', 0)),
            'likes': int(statistics.get('likeCount', 0)),
            'comments': int(statistics.get('commentCount', 0)),
            'duration': duration_str,
            'duration_seconds': parse_duration(duration, return_seconds=True)
        }
    except Exception as e:
        logger.error(f"Error getting video details for {video_id}: {str(e)}")
        return None

@login_required
def video_statistics(request, video_id):
    """View for displaying video statistics"""
    # Get video details
    video_details = get_video_details(request.user, video_id)
    if not video_details:
        messages.error(request, "Video not found or not accessible.")
        return redirect('dashboard')
    
    return render(request, 'dashboard/video_statistics.html', {
        'video': video_details,
        'user': request.user,
        'profile': request.user.userprofile
    })

def get_channel_analytics(user):
    """Get channel analytics for the dashboard display"""
    # Get analytics data from YouTube API
    analytics_result = get_youtube_analytics(user)
    
    # Default empty stats
    stats = {
        'subscribers': 0,
        'views': 0,
        'watch_time': 0,
        'likes': 0,
        'subscriber_change': 0,
        'view_change': 0,
        'watch_time_change': 0,
        'like_change': 0
    }
    
    # If we have data, process it for display
    if analytics_result and analytics_result.get('has_data'):
        stats = {
            'subscribers': analytics_result.get('total_subscribers', 0),
            'views': analytics_result.get('views', 0),
            'watch_time': analytics_result.get('watch_time', 0),
            'subscriber_change': analytics_result.get('net_subscriber_change', 0),
            'view_change': analytics_result.get('views_change', 0),
            'watch_time_change': analytics_result.get('watch_time_change', 0),
            'dates': analytics_result.get('dates', []),
            'views_data': analytics_result.get('views_data', []),
            'watch_time_data': analytics_result.get('watch_time_data', []),
            'subscribers_data': analytics_result.get('daily_net_subscribers_data', []),
            'likes': analytics_result.get('likes', 0),
            'like_change': analytics_result.get('like_change', 0)
        }
    
    return stats

def get_recent_videos(user, limit=10):
    """Get recent videos from a user's YouTube channel"""
    from .youtube_api import get_recent_video_stats
    
    try:
        # Check if user has YouTube connected
        profile = user.userprofile
        if not profile.youtube_connected or not profile.youtube_channel:
            return []
        
        # Get videos using the optimized API function
        result = get_recent_video_stats(profile.youtube_channel, max_results=limit)
        
        if not result.get('success', False):
            return []
        
        videos = result.get('videos', [])
        
        # Process videos into a consistent format
        formatted_videos = []
        for video in videos:
            formatted_videos.append({
                'id': video['id'],
                'title': video['title'],
                'thumbnail': video['thumbnail'],
                'published_at': video['published_at'],
                'views': video['view_count'],
                'likes': video['like_count'],
                'comments': video['comment_count'],
                'url': video['url'],
                'duration': video['duration']
            })
        
        return formatted_videos
    except Exception as e:
        logger.error(f"Error fetching recent videos for user {user.username}: {str(e)}")
        return []

def parse_duration(duration_str):
    """
    Parse ISO 8601 duration format to a human-readable string
    Example: PT1H30M15S -> 1:30:15
    """
    try:
        # Remove PT prefix
        duration = duration_str[2:]
        
        hours = 0
        minutes = 0
        seconds = 0
        
        # Extract hours, minutes, seconds
        if 'H' in duration:
            hours_part = duration.split('H')[0]
            hours = int(hours_part)
            duration = duration.split('H')[1]
            
        if 'M' in duration:
            minutes_part = duration.split('M')[0]
            minutes = int(minutes_part)
            duration = duration.split('M')[1]
            
        if 'S' in duration:
            seconds_part = duration.split('S')[0]
            seconds = int(seconds_part)
        
        # Format the duration string
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
            
    except Exception:
        return "0:00"  # Default duration if parsing fails

def about_us(request):
    """Simple view to render the about us page"""
    return render(request, 'about_us.html')

# Create your views here.
import google.auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

@login_required
@require_GET
def analytics_api(request):
    """API endpoint for getting aggregated analytics data for the current user's YouTube channel."""
    print("\n\n==== ANALYTICS API CALLED ====\n\n")
    
    # Get the time range from the request, defaulting to 30 days
    time_range = request.GET.get('range', '30')
    
    # Log request information for debugging
    logger.info(f"Analytics API called by user {request.user.username}")
    logger.info(f"Request GET params: {dict(request.GET)}")
    
    # Convert time_range to appropriate format
    if time_range == 'all':
        # Use TimeRange enum for all time
        from .youtube_analytics import TimeRange
        analytics_result = get_youtube_analytics(request.user, time_range=TimeRange.ALL_TIME)
    else:
        try:
            # Try to convert to integer days
            days = int(time_range)
            analytics_result = get_youtube_analytics(request.user, days=days)
        except ValueError:
            # Default to 30 days if invalid input
            logger.warning(f"Invalid time range value: {time_range}, defaulting to 30 days")
            analytics_result = get_youtube_analytics(request.user, days=30)
    
    # Log the result for debugging
    success = analytics_result.get('success', False)
    logger.info(f"Analytics API success: {success}")
    
    if not success:
        error_message = analytics_result.get('message', 'Unknown error')
        logger.warning(f"Analytics API error: {error_message}")
    
    # Return the analytics result directly - it's already formatted correctly
    return JsonResponse(analytics_result)

