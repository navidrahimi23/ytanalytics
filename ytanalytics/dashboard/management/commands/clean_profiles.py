from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from dashboard.models import UserProfile
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Clean up user profiles and ensure each user has exactly one profile'

    def handle(self, *args, **options):
        self.stdout.write('Checking user profiles...')
        
        # Get all users
        users = User.objects.all()
        fixed_count = 0
        
        for user in users:
            # Check if user has a profile
            try:
                # Try to get the profile
                profile = UserProfile.objects.get(user=user)
                self.stdout.write(f'User {user.username} has a profile')
            except UserProfile.DoesNotExist:
                # Create profile if it doesn't exist
                UserProfile.objects.create(user=user)
                self.stdout.write(self.style.SUCCESS(f'Created new profile for user {user.username}'))
                fixed_count += 1
            except UserProfile.MultipleObjectsReturned:
                # Handle duplicate profiles
                self.stdout.write(self.style.WARNING(f'Multiple profiles found for user {user.username}'))
                
                # Get all profiles for this user
                profiles = UserProfile.objects.filter(user=user)
                
                # Keep the first one, delete the rest
                primary_profile = profiles.first()
                for profile in profiles[1:]:
                    self.stdout.write(f'Deleting duplicate profile (ID: {profile.id}) for user {user.username}')
                    profile.delete()
                
                fixed_count += 1
        
        # Final summary
        if fixed_count > 0:
            self.stdout.write(self.style.SUCCESS(f'Fixed {fixed_count} user profile issues'))
        else:
            self.stdout.write(self.style.SUCCESS('All user profiles are in good shape!')) 