import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from dashboard.models import ChannelStats
from dashboard.youtube_api import get_channel_stats

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Updates statistics for all YouTube channels in the database'

    def handle(self, *args, **options):
        today = timezone.now().date()
        self.stdout.write(f"Starting channel stats update for {today}")
        
        # Get unique channel IDs from the ChannelStats model
        channel_ids = ChannelStats.objects.values_list('channel_id', flat=True).distinct()
        
        if not channel_ids:
            self.stdout.write(self.style.WARNING("No channels found in the database to update"))
            return
        
        updated_count = 0
        error_count = 0
        
        for channel_id in channel_ids:
            self.stdout.write(f"Updating stats for channel: {channel_id}")
            
            # Call YouTube API to get current stats
            response = get_channel_stats(channel_id)
            
            if response.get('success'):
                channel_data = response.get('channel', {})
                
                # Create or update stats for today
                stats, created = ChannelStats.objects.update_or_create(
                    channel_id=channel_id,
                    date=today,
                    defaults={
                        'views': channel_data.get('view_count', 0),
                        'subscribers': channel_data.get('subscriber_count', 0)
                    }
                )
                
                status = "Created" if created else "Updated"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{status} stats for {channel_id}: "
                        f"Views: {stats.views}, Subscribers: {stats.subscribers}"
                    )
                )
                updated_count += 1
            else:
                error_message = response.get('message', 'Unknown error')
                self.stdout.write(
                    self.style.ERROR(f"Failed to update stats for {channel_id}: {error_message}")
                )
                error_count += 1
        
        # Final summary
        self.stdout.write(
            self.style.SUCCESS(
                f"Completed channel stats update. "
                f"Updated: {updated_count}, Errors: {error_count}, Total channels: {len(channel_ids)}"
            )
        ) 