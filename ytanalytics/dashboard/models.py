from django.db import models
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    bio = models.TextField(max_length=500, blank=True)
    location = models.CharField(max_length=100, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    profile_picture = models.ImageField(upload_to='profile_pics/', blank=True, null=True)
    
    # Network Fields
    connected_users = models.ManyToManyField(User, related_name='connected_to', blank=True)
    
    # Social Media Integration Fields
    youtube_channel = models.CharField(max_length=100, blank=True)
    youtube_connected = models.BooleanField(default=False)
    youtube_access_token = models.CharField(max_length=255, blank=True)
    youtube_refresh_token = models.CharField(max_length=255, blank=True)
    has_analytics_access = models.BooleanField(default=False)
    
    tiktok_username = models.CharField(max_length=100, blank=True)
    tiktok_connected = models.BooleanField(default=False)
    tiktok_access_token = models.CharField(max_length=255, blank=True)
    tiktok_refresh_token = models.CharField(max_length=255, blank=True)
    
    instagram_username = models.CharField(max_length=100, blank=True)
    instagram_connected = models.BooleanField(default=False)
    instagram_access_token = models.CharField(max_length=255, blank=True)
    instagram_refresh_token = models.CharField(max_length=255, blank=True)
    
    def __str__(self):
        return f"{self.user.username}'s profile"
        
    def get_avatar_url(self):
        """Returns the URL for the user's profile picture"""
        if self.profile_picture and hasattr(self.profile_picture, 'url'):
            return self.profile_picture.url
        # Return a default avatar URL if no profile picture is set
        return '/static/images/default-avatar.png'
        
    def get_subscribers(self):
        """Get subscriber count for display in UI"""
        # If we have channel stats, return them
        if self.youtube_channel:
            try:
                stats = ChannelStats.objects.filter(channel_id=self.youtube_channel).first()
                if stats:
                    return stats.subscribers
            except:
                pass
        # Return a placeholder value if no stats available
        return 0
        
    def get_views(self):
        """Get view count for display in UI"""
        # If we have channel stats, return them
        if self.youtube_channel:
            try:
                stats = ChannelStats.objects.filter(channel_id=self.youtube_channel).first()
                if stats:
                    return stats.views
            except:
                pass
        # Return a placeholder value if no stats available
        return 0
        
    def get_video_count(self):
        """Get video count for display in UI"""
        # Try to get video count using YouTube API directly
        if self.youtube_connected and self.youtube_channel:
            try:
                from django.conf import settings
                from social_django.models import UserSocialAuth
                from google.oauth2.credentials import Credentials
                from googleapiclient.discovery import build
                import logging
                
                logger = logging.getLogger(__name__)
                
                # Get the social auth credentials
                social = UserSocialAuth.objects.get(user=self.user, provider='google-oauth2')
                creds_data = social.extra_data
                
                # Create credentials object
                credentials = Credentials(
                    token=creds_data.get('access_token'),
                    refresh_token=creds_data.get('refresh_token'),
                    token_uri='https://oauth2.googleapis.com/token',
                    client_id=settings.SOCIAL_AUTH_GOOGLE_OAUTH2_KEY,
                    client_secret=settings.SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET
                )
                
                # Build the YouTube API client
                youtube = build('youtube', 'v3', credentials=credentials)
                
                # Get channel statistics
                channel_response = youtube.channels().list(
                    part="statistics",
                    id=self.youtube_channel
                ).execute()
                
                # Extract and return video count
                if channel_response.get('items'):
                    stats = channel_response['items'][0]['statistics']
                    return int(stats.get('videoCount', 0))
            except Exception as e:
                logger.error(f"Error getting video count for {self.user.username}: {str(e)}")
        
        # Return a placeholder value if no stats available
        return 0

# Signal to create/update UserProfile when User is created/updated
@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    # Use get_or_create to avoid race conditions
    UserProfile.objects.get_or_create(user=instance)

class ChannelStats(models.Model):
    """
    Model to store historical YouTube channel statistics for trend analysis
    """
    channel_id = models.CharField(max_length=255, db_index=True)
    date = models.DateField(default=timezone.now, db_index=True)
    views = models.BigIntegerField(default=0)
    subscribers = models.BigIntegerField(default=0)
    
    class Meta:
        unique_together = ('channel_id', 'date')
        ordering = ['-date']
        indexes = [
            models.Index(fields=['channel_id', 'date']),
        ]
    
    def __str__(self):
        return f"{self.channel_id} - {self.date.strftime('%Y-%m-%d')}"

class Link(models.Model):
    """Model to represent connections between users"""
    from_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='links_sent')
    to_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='links_received')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = ('from_user', 'to_user')
        verbose_name = 'Link'
        verbose_name_plural = 'Links'
    
    def __str__(self):
        return f"{self.from_user.username} → {self.to_user.username}"
    
    @classmethod
    def is_linked(cls, user1, user2):
        """Check if user1 has linked to user2"""
        return cls.objects.filter(from_user=user1, to_user=user2).exists()
    
    @classmethod
    def is_mutually_linked(cls, user1, user2):
        """Check if there's a mutual link between user1 and user2"""
        return (cls.objects.filter(from_user=user1, to_user=user2).exists() and
                cls.objects.filter(from_user=user2, to_user=user1).exists())
    
    @classmethod
    def get_linked_users(cls, user):
        """Get all users that have a mutual link with the given user"""
        sent_links = cls.objects.filter(from_user=user).values_list('to_user', flat=True)
        return User.objects.filter(id__in=sent_links, links_sent__to_user=user)
    
    @classmethod
    def get_link_status(cls, from_user, to_user):
        """Get the status of the link between two users
        
        Returns:
            None: No link exists
            'one-way': Only from_user has linked to to_user
            'mutual': Both users have linked to each other
        """
        link_exists = cls.is_linked(from_user, to_user)
        reverse_link_exists = cls.is_linked(to_user, from_user)
        
        if link_exists and reverse_link_exists:
            return 'mutual'
        elif link_exists:
            return 'one-way'
        else:
            return None
