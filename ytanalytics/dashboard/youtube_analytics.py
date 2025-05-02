"""
YouTube Analytics Module - Core functionality for interacting with YouTube API
"""
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from social_django.utils import load_strategy
from googleapiclient.errors import HttpError
from datetime import datetime, timedelta
import logging
from enum import Enum

logger = logging.getLogger(__name__)

class TimeRange(Enum):
    """Time range options for analytics data"""
    DAY = 1
    WEEK = 7
    MONTH = 30
    QUARTER = 90
    HALF_YEAR = 180
    YEAR = 365
    ALL_TIME = 9999
    
    @classmethod
    def from_string(cls, value):
        """Convert string representation to TimeRange enum"""
        try:
            if value == 'all':
                return cls.ALL_TIME
            days = int(value)
            # Find closest match
            for item in cls:
                if item.value == days:
                    return item
            # If no exact match, return custom value
            return days
        except (ValueError, TypeError):
            return cls.MONTH  # Default

def get_user_credentials(user):
    """Get OAuth2 credentials for a user"""
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

def build_youtube_analytics_service(credentials):
    """Build YouTube Analytics API service with user credentials"""
    try:
        return build('youtubeAnalytics', 'v2', credentials=credentials)
    except Exception as e:
        logger.error(f"Error building YouTube Analytics service: {str(e)}")
        return None

def build_youtube_data_service(credentials):
    """Build YouTube Data API service with user credentials"""
    try:
        return build('youtube', 'v3', credentials=credentials)
    except Exception as e:
        logger.error(f"Error building YouTube Data service: {str(e)}")
        return None

def get_channel_info(youtube, channel_id=None):
    """
    Get basic channel information
    If channel_id is None, gets the authenticated user's channel
    """
    try:
        if channel_id:
            request = youtube.channels().list(
                part="snippet,statistics,contentDetails",
                id=channel_id
            )
        else:
            request = youtube.channels().list(
                part="snippet,statistics,contentDetails",
                mine=True
            )
        
        response = request.execute()
        
        if not response.get('items'):
            logger.warning("No channel found")
            return None
            
        return response['items'][0]
    except HttpError as e:
        logger.error(f"YouTube API error getting channel info: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting channel info: {str(e)}")
        return None

def get_analytics_report(analytics, channel_id, start_date, end_date):
    """Get basic analytics report for a channel"""
    try:
        # Request the core metrics we need
        metrics = "views,estimatedMinutesWatched,subscribersGained,subscribersLost,likes,comments"
        
        response = analytics.reports().query(
            ids=f"channel=={channel_id}",
            startDate=start_date,
            endDate=end_date,
            metrics=metrics,
            dimensions="day",
            sort="day"
        ).execute()
        
        return response
    except HttpError as e:
        logger.error(f"YouTube Analytics API error: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting analytics: {str(e)}")
        return None

def get_youtube_analytics(user, days=30, time_range=None):
    """
    Get YouTube analytics data for the specified user's channel
    
    Args:
        user: The Django user object
        days: Number of days to fetch data for (default 30)
        time_range: TimeRange enum value (overrides days if provided)
    
    Returns:
        Dictionary with analytics data or error information
    """
    # Use time_range enum value if provided, otherwise use days
    if time_range and isinstance(time_range, TimeRange):
        days = time_range.value
    
    logger.info(f"Getting YouTube analytics for user: {user.username}, days: {days}")
    
    try:
        # Check if user has a YouTube connection
        profile = user.userprofile
        if not profile.youtube_connected or not profile.youtube_channel:
            return {
                'success': False,
                'has_channel': False,
                'message': 'No YouTube channel connected'
            }
        
        # Get credentials for the API
        credentials = get_user_credentials(user)
        if not credentials:
            return {
                'success': False,
                'has_channel': True,
                'message': 'Unable to authenticate with YouTube'
            }
        
        # Build API services
        youtube = build_youtube_data_service(credentials)
        analytics = build_youtube_analytics_service(credentials)
        
        if not youtube or not analytics:
            return {
                'success': False, 
                'has_channel': True,
                'message': 'Failed to initialize YouTube API services'
            }
        
        # Get channel ID and basic info
        channel_id = profile.youtube_channel
        channel_info = get_channel_info(youtube, channel_id)
        
        if not channel_info:
            return {
                'success': False,
                'has_channel': True,
                'message': 'Failed to retrieve channel information'
            }
        
        # Calculate date range
        end_date = datetime.now().strftime('%Y-%m-%d')
        
        # For ALL_TIME, use channel creation date or a reasonable default
        if days == TimeRange.ALL_TIME.value:
            # Try to get channel creation date, default to 1 year ago if not available
            try:
                published_at = channel_info['snippet']['publishedAt']
                start_date = datetime.strptime(published_at, '%Y-%m-%dT%H:%M:%SZ').strftime('%Y-%m-%d')
            except (KeyError, ValueError):
                start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')
        else:
            start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        # Current channel statistics from channel_info
        stats = channel_info.get('statistics', {})
        current_subscribers = int(stats.get('subscriberCount', 0))
        total_views = int(stats.get('viewCount', 0))
        total_videos = int(stats.get('videoCount', 0))
        
        # Get analytics data
        analytics_data = get_analytics_report(analytics, channel_id, start_date, end_date)
        
        # Handle case where we have no data
        if not analytics_data or not analytics_data.get('rows'):
            return {
                'success': True,
                'has_channel': True,
                'has_data': False,
                'channel_name': channel_info['snippet']['title'],
                'channel_id': channel_id,
                'subscribers': current_subscribers,
                'total_views': total_views,
                'total_videos': total_videos,
                'dates': [],
                'views_data': [],
                'watch_time_data': [],
                'subscribers_data': [],
                'message': 'Not enough data available for the selected time range'
            }
        
        # Extract data from analytics response
        headers = [h['name'] for h in analytics_data['columnHeaders']]
        
        # Find column indices
        date_idx = headers.index('day')
        views_idx = headers.index('views')
        watch_time_idx = headers.index('estimatedMinutesWatched')
        subs_gained_idx = headers.index('subscribersGained')
        subs_lost_idx = headers.index('subscribersLost')
        likes_idx = headers.index('likes') if 'likes' in headers else None
        comments_idx = headers.index('comments') if 'comments' in headers else None
        
        # Process row data
        dates = []
        views_data = []
        watch_time_data = []
        watch_time_hours_data = []
        subscribers_data = []  # Net subscribers per day
        likes_data = []
        comments_data = []
        
        for row in analytics_data['rows']:
            dates.append(row[date_idx])
            views_data.append(int(row[views_idx]))
            
            watch_minutes = int(row[watch_time_idx])
            watch_time_data.append(watch_minutes)
            watch_time_hours_data.append(round(watch_minutes / 60, 1))
            
            net_subscribers = int(row[subs_gained_idx]) - int(row[subs_lost_idx])
            subscribers_data.append(net_subscribers)
            
            if likes_idx is not None:
                likes_data.append(int(row[likes_idx]))
            else:
                likes_data.append(0)
                
            if comments_idx is not None:
                comments_data.append(int(row[comments_idx]))
            else:
                comments_data.append(0)
        
        # Calculate period totals and metrics
        total_period_views = sum(views_data)
        total_watch_time_minutes = sum(watch_time_data)
        total_watch_time_hours = round(total_watch_time_minutes / 60, 1)
        net_subscribers_change = sum(subscribers_data)
        total_likes = sum(likes_data)
        
        # For period-over-period comparison (if we have enough data)
        if len(dates) > 1:
            # Split data in half to compare recent vs earlier
            mid_point = len(dates) // 2
            recent_views = sum(views_data[mid_point:])
            earlier_views = sum(views_data[:mid_point])
            views_change = recent_views - earlier_views
            
            recent_watch_time = sum(watch_time_data[mid_point:])
            earlier_watch_time = sum(watch_time_data[:mid_point])
            watch_time_change = round((recent_watch_time - earlier_watch_time) / 60, 1)
            
            recent_subs = sum(subscribers_data[mid_point:])
            earlier_subs = sum(subscribers_data[:mid_point])
            subs_change = recent_subs - earlier_subs
            
            recent_likes = sum(likes_data[mid_point:])
            earlier_likes = sum(likes_data[:mid_point])
            likes_change = recent_likes - earlier_likes
        else:
            views_change = 0
            watch_time_change = 0
            subs_change = 0
            likes_change = 0
        
        # Determine which view count to return
        # For ALL_TIME, use the lifetime total from channel info (total_views)
        # Otherwise, use the sum calculated for the period (total_period_views)
        final_views = total_views if days == TimeRange.ALL_TIME.value else total_period_views
        
        # Return formatted analytics data
        return {
            'success': True,
            'has_channel': True,
            'has_data': True,
            'channel_name': channel_info['snippet']['title'],
            'channel_id': channel_id,
            'subscribers': current_subscribers, # Always use current lifetime total
            'subscriber_change': net_subscribers_change,
            'views': final_views, # Use appropriate view total
            'view_change': views_change,
            'watch_time': total_watch_time_hours, # Sum over the period
            'watch_time_change': watch_time_change,
            'likes': total_likes,
            'like_change': likes_change,
            'total_videos': total_videos,
            'dates': dates,
            'views_data': views_data,
            'watch_time_data': watch_time_hours_data,
            'subscribers_data': subscribers_data,
            'message': None
        }
        
    except HttpError as e:
        error_message = str(e)
        logger.error(f"YouTube API error: {error_message}")
        
        if "available to OAuth" in error_message or "Service not enabled" in error_message:
            return {
                'success': False, 
                'has_channel': True,
                'needs_analytics_permission': True,
                'message': 'YouTube Analytics permissions required. Please reconnect your account with analytics access.'
            }
        else:
            return {
                'success': False,
                'has_channel': True,
                'message': f'YouTube API error: {error_message}'
            }
    except Exception as e:
        logger.error(f"Error in get_youtube_analytics: {str(e)}")
        return {
            'success': False,
            'message': f'Error retrieving analytics data: {str(e)}'
        } 