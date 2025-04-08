from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout, login, authenticate
from social_django.utils import load_strategy
from social_django.models import UserSocialAuth
from .youtube_analytics import get_youtube_analytics
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
                
                from googleapiclient.discovery import build
                from google.oauth2.credentials import Credentials
                
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

def dashboard(request):
    """
    Main dashboard view that renders statistics and recent videos
    """
    # Get profile data
    profile = request.user.userprofile
    
    # Initialize context with basic user data
    context = {
        'user': request.user,
        'profile': profile,
        'has_analytics_access': profile.has_analytics_access,
        'youtube_connected': profile.youtube_connected
    }
    
    # If not connected to YouTube, show dashboard with connect prompt
    if not profile.youtube_connected:
        logger.warning(f"User {request.user.username} accessed dashboard without connecting YouTube channel")
        messages.info(request, "Connect your YouTube channel to see your analytics and statistics.")
        return render(request, 'auth/dashboard.html', context)
    
    try:
        # Get channel statistics data
        stats = get_channel_analytics(request.user)
        
        # Get recent videos
        videos = get_recent_videos(request.user, limit=10)
        
        # Get mutually connected users using Link model
        from .models import Link
        linked_users = Link.get_linked_users(request.user)
        linked_profiles = UserProfile.objects.filter(user__in=linked_users)
        
        # Update context with additional data
        context.update({
            'stats': stats,
            'videos': videos,
            'linked_users': linked_profiles[:4],  # Show only first 4 linked users
        })
        
        return render(request, 'auth/dashboard.html', context)
        
    except Exception as e:
        logger.error(f"Error in dashboard view for user {request.user.username}: {str(e)}")
        messages.error(request, "An error occurred loading your dashboard. Please try again later.")
        return render(request, 'auth/dashboard.html', context)

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
        return render(request, 'home.html')

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
    
    # Default context with empty data
    context = {
        'dates': json.dumps([]),
        'views': json.dumps([]),
        'watch_time': json.dumps([]),
        'subscribers': json.dumps([]),
        'has_channel': False,
        'has_data': False,
        'message': None,
        'channel_name': None,
        'raw_data': analytics_result,  # For debugging
        'debug': True,  # Enable debug section
        'user_profile': request.user.userprofile,  # Add user profile to context
        'is_mock_data': analytics_result.get('is_mock_data', False),  # Flag for mock data
        'has_analytics_permission': has_analytics_permission,  # Add analytics permission status
        'needs_analytics_permission': needs_analytics_permission  # Flag to show analytics permission prompt
    }
    
    # Update context based on the analytics result
    if analytics_result:
        context['has_channel'] = analytics_result.get('has_channel', False)
        context['has_data'] = analytics_result.get('has_data', False)
        context['message'] = analytics_result.get('message')
        context['channel_name'] = analytics_result.get('channel_name')
        
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
            
            context.update({
                'dates': json.dumps(dates),
                'views': json.dumps(views),
                'watch_time': json.dumps(watch_time),
                'subscribers': json.dumps(subscribers),
                'total_views': total_views,
                'watch_time_hours': watch_time_hours,
                'total_subscribers': total_subscribers,
            })
    
    return render(request, 'auth/dashboard.html', context)

@login_required
def statistics_view(request):
    """View for the statistics page with just the graphs."""
    
    # Default empty context 
    context = {
        'channel_name': None,
        'has_analytics_permission': True,  # Always set to true for testing
        'needs_analytics_permission': False  # Don't show the permission prompt
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
                    })
                    
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
                })
                
                # Debug logging
                logger.debug(f"Submit button in HTML: {'submit-button' in html}")
                
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
    
    # Check if viewing own profile
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
    
    if show_private_stats:
        try:
            # For mutual links, get all-time analytics instead of just 30 days
            if not is_own_profile and link_status == 'mutual':
                analytics_result = get_youtube_analytics(user, days=9999)  # 9999 means all-time data
            else:
                analytics_result = get_youtube_analytics(user)
            
            # Calculate summary metrics if data is available
            if analytics_result and analytics_result.get('has_data') and analytics_result.get('basic_metrics'):
                basic_metrics = analytics_result.get('basic_metrics', [])
                
                # Calculate totals
                total_views = sum(row[1] for row in basic_metrics) if basic_metrics else 0
                total_watch_time = sum(row[2] for row in basic_metrics) if basic_metrics else 0
                watch_time_hours = round(total_watch_time / 60, 1)  # Convert minutes to hours
                
                # Sum subscribers gained (may be at index 4)
                total_subscribers = 0
                if basic_metrics and len(basic_metrics[0]) > 4:
                    total_subscribers = sum(row[4] for row in basic_metrics)
                
                # Add summary metrics to the result
                analytics_result['total_views'] = total_views
                analytics_result['watch_time_hours'] = watch_time_hours
                analytics_result['subscribers'] = total_subscribers
                
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
        'is_mock_data': analytics_result.get('is_mock_data', False) if analytics_result else False,
        'show_private_stats': show_private_stats,
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
                logger.info(f"User {request.user.username} has analytics permissions")
            else:
                logger.warning(f"User {request.user.username} lacks analytics permissions")
        except Exception as e:
            logger.error(f"Error checking analytics permissions: {str(e)}")
        
        profile.save()
        messages.success(request, 'YouTube account connected successfully!')
        return redirect('dashboard')
    
    # Build the OAuth URL with explicit scopes
    auth_url = '/auth/login/google-oauth2/'
    
    # Include all needed scopes
    scopes = [
        'https://www.googleapis.com/auth/youtube.readonly',
        'https://www.googleapis.com/auth/youtube',
        'https://www.googleapis.com/auth/yt-analytics.readonly'
    ]
    
    # URL encode the scopes
    import urllib.parse
    encoded_scopes = urllib.parse.quote(' '.join(scopes))
    
    # Build the complete auth URL
    auth_url += f'?scope={encoded_scopes}&next=/dashboard/'
    
    # Log that we're redirecting to OAuth
    logger.info(f"Redirecting user {request.user.username} to YouTube OAuth2 login with scopes: {scopes}")
    
    # Return redirect to the auth URL
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
    """View for Terms of Service page"""
    return render(request, 'terms_of_service.html')

def privacy_policy(request):
    """View for Privacy Policy page"""
    return render(request, 'privacy_policy.html')

@login_required
def connect_accounts_view(request):
    """View for the Connect Accounts page"""
    return render(request, 'auth/connect_accounts.html')

@login_required
@require_POST
def disconnect_platform(request, platform):
    """
    API endpoint to disconnect a platform (YouTube, TikTok, etc.)
    """
    try:
        profile = request.user.userprofile
        
        if platform == 'youtube':
            # Get the user's social auth for Google
            try:
                social_auth = UserSocialAuth.objects.get(user=request.user, provider='google-oauth2')
                
                # Load strategy and disconnect
                strategy = load_strategy()
                social_auth.disconnect(strategy)
                
                # Update user profile
                profile.youtube_connected = False
                profile.youtube_channel = None
                profile.has_analytics_access = False
                profile.save()
                
                logger.info(f"User {request.user.username} disconnected YouTube account")
                return JsonResponse({
                    'success': True,
                    'message': 'YouTube account disconnected successfully'
                })
            except UserSocialAuth.DoesNotExist:
                # Just update the profile if the social auth is already gone
                profile.youtube_connected = False
                profile.youtube_channel = None
                profile.has_analytics_access = False
                profile.save()
                
                logger.info(f"User {request.user.username} disconnected YouTube account (no social auth found)")
                return JsonResponse({
                    'success': True,
                    'message': 'YouTube account disconnected successfully'
                })
        elif platform in ['tiktok', 'instagram']:
            return JsonResponse({
                'success': False,
                'error': f'{platform.title()} integration is not yet available'
            })
        else:
            return JsonResponse({
                'success': False,
                'error': f'Unknown platform: {platform}'
            })
    except Exception as e:
        logger.error(f"Error disconnecting {platform} for user {request.user.username}: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

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
            # Generate some sample historical data for demonstration
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
@require_GET
def statistics_api(request):
    """API endpoint to get YouTube analytics data for different time ranges"""
    days = request.GET.get('days', '30')
    
    # Handle 'all' as a special case
    if days == 'all':
        # Use a very large number for all time data
        days = 9999
    else:
        try:
            days = int(days)
        except ValueError:
            days = 30  # Default to 30 days if invalid input
    
    try:
        # Get profile data
        profile = request.user.userprofile
        
        # If not connected to YouTube, return empty stats but with success flag
        if not profile.youtube_connected:
            return JsonResponse({
                'success': True,
                'subscribers': 0,
                'subscriber_change': 0,
                'views': 0,
                'view_change': 0,
                'watch_time': 0,
                'watch_time_change': 0,
                'likes': 0,
                'like_change': 0
            })
        
        # Get analytics data for the specified time range
        # Always use mock data on error to ensure we show something
        analytics_result = get_youtube_analytics(request.user, use_mock_data_on_error=True, days=days)
        
        if analytics_result and analytics_result.get('has_data'):
            basic_metrics = analytics_result.get('basic_metrics', [])
            
            # Calculate totals
            total_views = sum(row[1] for row in basic_metrics)
            total_watch_time = sum(row[2] for row in basic_metrics)
            total_subscribers = sum(row[4] for row in basic_metrics) if len(basic_metrics[0]) > 4 else 0
            
            # Convert watch time to hours
            watch_time_hours = round(total_watch_time / 60, 1)
            
            # Calculate likes (placeholder using 5% of views)
            total_likes = int(total_views * 0.05)
            
            # Calculate changes by comparing recent data to previous period
            if len(basic_metrics) > 1:
                half_point = len(basic_metrics) // 2
                recent_period = basic_metrics[half_point:]
                older_period = basic_metrics[:half_point]
                
                view_change = sum(row[1] for row in recent_period) - sum(row[1] for row in older_period)
                watch_time_change = round((sum(row[2] for row in recent_period) - sum(row[2] for row in older_period)) / 60, 1)
                sub_change = sum(row[4] for row in recent_period) - sum(row[4] for row in older_period) if len(basic_metrics[0]) > 4 else 0
                like_change = int(view_change * 0.05)
            else:
                view_change = 0
                watch_time_change = 0
                sub_change = 0
                like_change = 0
            
            # Also include the raw data for charts
            dates = [row[0] for row in basic_metrics]
            views_data = [row[1] for row in basic_metrics]
            watch_time_data = [row[2] for row in basic_metrics]
            subscribers_data = [row[4] for row in basic_metrics] if len(basic_metrics[0]) > 4 else [0] * len(basic_metrics)
            
            return JsonResponse({
                'success': True,
                'subscribers': total_subscribers,
                'subscriber_change': sub_change,
                'views': total_views,
                'view_change': view_change,
                'watch_time': watch_time_hours,
                'watch_time_change': watch_time_change,
                'likes': total_likes,
                'like_change': like_change,
                'dates': dates,
                'views_data': views_data,
                'watch_time_data': watch_time_data,
                'subscribers_data': subscribers_data,
                'is_mock_data': analytics_result.get('is_mock_data', False)
            })
        else:
            # Return empty stats if no data available but with success flag
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
            
    except Exception as e:
        logger.error(f"Error in statistics_api: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)

@login_required
def permissions_info(request):
    """View for the permissions info page explaining why we need analytics access"""
    return render(request, 'auth/permissions_info.html')

@login_required
def grant_analytics_access(request):
    """Handle YouTube Analytics OAuth permission upgrade"""
    # Build the OAuth URL manually with specific scopes
    auth_url = '/auth/login/google-oauth2/'
    
    # Specify the exact scopes needed for analytics
    scopes = [
        'https://www.googleapis.com/auth/yt-analytics.readonly',
        'https://www.googleapis.com/auth/yt-analytics-monetary.readonly',
        'https://www.googleapis.com/auth/youtube.readonly',
        'https://www.googleapis.com/auth/youtube'
    ]
    
    # URL encode the scopes
    import urllib.parse
    encoded_scopes = urllib.parse.quote(' '.join(scopes))
    
    # Add specific scopes and force consent to ensure we get the new permission
    auth_url += f'?scope={encoded_scopes}&prompt=consent&next=/dashboard/'
    
    # Log that we're redirecting to OAuth for analytics
    logger.info(f"Redirecting user {request.user.username} to YouTube Analytics OAuth2 upgrade with scopes: {scopes}")
    
    # Return redirect to the auth URL
    return redirect(auth_url)

@login_required
def request_analytics_access(request):
    """
    View to redirect users to request analytics access
    """
    try:
        # Get the configuration for analytics permissions OAuth
        analytics_scopes = settings.YOUTUBE_ANALYTICS_SCOPES
        strategy = load_strategy()
        
        # Get Google social auth for this user
        social_auth = UserSocialAuth.objects.get(user=request.user, provider='google-oauth2')
        
        # Generate the authorization URL with analytics scope
        redirect_uri = f"{settings.SOCIAL_AUTH_URL_PREFIX}/complete/google-oauth2/"
        auth_url = social_auth.get_auth_url(strategy, redirect_uri, extra_scope=analytics_scopes)
        
        logger.info(f"User {request.user.username} requesting analytics access")
        return redirect(auth_url)
        
    except Exception as e:
        logger.error(f"Error requesting analytics access for user {request.user.username}: {str(e)}")
        messages.error(request, "An error occurred while requesting analytics access. Please try again later.")
        return redirect('dashboard')

@login_required
def analytics_info(request):
    """
    Displays information about analytics permissions
    """
    return render(request, 'auth/analytics_info.html', {
        'user': request.user,
        'profile': request.user.userprofile
    })

@login_required
def video_statistics(request, video_id):
    """
    Display detailed statistics for a specific video
    """
    try:
        # Get user's profile
        profile = request.user.userprofile
        
        # Check if connected to YouTube
        if not profile.youtube_connected:
            logger.warning(f"User {request.user.username} attempted to access video stats without connecting YouTube")
            return redirect('connect_account')
            
        # Check analytics permission status
        has_analytics_access = profile.has_analytics_access
        
        # Get basic video details and metrics
        video_details = get_video_details(request.user, video_id)
        
        if not video_details:
            messages.error(request, "Could not find that video. Please try again.")
            return redirect('dashboard')
            
        # Get retention data if user has analytics access
        retention_data = None
        if has_analytics_access:
            retention_data = get_retention_data(request.user, video_id)
            
        # Prepare context
        context = {
            'user': request.user,
            'profile': profile,
            'video': video_details,
            'retention_data': retention_data,
            'has_analytics_access': has_analytics_access,
        }
        
        return render(request, 'auth/video_statistics.html', context)
        
    except Exception as e:
        logger.error(f"Error in video statistics view for user {request.user.username}, video {video_id}: {str(e)}")
        messages.error(request, "An error occurred loading video statistics. Please try again later.")
        return redirect('dashboard')

def get_channel_analytics(user):
    """
    Get channel analytics for the dashboard display
    
    Parameters:
    - user: The Django user object
    
    Returns:
    - Dictionary containing formatted stats for dashboard display
    """
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
        basic_metrics = analytics_result.get('basic_metrics', [])
        
        if basic_metrics:
            # Calculate totals
            views = sum(row[1] for row in basic_metrics)
            watch_time = sum(row[2] for row in basic_metrics) if len(basic_metrics[0]) > 2 else 0
            subscribers = sum(row[4] for row in basic_metrics) if len(basic_metrics[0]) > 4 else 0
            
            # Convert watch time to hours
            watch_time_hours = round(watch_time / 60, 1) if watch_time else 0
            
            # For like count, we don't have it directly in analytics, so use a derived metric
            # In a real app, you'd get this from the videos API
            likes = int(views * 0.05)  # Placeholder assumption: 5% of views result in likes
            
            # Calculate changes by comparing recent data (last 7 days) to older data
            if len(basic_metrics) > 7:
                recent_period = basic_metrics[-7:]
                older_period = basic_metrics[-14:-7]
                
                recent_views = sum(row[1] for row in recent_period)
                older_views = sum(row[1] for row in older_period)
                view_change = recent_views - older_views
                
                recent_watch_time = sum(row[2] for row in recent_period) if len(basic_metrics[0]) > 2 else 0
                older_watch_time = sum(row[2] for row in older_period) if len(basic_metrics[0]) > 2 else 0
                watch_time_change = round((recent_watch_time - older_watch_time) / 60, 1)
                
                recent_subs = sum(row[4] for row in recent_period) if len(basic_metrics[0]) > 4 else 0
                older_subs = sum(row[4] for row in older_period) if len(basic_metrics[0]) > 4 else 0
                sub_change = recent_subs - older_subs
                
                # Calculate like change (using our derived metric)
                like_change = int(view_change * 0.05)
            else:
                view_change = 0
                watch_time_change = 0
                sub_change = 0
                like_change = 0
            
            # Update stats dictionary
            stats.update({
                'subscribers': subscribers,
                'views': views,
                'watch_time': watch_time_hours,
                'likes': likes,
                'subscriber_change': sub_change,
                'view_change': view_change,
                'watch_time_change': watch_time_change,
                'like_change': like_change
            })
    
    return stats

def get_recent_videos(user, limit=10):
    """
    Get recent videos for a user's channel
    
    Parameters:
    - user: The Django user object
    - limit: Maximum number of videos to return
    
    Returns:
    - List of video objects with details
    """
    try:
        profile = user.userprofile
        
        # If user has no YouTube channel, return empty list
        if not profile.youtube_connected or not profile.youtube_channel:
            return []
            
        # Get credentials for API access
        social = user.social_auth.get(provider='google-oauth2')
        access_token = social.get_access_token(load_strategy())
        refresh_token = social.extra_data.get('refresh_token')
        client_id = social.extra_data.get('client_id')
        client_secret = social.extra_data.get('client_secret')
        
        # Create credentials object
        credentials = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri='https://oauth2.googleapis.com/token',
            client_id=client_id,
            client_secret=client_secret
        )
        
        # Build YouTube API client
        youtube = build('youtube', 'v3', credentials=credentials)
        
        # Get channel videos
        search_response = youtube.search().list(
            part='snippet',
            channelId=profile.youtube_channel,
            maxResults=limit,
            order='date',
            type='video'
        ).execute()
        
        if not search_response.get('items'):
            return []
            
        # Get video details including statistics
        video_ids = [item['id']['videoId'] for item in search_response['items']]
        videos_response = youtube.videos().list(
            part='snippet,statistics,contentDetails',
            id=','.join(video_ids)
        ).execute()
        
        # Process and format video data
        videos = []
        for item in videos_response.get('items', []):
            # Parse duration to a human-readable format
            duration = item['contentDetails']['duration']  # ISO 8601 format
            duration_str = parse_duration(duration)
            
            videos.append({
                'id': item['id'],
                'title': item['snippet']['title'],
                'thumbnail': item['snippet']['thumbnails']['high']['url'],
                'published_at': item['snippet']['publishedAt'],
                'duration': duration_str,
                'views': int(item['statistics'].get('viewCount', 0)),
                'likes': int(item['statistics'].get('likeCount', 0)),
                'comments': int(item['statistics'].get('commentCount', 0)),
                'url': f"https://www.youtube.com/watch?v={item['id']}"
            })
        
        return videos
        
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

# Create your views here.
import google.auth
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required

