"""
accounts.urls — URL patterns for authentication and account management.

Included in parkshare/urls.py under the 'accounts/' prefix.
"""

from django.urls import path

from accounts import views

urlpatterns = [
    # Auth
    path('login/',                    views.login_view,               name='login'),
    path('logout/',                   views.logout_view,              name='logout'),

    # Two-factor verification
    path('totp/verify/',              views.totp_verify,              name='totp_verify'),
    path('totp/enroll/',              views.totp_enroll,              name='totp_enroll'),

    # Recovery paths
    path('recovery/',                 views.recovery_code,            name='recovery_code'),
    path('lost-authenticator/',       views.lost_authenticator,       name='lost_authenticator'),
    path('lost-authenticator/verify/', views.lost_authenticator_verify, name='lost_authenticator_verify'),

    # Registration
    path('register/',                 views.register,                 name='register'),
    path('register/<str:code>/',      views.register_invite,          name='register_invite'),

    # Profile and preferences
    path('profile/',                  views.profile,                  name='profile'),
    path('notifications/',            views.notification_prefs,       name='notification_prefs'),

]
