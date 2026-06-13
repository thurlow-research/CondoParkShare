"""
notifications.urls — URL patterns for push subscriptions and relay messaging.

Included in parkshare/urls.py under the 'messages/' prefix.
"""

from django.urls import path

from notifications import views

urlpatterns = [
    # Web push subscription management
    path("push/subscribe/", views.push_subscribe, name="push_subscribe"),
    path("push/unsubscribe/", views.push_unsubscribe, name="push_unsubscribe"),
    # Email relay messaging
    path("send/<int:booking_pk>/", views.message_send, name="message_send"),
    path("reply/<uuid:token>/", views.message_reply, name="message_reply"),
]
