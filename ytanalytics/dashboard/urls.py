from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),  # The dashboard view
    path('about/', views.about, name='about'),  # About page
]
