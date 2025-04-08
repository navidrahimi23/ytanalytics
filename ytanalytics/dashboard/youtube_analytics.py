from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from social_django.utils import load_strategy
from datetime import datetime, timedelta
import json
import logging
from googleapiclient.errors import HttpError
import random

logger = logging.getLogger(__name__)

def get_youtube_analytics(user, use_mock_data_on_error=True, days=30):
    """
    Get YouTube analytics data for the specified user
    
    Parameters:
    - user: Django User object
    - use_mock_data_on_error: If True, returns mock data when an error occurs
    - days: Number of days to analyze (default: 30)
    
    Returns:
    Dictionary with analytics data or error information
    """
    try:
        logger.info(f"Getting YouTube analytics for user: {user.username}, days: {days}")
        
        # Get the user's credentials
        profile = user.userprofile
        
        # Check if user has connected YouTube account
        if not profile.youtube_connected or not profile.youtube_channel:
            logger.warning(f"User {user.username} has not connected YouTube, returning empty stats")
            return {
                'has_channel': False,
                'has_data': False,
                'message': 'You need to connect your YouTube channel first',
                'is_mock_data': True
            }
        
        logger.info(f"User's stored channel ID: {profile.youtube_channel}")
        
        try:
            # Get user credentials
            credentials = get_user_credentials(user)
            if not credentials:
                logger.warning(f"Failed to get credentials for user {user.username}, using mock data")
                return get_mock_analytics_data(user, days=days)
            
            logger.info(f"Successfully got credentials for user {user.username}, fetching analytics...")
            
            # Build YouTube Analytics API client
            analytics = build('youtubeAnalytics', 'v2', credentials=credentials)
            
            # Get the channel ID
            channel_id = profile.youtube_channel
            
            # Format dates for the API
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            logger.info(f"Querying YouTube Analytics API from {start_date} to {end_date} for channel {channel_id}")
            
            try:
                # Test if we can fetch simple data
                channel_report = analytics.reports().query(
                    ids=f'channel=={channel_id}',
                    startDate=start_date,
                    endDate=end_date,
                    metrics='views,estimatedMinutesWatched,subscribersGained',
                    dimensions='day',
                    sort='day'
                ).execute()
                
                logger.info(f"Successfully retrieved analytics data: {len(channel_report.get('rows', [])) if 'rows' in channel_report else 0} rows")
                
                # Format the data for our app
                basic_metrics = []
                dates = []
                views = []
                watch_time = []
                subscribers = []
                
                for row in channel_report.get('rows', []):
                    date_str = row[0]  # The date in YYYY-MM-DD format
                    day_views = row[1]  # Views for this day
                    day_watch_time = row[2]  # Estimated minutes watched
                    day_subscribers = row[3]  # Subscribers gained
                    
                    # Save to our lists
                    dates.append(date_str)
                    views.append(day_views)
                    watch_time.append(day_watch_time)
                    subscribers.append(day_subscribers)
                    
                    # Format for the basic_metrics data structure
                    basic_metrics.append([
                        date_str,
                        day_views,
                        day_watch_time,
                        int(day_views * 0.05),  # Estimated likes (5% of views)
                        int(day_views * 0.01),  # Estimated comments (1% of views)
                        day_subscribers
                    ])
                
                # Get channel info for the name
                youtube = build('youtube', 'v3', credentials=credentials)
                channel_response = youtube.channels().list(
                    part='snippet',
                    id=channel_id
                ).execute()
                
                channel_name = channel_id
                if channel_response.get('items'):
                    channel_name = channel_response['items'][0]['snippet']['title']
                
                # Calculate totals
                total_views = sum(views)
                total_watch_time = sum(watch_time)
                total_watch_time_hours = round(total_watch_time / 60, 1)  # Convert to hours
                total_subscribers = sum(subscribers)
                
                return {
                    'has_channel': True,
                    'has_data': True,
                    'channel_name': channel_name,
                    'channel_id': channel_id,
                    'basic_metrics': basic_metrics,
                    'is_mock_data': False,
                    'subscribers': profile.get_subscribers() or total_subscribers,
                    'views': total_views,
                    'watch_time': total_watch_time_hours,
                    'message': None
                }
                
            except HttpError as e:
                error_reason = str(e)
                logger.error(f"YouTube API error: {error_reason}")
                
                if "Service not enabled" in error_reason or "forbidden" in error_reason.lower():
                    logger.warning(f"User {user.username} lacks proper permissions for YouTube Analytics")
                    # Return mock data but flag as mock
                    mock_data = get_mock_analytics_data(user, days=days)
                    mock_data['message'] = 'Using mock data - YouTube Analytics permissions not granted'
                    return mock_data
                else:
                    # Unknown API error
                    logger.error(f"Unknown YouTube API error: {error_reason}")
                    # Fall back to mock data
                    return get_mock_analytics_data(user, days=days)
                    
        except Exception as inner_e:
            logger.error(f"Error accessing YouTube Analytics API: {str(inner_e)}")
            # Fall back to mock data
            return get_mock_analytics_data(user, days=days)
            
    except Exception as e:
        logger.error(f"Error in YouTube analytics for user {user.username}: {str(e)}")
        if use_mock_data_on_error:
            # Create a mock User-like object with a username attribute
            class MockUser:
                def __init__(self, username):
                    self.username = username
            
            mock_user = MockUser("Sample Channel")
            return get_mock_analytics_data(mock_user, days=days)
        return {
            'has_channel': False,
            'has_data': False,
            'message': f'Error retrieving YouTube analytics: {str(e)}',
            'error': str(e)
        }

def get_mock_analytics_data(user, days=30):
    """Generate mock data for testing purposes"""
    # Generate base subscriber count (smaller number for realism)
    channel_subs = random.randint(50, 500)  # Base subscribers between 50-500
    
    # Generate dates for the requested period
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # Generate daily data points
    dates = []
    current_date = start_date
    basic_metrics = []
    
    # Start with base metrics related to channel size
    daily_views_base = channel_subs * 0.2  # Average 20% of subscribers watch daily
    daily_watch_time_base = daily_views_base * 3  # Average 3 minutes per view
    daily_subs_base = channel_subs * 0.002  # 0.2% subscriber growth daily
    
    while current_date <= end_date:
        date_str = current_date.strftime('%Y-%m-%d')
        dates.append(date_str)
        
        # Add randomness to daily metrics
        random_factor = random.uniform(0.7, 1.3)
        daily_views = int(daily_views_base * random_factor)
        daily_watch_time = int(daily_watch_time_base * random_factor)
        
        # Subscriber growth with occasional spikes
        sub_random = random.uniform(0.5, 2.0)
        daily_subs = int(daily_subs_base * sub_random)
        
        # Row format: [date, views, watch_time, likes, comments, subscribers]
        basic_metrics.append([
            date_str,
            daily_views,
            daily_watch_time,
            int(daily_views * 0.05),  # 5% like rate
            int(daily_views * 0.01),  # 1% comment rate
            daily_subs
        ])
        
        # Move to next day
        current_date += timedelta(days=1)
    
    mock_result = {
        'has_channel': True,
        'has_data': True,
        'channel_name': f"{user.username}'s Channel",
        'channel_id': 'MOCK_CHANNEL_ID',
        'basic_metrics': basic_metrics,
        'is_mock_data': True,
        'subscribers': channel_subs,
        'views': channel_subs * days * 0.2,  # Estimate total views
        'message': 'Using mock data for testing'
    }
    
    return mock_result

def get_retention_data(user, video_id):
    """
    Get audience retention data for a specific video
    Returns retention data for a specific video
    """
    try:
        profile = user.userprofile
        
        # Check if user has analytics access
        if not profile.has_analytics_access:
            logger.warning(f"User {user.username} attempted to access retention data without proper permissions")
            return None
            
        credentials = get_user_credentials(user)
        if not credentials:
            logger.error(f"Could not get credentials for user {user.username}")
            return None
            
        analytics = build('youtubeAnalytics', 'v2', credentials=credentials)
        
        # Get the video's publish date
        youtube = build('youtube', 'v3', credentials=credentials)
        video_response = youtube.videos().list(
            part='snippet',
            id=video_id
        ).execute()
        
        if not video_response.get('items'):
            logger.error(f"Video {video_id} not found for user {user.username}")
            return None
            
        # Format dates for the API
        publish_date = video_response['items'][0]['snippet']['publishedAt']
        publish_date = datetime.strptime(publish_date, '%Y-%m-%dT%H:%M:%SZ')
        start_date = publish_date.strftime('%Y-%m-%d')
        end_date = datetime.now().strftime('%Y-%m-%d')
        
        # Get audience retention data
        retention_response = analytics.reports().query(
            dimensions='elapsedVideoTimeRatio',
            metrics='relativeRetentionPerformance',
            filters=f'video=={video_id}',
            startDate=start_date,
            endDate=end_date,
            sort='elapsedVideoTimeRatio'
        ).execute()

        if not retention_response.get('rows'):
            logger.warning(f"No retention data available for video {video_id}")
            return None
            
        # Format data for frontend visualization
        retention_data = {
            'labels': [],
            'data': []
        }
        
        for row in retention_response['rows']:
            time_ratio = float(row[0]) * 100  # Convert to percentage
            retention = float(row[1])
            
            retention_data['labels'].append(f"{time_ratio:.0f}%")
            retention_data['data'].append(retention)
            
        return retention_data
        
    except Exception as e:
        logger.error(f"Error getting retention data for video {video_id}: {str(e)}")
        return None

def get_user_credentials(user):
    """
    Helper function to get user credentials from social auth
    
    Parameters:
    - user: The Django user object
    
    Returns:
    - Credentials object or None if error occurs
    """
    try:
        # Get the user's social auth data
        social = user.social_auth.get(provider='google-oauth2')
        
        # Get tokens
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
        
        return credentials
    except Exception as e:
        logger.error(f"Error getting user credentials: {str(e)}")
        return None
