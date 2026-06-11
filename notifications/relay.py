"""
notifications.relay — Email relay messaging between owners and borrowers.

Implements the email relay system described in CONFIRMED-REQUIREMENTS.md §F18.
Phone numbers and email addresses are never exposed to residents; communication
flows through this relay with rate limiting and expiring reply tokens.
"""

MAX_MESSAGES_PER_USER_PER_BOOKING = 10


def can_send_relay(from_user, booking):
    """Return True if from_user has not hit the per-booking message cap."""
    from notifications.models import RelayMessage

    return (
        RelayMessage.objects.filter(from_user=from_user, booking=booking).count()
        < MAX_MESSAGES_PER_USER_PER_BOOKING
    )


def send_relay_message(from_user, to_user, booking, body, reply_to=None):
    """
    Create a RelayMessage and send the relay email.

    Returns the created RelayMessage.
    Raises ValueError if the per-booking message cap has been reached.

    ``reply_to`` is an optional RelayMessage that this message is responding to;
    it is accepted for context but not stored (no in-app thread per design).
    """
    from notifications.models import RelayMessage
    from django.core.mail import send_mail
    from django.urls import reverse
    import uuid

    if not can_send_relay(from_user, booking):
        raise ValueError('Message limit reached for this booking')

    token = uuid.uuid4()
    expires_at = booking.time_range.upper

    msg = RelayMessage.objects.create(
        organization=booking.organization,
        from_user=from_user,
        to_user=to_user,
        booking=booking,
        body=body,
        reply_token=token,
        token_expires_at=expires_at,
    )

    reply_url = (
        f"https://{booking.organization.hostname}"
        f"{reverse('message_reply', kwargs={'token': token})}"
    )

    send_mail(
        subject=f'Message about spot {booking.spot.spot_number}',
        message=(
            f'{body}\n\n'
            f'— Reply to this message: {reply_url}\n'
            f'(This link is valid until the booking ends.)'
        ),
        from_email=f'noreply@{booking.organization.hostname}',
        recipient_list=[to_user.email],
        fail_silently=True,
    )
    return msg
