from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from datetime import datetime, timedelta
import logging
from .youtube_analytics import get_youtube_analytics
import random

logger = logging.getLogger(__name__)

@login_required
@require_GET
def statistics_api(request):
    """API endpoint to get YouTube analytics data for different time ranges"""
    days_param = request.GET.get('days', '30')
    
    # Handle "all" as a special case
    if days_param == 'all':
        days = 9999  # Use 9999 days for true "all time" option
        period_name = "All Time"
    else:
        try:
            days = int(days_param)
        except ValueError:
            days = 30  # Default to 30 days if invalid input
        
        # Map days to standard time periods
        if days == 1:
            days = 1  # 1 day
            period_name = "1 Day"
        elif days <= 10:
            days = 10  # 10 days
            period_name = "10 Days"
        elif days <= 30:
            days = 30  # 1 month
            period_name = "30 Days"
        elif days <= 180:
            days = 180  # 6 months
            period_name = "6 Months"
        elif days <= 365:
            days = 365  # 1 year
            period_name = "1 Year"
        else:
            days = 365  # Default to 1 year max
            period_name = "1 Year"
    
    logger.info(f"Getting statistics for {period_name} (days={days}) for user: {request.user.username}")
    
    try:
        # Get profile data
        profile = request.user.userprofile
        
        # If not connected to YouTube, return empty stats but with success flag
        if not profile.youtube_connected:
            logger.warning(f"User {request.user.username} has not connected YouTube, returning empty stats")
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
                'subscribers_data': [],
                'period': period_name
            })
        
        # Get analytics data for the specified time range
        # Always use mock data for testing
        analytics_result = get_youtube_analytics(request.user, use_mock_data_on_error=True, days=days)
        
        if analytics_result and analytics_result.get('has_data'):
            basic_metrics = analytics_result.get('basic_metrics', [])
            
            if not basic_metrics or len(basic_metrics) == 0:
                logger.warning(f"No basic metrics available for user {request.user.username}")
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
                    'subscribers_data': [],
                    'period': period_name,
                    'is_mock_data': analytics_result.get('is_mock_data', False)
                })
            
            # Calculate totals
            total_views = sum(row[1] for row in basic_metrics)
            
            # Check if watch time is available in the metrics (index 2)
            if len(basic_metrics[0]) > 2:
                total_watch_time = sum(row[2] for row in basic_metrics) 
            else:
                # Estimate watch time based on views
                total_watch_time = total_views * 3  # Assuming 3 minutes per view
            
            # Check if subscribers is available in the metrics (index 4)
            if len(basic_metrics[0]) > 4:
                total_subscribers = sum(row[4] for row in basic_metrics) 
            else:
                # Use a placeholder value
                total_subscribers = int(total_views * 0.01)  # Assuming 1% of views = new subscribers
            
            # Convert watch time to hours
            watch_time_hours = round(total_watch_time / 60, 1)
            
            # Calculate likes (estimate as 5% of views)
            total_likes = int(total_views * 0.05)
            
            # Calculate changes by comparing recent data to previous period
            if len(basic_metrics) > 1:
                half_point = len(basic_metrics) // 2
                recent_period = basic_metrics[half_point:]
                older_period = basic_metrics[:half_point]
                
                # Calculate views change
                recent_views = sum(row[1] for row in recent_period)
                older_views = sum(row[1] for row in older_period)
                view_change = recent_views - older_views
                
                # Calculate watch time change
                if len(basic_metrics[0]) > 2:
                    recent_watch_time = sum(row[2] for row in recent_period)
                    older_watch_time = sum(row[2] for row in older_period)
                else:
                    recent_watch_time = recent_views * 3
                    older_watch_time = older_views * 3
                watch_time_change = round((recent_watch_time - older_watch_time) / 60, 1)
                
                # Calculate subscriber change
                if len(basic_metrics[0]) > 4:
                    recent_subs = sum(row[4] for row in recent_period)
                    older_subs = sum(row[4] for row in older_period)
                    sub_change = recent_subs - older_subs
                else:
                    sub_change = int(view_change * 0.01)
                
                # Calculate like change
                like_change = int(view_change * 0.05)
            else:
                view_change = 0
                watch_time_change = 0
                sub_change = 0
                like_change = 0
            
            # Also include the raw data for charts
            dates = [row[0] for row in basic_metrics]
            views_data = [row[1] for row in basic_metrics]
            
            if len(basic_metrics[0]) > 2:
                watch_time_data = [row[2] for row in basic_metrics]
            else:
                watch_time_data = [views * 3 for views in views_data]
                
            if len(basic_metrics[0]) > 4:
                subscribers_data = [row[4] for row in basic_metrics]
            else:
                subscribers_data = [int(views * 0.01) for views in views_data]
            
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
                'period': period_name,
                'is_mock_data': analytics_result.get('is_mock_data', True)
            })
        else:
            # Return empty stats with a message about why data might be missing
            message = analytics_result.get('message', 'No analytics data available') if analytics_result else 'Could not retrieve analytics data'
            logger.warning(f"No analytics data for user {request.user.username}: {message}")
            
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
                'subscribers_data': [],
                'period': period_name,
                'message': message
            })
            
    except Exception as e:
        logger.error(f"Error in statistics_api: {str(e)}")
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500) 