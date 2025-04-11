from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from social_django.utils import load_strategy
from datetime import datetime, timedelta
import json
import logging
from googleapiclient.errors import HttpError
import random
import time
from django.core.cache import cache
from functools import wraps

logger = logging.getLogger(__name__)

# Constants for API rate limiting and caching
CACHE_TIMEOUT = 900  # 15 minutes
MAX_CALLS_PER_USER_PER_DAY = 100
ANALYTICS_FETCH_THRESHOLD = 5  # seconds

def performance_monitor(func):
    """Decorator to monitor function performance"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        user = args[0] if args else kwargs.get('user')
        username = user.username if hasattr(user, 'username') else 'unknown'
        
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            
            # Log performance metrics
            logger.info(f"{func.__name__} completed in {duration:.2f}s for user {username}")
            
            # Alert on slow operations
            if duration > ANALYTICS_FETCH_THRESHOLD:
                logger.warning(f"Slow operation: {func.__name__} took {duration:.2f}s for user {username}")
                
            return result
        except Exception as e:
            logger.error(f"{func.__name__} failed in {time.time() - start_time:.2f}s: {str(e)}")
            raise
    return wrapper

def cache_result(timeout=CACHE_TIMEOUT):
    """Decorator to cache function results"""
    def decorator(func):
        @wraps(func)
        def wrapper(user, *args, **kwargs):
            # Create a unique cache key based on function name, user ID, and arguments
            days = kwargs.get('days', 30)  # Default to 30 if not provided
            cache_key = f"{func.__name__}_{user.id}_{days}"
            
            # Try to get from cache first
            cached_result = cache.get(cache_key)
            if cached_result:
                logger.info(f"Cache hit for {cache_key}")
                return cached_result
            
            # Check rate limiting
            rate_limit_key = f"api_calls_{user.id}_{datetime.now().strftime('%Y-%m-%d')}"
            call_count = cache.get(rate_limit_key, 0)
            
            if call_count >= MAX_CALLS_PER_USER_PER_DAY:
                logger.warning(f"Rate limit exceeded for user {user.id}")
                return {
                    'has_channel': True,
                    'has_data': False,
                    'message': 'API rate limit exceeded. Please try again tomorrow.'
                }
                
            # Increment the call count
            cache.set(rate_limit_key, call_count + 1, timeout=86400)  # 24 hours
            
            # Execute the function
            result = func(user, *args, **kwargs)
            
            # Cache the result
            if result.get('has_data', False):
                cache.set(cache_key, result, timeout=timeout)
                
            return result
        return wrapper
    return decorator

@performance_monitor
@cache_result()
def get_youtube_analytics(user, days=30):
    """
    Get YouTube analytics data for the specified user
    
    Parameters:
    - user: Django User object
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
                'message': 'You need to connect your YouTube channel first'
            }
        
        logger.info(f"User's stored channel ID: {profile.youtube_channel}")
        
        try:
            # Get user credentials
            credentials = get_user_credentials(user)
            if not credentials:
                logger.warning(f"Failed to get credentials for user {user.username}")
                return {
                    'has_channel': True,
                    'has_data': False,
                    'message': 'Unable to authenticate with YouTube. Please reconnect your account.'
                }
            
            logger.info(f"Successfully got credentials for user {user.username}, fetching analytics...")
            
            # Initialize API clients
            youtube_api_clients = initialize_youtube_clients(credentials)
            if not youtube_api_clients:
                return {
                    'has_channel': True,
                    'has_data': False,
                    'message': 'Failed to initialize YouTube API clients.'
                }
                
            analytics = youtube_api_clients['analytics']
            youtube = youtube_api_clients['data']
            
            # Get the channel ID
            channel_id = profile.youtube_channel
            
            # Format dates for the API
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
            
            logger.info(f"Querying YouTube Analytics API from {start_date} to {end_date} for channel {channel_id}")
            
            try:
                # Optimize: Get both statistics and snippet in one API call
                channel_response = youtube.channels().list(
                    part='statistics,snippet',
                    id=channel_id
                ).execute()
                
                if not channel_response.get('items'):
                    logger.warning(f"No channel data found for channel ID: {channel_id}")
                    return {
                        'has_channel': True,
                        'has_data': False,
                        'message': 'No channel data found. Please verify your YouTube channel.'
                    }
                
                channel_data = channel_response['items'][0]
                
                # Extract channel statistics
                current_total_subscribers = int(channel_data['statistics'].get('subscriberCount', 0))
                logger.info(f"Current total subscriber count: {current_total_subscribers}")
                
                # Get channel name
                channel_name = channel_data['snippet']['title'] if 'snippet' in channel_data else channel_id
                
                # Get analytics data with retry for transient failures
                analytics_data = get_analytics_with_retry(analytics, channel_id, start_date, end_date)
                if not analytics_data:
                    return {
                        'has_channel': True,
                        'has_data': False,
                        'message': 'Unable to retrieve analytics data. Please try again later.'
                    }
                
                subscriber_summary = analytics_data['subscriber_summary']
                channel_report = analytics_data['channel_report']
                
                # Format the data for our app
                basic_metrics = []
                dates = []
                views = []
                watch_time = []
                daily_subscribers_gained = []
                daily_subscribers_lost = []
                daily_net_subscribers = []
                
                for row in channel_report.get('rows', []):
                    date_str = row[0]  # The date in YYYY-MM-DD format
                    day_views = row[1]  # Views for this day
                    day_watch_time = row[2]  # Estimated minutes watched
                    day_subs_gained = row[3]  # Subscribers gained
                    day_subs_lost = row[4]  # Subscribers lost
                    day_net_subs = day_subs_gained - day_subs_lost  # Net subscriber change
                    
                    # Save to our lists
                    dates.append(date_str)
                    views.append(day_views)
                    watch_time.append(day_watch_time)
                    daily_subscribers_gained.append(day_subs_gained)
                    daily_subscribers_lost.append(day_subs_lost)
                    daily_net_subscribers.append(day_net_subs)
                    
                    # Format for the basic_metrics data structure
                    basic_metrics.append([
                        date_str,
                        day_views,
                        day_watch_time,
                        day_net_subs
                    ])
                
                # Calculate totals
                total_views = sum(views)
                total_watch_time = sum(watch_time)
                total_watch_time_hours = round(total_watch_time / 60, 1)  # Convert to hours
                
                # Get subscriber changes from the summary
                subs_gained_in_period = subscriber_summary.get('gained', 0)
                subs_lost_in_period = subscriber_summary.get('lost', 0)
                
                # Calculate net subscriber change for the requested period
                net_subscriber_change = subs_gained_in_period - subs_lost_in_period
                
                return {
                    'has_channel': True,
                    'has_data': True,
                    'channel_name': channel_name,
                    'channel_id': channel_id,
                    'basic_metrics': basic_metrics,
                    'total_subscribers': current_total_subscribers,  # Current total subscriber count
                    'subscribers_gained': subs_gained_in_period,     # Gained in this period
                    'subscribers_lost': subs_lost_in_period,         # Lost in this period
                    'net_subscriber_change': net_subscriber_change,  # Net change in this period
                    'views': total_views,
                    'watch_time': total_watch_time_hours,
                    'message': None
                }
                
            except HttpError as e:
                error_reason = str(e)
                logger.error(f"YouTube API error: {error_reason}")
                
                if "Service not enabled" in error_reason or "forbidden" in error_reason.lower():
                    logger.warning(f"User {user.username} lacks proper permissions for YouTube Analytics")
                    return {
                        'has_channel': True,
                        'has_data': False,
                        'message': 'YouTube Analytics permissions not granted. Please grant analytics access.'
                    }
                else:
                    # Unknown API error
                    logger.error(f"Unknown YouTube API error: {error_reason}")
                    return {
                        'has_channel': True,
                        'has_data': False,
                        'message': f'YouTube API error: {error_reason}'
                    }
                    
        except Exception as inner_e:
            logger.error(f"Error accessing YouTube Analytics API: {str(inner_e)}")
            return {
                'has_channel': True,
                'has_data': False,
                'message': f'Error accessing YouTube Analytics: {str(inner_e)}'
            }
            
    except Exception as e:
        logger.error(f"Error in YouTube analytics for user {user.username}: {str(e)}")
        return {
            'has_channel': False,
            'has_data': False,
            'message': f'Error retrieving YouTube analytics: {str(e)}',
            'error': str(e)
        }

def initialize_youtube_clients(credentials):
    """
    Initialize YouTube API clients with retries
    
    Parameters:
    - credentials: User OAuth2 credentials
    
    Returns:
    - Dict containing initialized API clients or None if error
    """
    try:
        # Create the API clients
        analytics = build('youtubeAnalytics', 'v2', credentials=credentials)
        youtube = build('youtube', 'v3', credentials=credentials)
        
        return {
            'analytics': analytics,
            'data': youtube
        }
    except Exception as e:
        logger.error(f"Error initializing YouTube API clients: {str(e)}")
        return None

def get_analytics_with_retry(analytics, channel_id, start_date, end_date, max_retries=3):
    """
    Get analytics data with retry logic for transient failures
    
    Parameters:
    - analytics: YouTube Analytics API client
    - channel_id: YouTube channel ID
    - start_date: Start date for analytics
    - end_date: End date for analytics
    - max_retries: Maximum number of retry attempts
    
    Returns:
    - Dict containing analytics data or None if all retries fail
    """
    retry_count = 0
    backoff = 2  # Initial backoff in seconds
    
    while retry_count < max_retries:
        try:
            # Get subscriber changes in the specified time period
            subscriber_summary = analytics.reports().query(
                ids=f'channel=={channel_id}',
                startDate=start_date,
                endDate=end_date,
                metrics='subscribersGained,subscribersLost',
            ).execute()
            
            # Get daily analytics data 
            channel_report = analytics.reports().query(
                ids=f'channel=={channel_id}',
            startDate=start_date,
            endDate=end_date,
                metrics='views,estimatedMinutesWatched,subscribersGained,subscribersLost',
                dimensions='day',
                sort='day'
            ).execute()
            
            logger.debug(f"Raw channel report data: {channel_report}")
            logger.info(f"Successfully retrieved analytics data: {len(channel_report.get('rows', [])) if 'rows' in channel_report else 0} rows")
            
            # Extract subscriber summary data
            subs_gained_in_period = 0
            subs_lost_in_period = 0
            
            if subscriber_summary.get('rows'):
                subs_gained_in_period = subscriber_summary['rows'][0][0]
                subs_lost_in_period = subscriber_summary['rows'][0][1]
                logger.info(f"In the requested period: Subscribers gained: {subs_gained_in_period}, lost: {subs_lost_in_period}")
            
            return {
                'subscriber_summary': {
                    'gained': subs_gained_in_period,
                    'lost': subs_lost_in_period
                },
                'channel_report': channel_report
            }
            
        except HttpError as e:
            # Only retry on 5xx errors or quota errors
            status_code = e.resp.status
            if status_code >= 500 or status_code == 403 and "quota" in str(e).lower():
                retry_count += 1
                if retry_count < max_retries:
                    sleep_time = backoff * (2 ** (retry_count - 1))  # Exponential backoff
                    logger.warning(f"Retrying API call after {sleep_time}s. Attempt {retry_count}/{max_retries}")
                    time.sleep(sleep_time)
                    continue
            
            # Don't retry on other errors
            logger.error(f"YouTube API error (non-retryable): {str(e)}")
            return None
            
        except Exception as e:
            logger.error(f"Unexpected error in analytics request: {str(e)}")
            return None
    
    # All retries failed
    logger.error(f"All retries failed for channel {channel_id}")
    return None

@performance_monitor
def get_retention_data(user, video_id):
    """
    Get audience retention data for a specific video
    Returns retention data for a specific video
    """
    # Create a unique cache key for retention data
    cache_key = f"retention_data_{user.id}_{video_id}"
    
    # Try to get from cache first
    cached_data = cache.get(cache_key)
    if cached_data:
        logger.info(f"Using cached retention data for video {video_id}")
        return cached_data
    
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
            
        # Initialize API clients
        analytics = build('youtubeAnalytics', 'v2', credentials=credentials)
        youtube = build('youtube', 'v3', credentials=credentials)
        
        # Get the video's publish date
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
        
        # Get audience retention data with retry
        retention_data = get_retention_with_retry(analytics, video_id, start_date, end_date)
        if retention_data:
            # Cache the result
            cache.set(cache_key, retention_data, timeout=CACHE_TIMEOUT)
            
        return retention_data
        
    except Exception as e:
        logger.error(f"Error getting retention data for video {video_id}: {str(e)}")
        return None

def get_retention_with_retry(analytics, video_id, start_date, end_date, max_retries=3):
    """Get retention data with retry logic"""
    retry_count = 0
    backoff = 2  # Initial backoff in seconds
    
    while retry_count < max_retries:
        try:
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
            
        except HttpError as e:
            # Only retry on 5xx errors or quota errors
            status_code = e.resp.status
            if status_code >= 500 or status_code == 403 and "quota" in str(e).lower():
                retry_count += 1
                if retry_count < max_retries:
                    sleep_time = backoff * (2 ** (retry_count - 1))  # Exponential backoff
                    logger.warning(f"Retrying API call after {sleep_time}s. Attempt {retry_count}/{max_retries}")
                    time.sleep(sleep_time)
                    continue
            
            # Don't retry on other errors
            logger.error(f"YouTube API error (non-retryable): {str(e)}")
            return None
            
        except Exception as e:
            logger.error(f"Unexpected error in retention request: {str(e)}")
            return None
    
    # All retries failed
    logger.error(f"All retention data retries failed for video {video_id}")
    return None

def get_user_credentials(user):
    """
    Helper function to get user credentials from social auth
    
    Parameters:
    - user: The Django user object
    
    Returns:
    - Credentials object or None if error occurs
    """
    # Create a unique cache key for user credentials
    cache_key = f"user_creds_{user.id}"
    
    # Try to get from cache first (short timeout for credentials)
    cached_creds = cache.get(cache_key)
    if cached_creds:
        logger.info(f"Using cached credentials for user {user.username}")
        return cached_creds
    
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
        
        # Cache credentials (short timeout)
        cache.set(cache_key, credentials, timeout=300)  # 5 minutes
        
        return credentials
    except Exception as e:
        logger.error(f"Error getting user credentials: {str(e)}")
        return None

# We'll keep get_mock_analytics_data but mark it for future removal
# It can be useful for testing but should eventually be moved to a test module
def get_mock_analytics_data(user, days=30):
    """Generate mock data for testing purposes"""
    logger.warning("Using mock data - consider removing this function in production")
    # More realistic values - approximately matching the user's actual channel
    channel_subs = 66  # Set this to the actual subscriber count
    total_channel_views = 5000  # Set this to the approximate total view count
    
    # Generate dates for the requested period
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    # Generate daily data points
    dates = []
    current_date = start_date
    basic_metrics = []
    
    # Calculate realistic daily values based on actual channel size
    avg_daily_views = total_channel_views / 365  # Avg daily views based on total views spread over a year
    daily_views_base = max(1, avg_daily_views / 10)  # Conservative daily views estimate
    daily_watch_time_base = daily_views_base * 3  # Average 3 minutes per view
    daily_subs_gained_base = channel_subs * 0.005  # 0.5% growth rate (more conservative)
    daily_subs_lost_base = channel_subs * 0.002  # 0.2% unsubscribe rate (more conservative)
    
    # Track total gains and losses for the period
    total_subs_gained = 0
    total_subs_lost = 0
    
    # Ensure we don't generate huge numbers for long time periods
    if days > 90:
        # For longer periods, reduce the base values further to avoid unrealistic growth
        scaling_factor = min(1.0, 90 / days)
        daily_views_base *= scaling_factor
        daily_subs_gained_base *= scaling_factor
    
    while current_date <= end_date:
        date_str = current_date.strftime('%Y-%m-%d')
        dates.append(date_str)
        
        # Add randomness to daily metrics but keep them realistic
        random_factor = random.uniform(0.7, 1.3)
        daily_views = max(0, int(daily_views_base * random_factor))
        daily_watch_time = max(0, int(daily_watch_time_base * random_factor))
        
        # Subscriber changes with occasional spikes
        subs_gained_random = random.uniform(0.5, 1.5)
        daily_subs_gained = max(0, int(daily_subs_gained_base * subs_gained_random))
        
        subs_lost_random = random.uniform(0.5, 1.5)
        daily_subs_lost = max(0, int(daily_subs_lost_base * subs_lost_random))
        
        # Calculate net change for this day
        daily_net_subs = daily_subs_gained - daily_subs_lost
        
        # Track totals for period
        total_subs_gained += daily_subs_gained
        total_subs_lost += daily_subs_lost
        
        # Row format: [date, views, watch_time, likes, comments, net_subs_change]
        basic_metrics.append([
            date_str,
            daily_views,
            daily_watch_time,
            int(daily_views * 0.05),  # 5% like rate
            int(daily_views * 0.01),  # 1% comment rate
            daily_net_subs
        ])
        
        # Move to next day
        current_date += timedelta(days=1)
    
    # Calculate total metrics for the period
    total_views = max(1, int(sum(row[1] for row in basic_metrics)))
    total_watch_time_hours = round(sum(row[2] for row in basic_metrics) / 60, 1)
    net_sub_change = total_subs_gained - total_subs_lost
    
    # For "all time" view (high days value), adjust total channel stats
    if days > 1000:  # If this is an "all time" request
        # Use the actual full channel stats
        total_views = total_channel_views
    
    # Ensure we don't exceed the total channel stats
    total_views = min(total_views, total_channel_views)
    
    logger.info(f"Generated mock data: subs={channel_subs}, views={total_views}, watch_time={total_watch_time_hours}h")
    
    mock_result = {
        'has_channel': True,
        'has_data': True,
        'channel_name': f"{user.username}'s Channel",
        'channel_id': 'MOCK_CHANNEL_ID',
        'basic_metrics': basic_metrics,
        'is_mock_data': True,
        'total_subscribers': channel_subs,  # Current total subscriber count
        'subscribers_gained': total_subs_gained,  # Gained in the period
        'subscribers_lost': total_subs_lost,      # Lost in the period
        'net_subscriber_change': net_sub_change,  # Net change for period
        'views': total_views,
        'watch_time': total_watch_time_hours,
        'message': 'Using mock data for testing'
    }
    
    return mock_result
