"""
URL configuration for parkshare project.
"""
from django.urls import include, path

from accounts import views as account_views
from parkshare.admin_site import operator_admin_site

urlpatterns = [
    path('accounts/', include('accounts.urls')),
    path('', include('parking.urls')),
    path('messages/', include('notifications.urls')),
    path('portal/', include('portal.urls')),
    path('admin/', operator_admin_site.urls),
    # Operator impersonation end — lives under /admin/ per design doc §4
    path('admin/impersonation/end/', account_views.impersonation_end, name='impersonation_end'),
]
