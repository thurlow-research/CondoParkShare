"""
URL configuration for parkshare project.
"""
from django.contrib import admin
from django.urls import path

from accounts import views as account_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('admin/impersonation/end/', account_views.impersonation_end, name='impersonation_end'),
]
