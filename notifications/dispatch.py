"""
notifications.dispatch — Notification dispatcher.

notify(event, booking) routes events to the correct recipients via email
and (optionally) web push.  See TECHNICAL-DESIGN.md §10.
"""

OWNER_EVENTS = {
    "booking_confirmed",
    "booking_completed",
    "warning_30",
    "warning_15",
    "booking_cancelled_by_borrower",
    "booking_cancelled_by_owner",
    "booking_cancelled_by_admin",
    "early_release_confirmed",
}

BORROWER_EVENTS = {
    "booking_confirmed",
    "booking_starts",
    "booking_completed",
    "warning_30",
    "warning_15",
    "booking_cancelled_by_borrower",
    "booking_cancelled_by_owner",
    "booking_cancelled_by_admin",
    "early_release_confirmed",
}


def notify(event, booking, **kwargs):
    """Dispatch a notification event to the appropriate recipients."""
    owner = booking.spot.owner
    borrower = booking.borrower

    if event in OWNER_EVENTS and owner:
        _send(owner, event, booking, **kwargs)
    if event in BORROWER_EVENTS and borrower:
        _send(borrower, event, booking, **kwargs)


def _send(user, event, booking, **kwargs):
    """Route a notification to email and optionally push for a single user."""
    _send_email(user, event, booking, **kwargs)
    if user.notification_prefs.get("push") and user.push_subscriptions.exists():
        _send_push(user, event, booking)


def _send_email(user, event, booking, **kwargs):
    """Send a transactional email notification for an event."""
    from django.core.mail import send_mail

    subject, body = _render_notification(event, booking, user, **kwargs)
    send_mail(
        subject=subject,
        message=body,
        from_email=None,  # uses DEFAULT_FROM_EMAIL from settings
        recipient_list=[user.email],
        fail_silently=True,
    )


def _send_push(user, event, booking):
    """Send a web push notification via pywebpush (no-op if pywebpush not installed)."""
    try:
        import json

        import requests
        from django.conf import settings
        from pywebpush import WebPushException, webpush

        payload = json.dumps({"event": event, "booking_id": booking.pk})
        for sub in user.push_subscriptions.all():
            try:
                webpush(
                    subscription_info={
                        "endpoint": sub.endpoint,
                        "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                    },
                    data=payload,
                    vapid_private_key=settings.VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": f"mailto:{settings.VAPID_ADMIN_EMAIL}"},
                    # (connect, read) timeout — without this a hung push endpoint
                    # blocks the gunicorn worker indefinitely. See #157.
                    timeout=(10, 30),
                )
            except (WebPushException, requests.exceptions.RequestException):
                pass  # individual push failure/timeout does not abort
    except ImportError:
        pass


def _render_notification(event, booking, recipient, **kwargs):
    """Return (subject, body) tuple for a notification event."""
    org = booking.organization
    spot = booking.spot

    import zoneinfo

    tz = zoneinfo.ZoneInfo(org.timezone)
    local_start = booking.time_range.lower.astimezone(tz)
    local_end = booking.time_range.upper.astimezone(tz)
    time_str = (
        f"{local_start.strftime('%a %b %d, %I:%M %p')} – "
        f"{local_end.strftime('%I:%M %p %Z')}"
    )

    subjects = {
        "booking_confirmed": f"Booking confirmed — {spot.spot_number}",
        "booking_starts": f"Your booking at {spot.spot_number} has started",
        "booking_completed": f"Booking at {spot.spot_number} complete",
        "warning_30": f"30 minutes left — {spot.spot_number}",
        "warning_15": f"15 minutes left — {spot.spot_number}",
        "booking_cancelled_by_borrower": f"Booking cancelled — {spot.spot_number}",
        "booking_cancelled_by_owner": f"Your booking was cancelled — {spot.spot_number}",
        "booking_cancelled_by_admin": f"Booking cancelled by HOA — {spot.spot_number}",
        "early_release_confirmed": f"Spot released early — {spot.spot_number}",
    }
    bodies = {
        "booking_confirmed": f"{spot.spot_number} is booked for {time_str}.",
        "booking_starts": f"Your booking at {spot.spot_number} starts now. {time_str}.",
        "booking_completed": f"Booking at {spot.spot_number} is complete. {time_str}.",
        "warning_30": f'30 minutes remaining on {spot.spot_number}. End: {local_end.strftime("%I:%M %p %Z")}.',
        "warning_15": f'15 minutes remaining on {spot.spot_number}. End: {local_end.strftime("%I:%M %p %Z")}.',
        "booking_cancelled_by_borrower": f"Booking at {spot.spot_number} ({time_str}) was cancelled by the resident.",
        "booking_cancelled_by_owner": (
            f"Your booking at {spot.spot_number} ({time_str}) was cancelled by the owner."
            + (f" Reason: {booking.cancel_reason}" if booking.cancel_reason else "")
        ),
        "booking_cancelled_by_admin": (
            f"Your booking at {spot.spot_number} ({time_str}) was cancelled by the HOA."
            + (f" Reason: {booking.cancel_reason}" if booking.cancel_reason else "")
        ),
        "early_release_confirmed": (
            f"Spot {spot.spot_number} released early. "
            f'Updated end: {local_end.strftime("%I:%M %p %Z")}.'
        ),
    }

    subject = subjects.get(event, "CondoParkShare notification")
    body = bodies.get(event, "")
    return subject, body
