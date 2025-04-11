from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET
from datetime import datetime, timedelta
import logging
from .youtube_analytics import get_youtube_analytics
import random
from .views import get_recent_videos
from django.core.cache import cache
import time
import concurrent.futures

logger = logging.getLogger(__name__)

@login_required
@require_GET
def statistics_api(request):
    """API endpoint to get YouTube analytics data for different time ranges"""
    start_time = time.time()
    request_id = random.randint(1000, 9999)  # For tracking this specific request in logs
    
    # Get and validate time range parameter
    days = request.GET.get('days', '30')
    logger.info(f"[REQ-{request_id}] Statistics API request for user {request.user.username}, time range: {days}")
    
    # Handle 'all' as a special case
    if days == 'all':
        # Use a very large number for all time data
        days = 9999
    else:
        try:
            days = int(days)
            if days <= 0:
                logger.warning(f"[REQ-{request_id}] Invalid days parameter: {days}, defaulting to 30")
                days = 30
        except ValueError:
            logger.warning(f"[REQ-{request_id}] Invalid days parameter: {days}, defaulting to 30")
            days = 30  # Default to 30 days if invalid input
    
    # Create cache key based on user and days
    cache_key = f"stats_api_{request.user.id}_{days}"
    cached_response = cache.get(cache_key)
    
    if cached_response:
        logger.info(f"[REQ-{request_id}] Serving cached response for user {request.user.username}")
        duration = time.time() - start_time
        logger.info(f"[REQ-{request_id}] Request completed in {duration:.2f}s (cached)")
        return JsonResponse(cached_response)
    
    try:
        # Get profile data
        profile = request.user.userprofile
        
        # If not connected to YouTube, return error response
        if not profile.youtube_connected:
            logger.warning(f"[REQ-{request_id}] User {request.user.username} has no connected YouTube channel")
            return JsonResponse({
                'success': False,
                'message': 'Please connect your YouTube channel to view analytics.'
            })
        
        # Get analytics data for the specified time range with timeout handling
        try:
            with concurrent.futures.ThreadPoolExecutor() as executor:
                # Set a timeout for the analytics call
                future = executor.submit(get_youtube_analytics, request.user, days=days)
                analytics_result = future.result(timeout=15)  # 15 second timeout
        except concurrent.futures.TimeoutError:
            logger.error(f"[REQ-{request_id}] Analytics request timed out for user {request.user.username}")
            return JsonResponse({
                'success': False,
                'message': 'The request timed out. Please try again later.'
            }, status=504)  # Gateway Timeout
        
        if analytics_result and analytics_result.get('has_data'):
            basic_metrics = analytics_result.get('basic_metrics', [])
            
            # Log successful data retrieval
            logger.info(f"[REQ-{request_id}] Successfully retrieved analytics data: {len(basic_metrics)} data points")
            
            # Process data or use pre-calculated values from the analytics result
            total_subscribers = analytics_result.get('total_subscribers', 0)
            total_views = analytics_result.get('views', 0)
            total_watch_time_hours = analytics_result.get('watch_time', 0)
            
            # Calculate changes by comparing recent data to previous period
            if len(basic_metrics) > 1:
                half_point = len(basic_metrics) // 2
                recent_period = basic_metrics[half_point:]
                older_period = basic_metrics[:half_point]
                
                view_change = sum(row[1] for row in recent_period) - sum(row[1] for row in older_period)
                watch_time_change = round((sum(row[2] for row in recent_period) - sum(row[2] for row in older_period)) / 60, 1)
                sub_change = sum(row[3] for row in recent_period) - sum(row[3] for row in older_period)
            else:
                view_change = 0
                watch_time_change = 0
                sub_change = analytics_result.get('net_subscriber_change', 0)
            
            # Include the raw data for charts
            dates = [row[0] for row in basic_metrics]
            views_data = [row[1] for row in basic_metrics]
            watch_time_data = [row[2] for row in basic_metrics]
            subscribers_data = [row[3] for row in basic_metrics]
            
            response_data = {
                'success': True,
                'subscribers': total_subscribers,
                'subscriber_change': sub_change,
                'views': total_views,
                'view_change': view_change,
                'watch_time': total_watch_time_hours,
                'watch_time_change': watch_time_change,
                'dates': dates,
                'views_data': views_data,
                'watch_time_data': watch_time_data,
                'subscribers_data': subscribers_data
            }
            
            # Cache the successful response (short timeout)
            cache.set(cache_key, response_data, timeout=300)  # 5 minutes cache
            
            duration = time.time() - start_time
            logger.info(f"[REQ-{request_id}] Request completed successfully in {duration:.2f}s")
            
            return JsonResponse(response_data)
        else:
            # Return error response with the specific message from analytics_result
            logger.warning(f"[REQ-{request_id}] No analytics data: {analytics_result.get('message')}")
            return JsonResponse({
                'success': False,
                'message': analytics_result.get('message', 'Could not retrieve analytics data.')
            })
            
    except Exception as e:
        # Log the full exception details for debugging
        logger.exception(f"[REQ-{request_id}] Error in statistics_api: {str(e)}")
        
        # Return a user-friendly error message
        return JsonResponse({
            'success': False,
            'message': 'An error occurred while retrieving your analytics data. Please try again later.'
        }, status=500)
    finally:
        # Always log the total duration
        duration = time.time() - start_time
        logger.info(f"[REQ-{request_id}] Total request processing time: {duration:.2f}s") 