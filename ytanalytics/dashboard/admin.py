from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import UserProfile

# Define inline admin for UserProfile
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = 'User Profile'
    fk_name = 'user'

# Extend the existing UserAdmin
class CustomUserAdmin(UserAdmin):
    inlines = (UserProfileInline, )
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'get_youtube_connected')
    
    def get_youtube_connected(self, instance):
        try:
            return instance.userprofile.youtube_connected
        except UserProfile.DoesNotExist:
            return False
    get_youtube_connected.short_description = 'YouTube Connected'
    get_youtube_connected.boolean = True

# Re-register UserAdmin with our custom admin
admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)

# Register UserProfile directly as well
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'youtube_connected', 'tiktok_connected', 'instagram_connected')
    search_fields = ('user__username', 'user__email')
    list_filter = ('youtube_connected', 'tiktok_connected', 'instagram_connected')
