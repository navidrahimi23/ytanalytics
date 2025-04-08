import os
import logging
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from django.conf import settings

logger = logging.getLogger(__name__)

def get_youtube_api_key():
    """Retrieve YouTube API key from settings"""
    return settings.YT_API_KEY

def build_youtube_service():
    """Build and return a YouTube API service object"""
    api_key = get_youtube_api_key()
    if not api_key:
        logger.error("YouTube API key not found in environment variables")
        return None
    
    try:
        return build('youtube', 'v3', developerKey=api_key)
    except Exception as e:
        logger.error(f"Error building YouTube service: {str(e)}")
        return None

def search_channel(query, max_results=5):
    """
    Search for YouTube channels by name or handle
    
    Parameters:
    - query: The search query (channel name or handle)
    - max_results: Maximum number of results to return
    
    Returns:
    - Dictionary containing search results or error message
    """
    youtube = build_youtube_service()
    if not youtube:
        return {'error': 'API configuration error'}
    
    try:
        # First search for channels
        search_response = youtube.search().list(
            q=query,
            part='snippet',
            type='channel',
            maxResults=max_results
        ).execute()
        
        channels = []
        for item in search_response.get('items', []):
            channel_id = item['id']['channelId']
            
            # Extract basic info from search results
            channels.append({
                'id': channel_id,
                'title': item['snippet']['title'],
                'description': item['snippet']['description'],
                'thumbnail': item['snippet']['thumbnails']['medium']['url'],
                'published_at': item['snippet']['publishedAt']
            })
        
        return {
            'success': True,
            'channels': channels
        }
    
    except HttpError as e:
        logger.error(f"YouTube API error in search_channel: {str(e)}")
        return {
            'error': 'API error',
            'message': str(e)
        }
    except Exception as e:
        logger.error(f"Unexpected error in search_channel: {str(e)}")
        return {
            'error': 'Unexpected error',
            'message': str(e)
        }

def get_channel_stats(channel_id):
    """
    Get public channel statistics using channel ID
    
    Parameters:
    - channel_id: YouTube channel ID
    
    Returns:
    - Dictionary containing channel statistics
    """
    youtube = build_youtube_service()
    if not youtube:
        return {'error': 'API configuration error'}
    
    try:
        # Get channel statistics
        channel_response = youtube.channels().list(
            part='snippet,statistics,brandingSettings',
            id=channel_id
        ).execute()
        
        if not channel_response.get('items'):
            return {
                'error': 'Channel not found',
                'message': 'No channel found with the given ID'
            }
        
        channel_info = channel_response['items'][0]
        snippet = channel_info['snippet']
        statistics = channel_info['statistics']
        
        # Extract relevant data
        result = {
            'id': channel_id,
            'title': snippet['title'],
            'description': snippet.get('description', ''),
            'thumbnail': snippet['thumbnails']['high']['url'],
            'published_at': snippet['publishedAt'],
            'country': snippet.get('country', 'Unknown'),
            'view_count': statistics.get('viewCount', 0),
            'subscriber_count': statistics.get('subscriberCount', 0),
            'video_count': statistics.get('videoCount', 0),
            'custom_url': snippet.get('customUrl', ''),
        }
        
        # Get banner image if available
        if 'brandingSettings' in channel_info and 'image' in channel_info['brandingSettings']:
            result['banner_url'] = channel_info['brandingSettings']['image'].get('bannerExternalUrl', '')
        
        return {
            'success': True,
            'channel': result
        }
        
    except HttpError as e:
        logger.error(f"YouTube API error in get_channel_stats: {str(e)}")
        return {
            'error': 'API error',
            'message': str(e)
        }
    except Exception as e:
        logger.error(f"Unexpected error in get_channel_stats: {str(e)}")
        return {
            'error': 'Unexpected error',
            'message': str(e)
        }

def get_recent_video_stats(channel_id, max_results=10):
    """
    Get recent videos and their stats from a channel
    
    Parameters:
    - channel_id: YouTube channel ID
    - max_results: Maximum number of videos to retrieve
    
    Returns:
    - Dictionary containing videos and their statistics
    """
    youtube = build_youtube_service()
    if not youtube:
        return {'error': 'API configuration error'}
    
    try:
        # First, get recent videos from the channel
        search_response = youtube.search().list(
            channelId=channel_id,
            part='id,snippet',
            order='date',
            type='video',
            maxResults=max_results
        ).execute()
        
        if not search_response.get('items'):
            return {
                'success': True,
                'videos': []
            }
        
        # Extract video IDs
        video_ids = [item['id']['videoId'] for item in search_response['items']]
        
        # Get detailed statistics for all videos in a single request
        videos_response = youtube.videos().list(
            part='snippet,statistics,contentDetails',
            id=','.join(video_ids)
        ).execute()
        
        videos = []
        for item in videos_response.get('items', []):
            video_id = item['id']
            snippet = item['snippet']
            statistics = item['statistics']
            content_details = item['contentDetails']
            
            # Parse duration string (in ISO 8601 format)
            duration = content_details.get('duration', 'PT0S')
            # We won't parse the duration here, but we could add a helper function
            
            videos.append({
                'id': video_id,
                'title': snippet['title'],
                'description': snippet.get('description', ''),
                'thumbnail': snippet['thumbnails']['medium']['url'],
                'published_at': snippet['publishedAt'],
                'view_count': statistics.get('viewCount', 0),
                'like_count': statistics.get('likeCount', 0),
                'comment_count': statistics.get('commentCount', 0),
                'duration': duration,
                'url': f"https://www.youtube.com/watch?v={video_id}"
            })
        
        return {
            'success': True,
            'videos': videos
        }
        
    except HttpError as e:
        logger.error(f"YouTube API error in get_recent_video_stats: {str(e)}")
        return {
            'error': 'API error',
            'message': str(e)
        }
    except Exception as e:
        logger.error(f"Unexpected error in get_recent_video_stats: {str(e)}")
        return {
            'error': 'Unexpected error',
            'message': str(e)
        } 