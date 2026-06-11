"""
notifications.models — WebPushSubscription, RelayMessage.

WebPushSubscription stores user VAPID push subscription data.
RelayMessage provides the email relay messaging feature (NEW-1).
"""

import uuid
from django.db import models


class WebPushSubscription(models.Model):
    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='push_subscriptions',
    )
    endpoint = models.URLField(max_length=500, unique=True)
    p256dh = models.CharField(max_length=256)
    auth = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'PushSub user={self.user_id} {self.endpoint[:40]}…'


class RelayMessage(models.Model):
    organization = models.ForeignKey('parking.Organization', on_delete=models.PROTECT)
    from_user = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='sent_relay_messages',
    )
    to_user = models.ForeignKey(
        'accounts.User',
        on_delete=models.PROTECT,
        related_name='received_relay_messages',
    )
    booking = models.ForeignKey(
        'parking.Booking',
        on_delete=models.PROTECT,
        related_name='relay_messages',
    )
    body = models.TextField()
    reply_token = models.UUIDField(default=uuid.uuid4, unique=True)
    token_expires_at = models.DateTimeField()   # = booking.time_range.upper
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'RelayMessage {self.pk} from={self.from_user_id} to={self.to_user_id}'
