from django import forms
from .models import UserProfile
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['bio', 'location', 'birth_date', 'profile_picture', 'youtube_channel']
        widgets = {
            'birth_date': forms.DateInput(attrs={'type': 'date'}),
            'bio': forms.Textarea(attrs={'rows': 4}),
        }

class ExtendedProfileForm(forms.ModelForm):
    username = forms.CharField(max_length=150, required=True, help_text="Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.")
    
    class Meta:
        model = User
        fields = ['username']
        
    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This username is already taken.")
        return username 

class SignUpForm(UserCreationForm):
    email = forms.EmailField(max_length=254, required=True, help_text='Required. Enter a valid email address.')
    username = forms.CharField(
        max_length=20,
        required=True,
        help_text='Required. 20 characters or fewer. Letters, digits and @/./+/-/_ only.'
    )
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password1', 'password2']
    
    def clean_username(self):
        username = self.cleaned_data.get('username')
        if len(username) > 20:
            raise forms.ValidationError("Username must be 20 characters or fewer.")
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("This username is already taken.")
        return username
    
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']
        if commit:
            user.save()
            # UserProfile will be created by the signal in models.py
        return user 