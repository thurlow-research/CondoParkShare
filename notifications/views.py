"""
notifications.views — Web push subscription management and email relay messaging.

Views:
  push_subscribe      POST  — register a push subscription for the current user
  push_unsubscribe    POST  — remove a push subscription by endpoint
  message_send        GET/POST  — send a relay message for a booking
  message_reply       GET/POST  — reply to a relay message via its token
"""

import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.timezone import now
from django.views.decorators.http import require_POST

from accounts.decorators import active_required
from notifications.forms import MessageForm
from notifications.models import RelayMessage, WebPushSubscription
from notifications.relay import can_send_relay, send_relay_message
from parking.models import Booking


# ---------------------------------------------------------------------------
# Web push subscription management
# ---------------------------------------------------------------------------

@login_required
@require_POST
def push_subscribe(request):
    """Register or update a web push subscription for the authenticated user."""
    try:
        data = json.loads(request.body)
        endpoint = data['endpoint']
        p256dh = data['keys']['p256dh']
        auth = data['keys']['auth']
    except (KeyError, json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid subscription data'}, status=400)

    WebPushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user,
            'p256dh': p256dh,
            'auth': auth,
        },
    )
    return JsonResponse({'status': 'subscribed'})


@login_required
@require_POST
def push_unsubscribe(request):
    """Remove a web push subscription by endpoint."""
    try:
        data = json.loads(request.body)
        endpoint = data['endpoint']
    except (KeyError, json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid request data'}, status=400)

    WebPushSubscription.objects.filter(
        user=request.user,
        endpoint=endpoint,
    ).delete()
    return JsonResponse({'status': 'unsubscribed'})


# ---------------------------------------------------------------------------
# Email relay messaging
# ---------------------------------------------------------------------------

@active_required
def message_send(request, booking_pk):
    """
    Send a relay message for a booking.

    The authenticated user must be either the borrower or the spot owner.
    The message is relayed by email; neither party's real address is exposed.
    """
    booking = get_object_or_404(Booking.scoped, pk=booking_pk)

    # Access control: only the borrower or the spot owner may message
    user = request.user
    borrower = booking.borrower
    owner = booking.spot.owner

    if user != borrower and user != owner:
        raise PermissionDenied

    # Booking must be in a messageable state
    if booking.status not in ('confirmed', 'active'):
        return render(request, 'notifications/message_error.html', {
            'error': 'Messaging is only available for confirmed or active bookings.',
        }, status=400)

    # Token must not have expired (uses booking end time)
    if booking.time_range.upper <= now():
        return render(request, 'notifications/message_error.html', {
            'error': 'This booking has ended; messaging is no longer available.',
        }, status=400)

    # Determine recipient
    to_user = owner if user == borrower else borrower

    if request.method == 'POST':
        form = MessageForm(request.POST)
        if form.is_valid():
            if not can_send_relay(user, booking):
                return render(request, 'notifications/message_error.html', {
                    'error': 'You have reached the maximum number of messages for this booking.',
                }, status=400)
            send_relay_message(
                from_user=user,
                to_user=to_user,
                booking=booking,
                body=form.cleaned_data['body'],
            )
            return render(request, 'notifications/message_sent.html', {
                'booking': booking,
            })
    else:
        form = MessageForm()

    return render(request, 'notifications/message_send.html', {
        'form': form,
        'booking': booking,
        'to_user_display': to_user.display_name if to_user else 'the other party',
    })


def message_reply(request, token):
    """
    Reply to a relay message via its reply token.

    Token is validated (exists, not expired).  Real email addresses are not
    exposed to either party.
    """
    original = get_object_or_404(RelayMessage, reply_token=token)

    if original.token_expires_at <= now():
        return render(request, 'notifications/message_error.html', {
            'error': 'This reply link has expired.',
        }, status=404)

    # Reply goes back to the original sender
    from_user = request.user if request.user.is_authenticated else None
    reply_from = original.to_user
    reply_to = original.from_user

    if request.method == 'POST':
        # Require authentication for POST
        if not request.user.is_authenticated:
            return redirect('login')
        if request.user != reply_from:
            raise PermissionDenied

        form = MessageForm(request.POST)
        if form.is_valid():
            if not can_send_relay(request.user, original.booking):
                return render(request, 'notifications/message_error.html', {
                    'error': 'You have reached the maximum number of messages for this booking.',
                }, status=400)
            send_relay_message(
                from_user=reply_from,
                to_user=reply_to,
                booking=original.booking,
                body=form.cleaned_data['body'],
                reply_to=original,
            )
            return render(request, 'notifications/message_sent.html', {
                'booking': original.booking,
            })
    else:
        form = MessageForm()

    return render(request, 'notifications/message_reply.html', {
        'form': form,
        'original': original,
        'booking': original.booking,
    })
