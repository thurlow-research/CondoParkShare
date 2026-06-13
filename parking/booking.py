"""
parking.booking — Core booking logic.

Functions:
  assign_spot       — tentative assignment with owner-rotation algorithm
  confirm_booking   — promote tentative → confirmed
  cancel_booking    — cancel by borrower or owner
  release_booking   — early release (shorten booking end, hour-aligned)

See TECHNICAL-DESIGN.md §6, §8 and CONFIRMED-REQUIREMENTS.md §B.
"""

BUFFER_HOURS = 1  # fixed for pilot; see booking_buffer_hours on Organization


def assign_spot(organization, borrower, requested_start, requested_end):
    """Tentatively assign a spot using the owner-rotation algorithm.

    Finds active spots in *organization* whose availability windows cover
    [requested_start, requested_end] and which have no conflicting booking
    (including the 1-hour buffer on each side).

    Ties are broken by ``owner.last_booking_at`` ascending (nulls first),
    so the owner whose spot was last booked longest ago gets priority.

    Returns a Booking with status='tentative', or None if no spot is
    available.

    Implementation notes:
    - Uses Exists(OuterRef) rather than exclude(bookings__overlap=...) to
      avoid the multi-valued FK fan-out bug.
    - Uses select_for_update(skip_locked=True) so concurrent requests do not
      double-assign the same spot.
    """
    from datetime import timedelta

    from django.db import transaction
    from django.db.models import Exists, F, OuterRef
    from django.utils.timezone import now
    from psycopg2.extras import DateTimeTZRange

    from parking.models import AvailabilityWindow, Booking, ParkingSpot

    buffer = timedelta(hours=BUFFER_HOURS)
    buffered = DateTimeTZRange(requested_start - buffer, requested_end + buffer)
    req_range = DateTimeTZRange(requested_start, requested_end)

    # Subquery: does this spot have any conflicting active booking?
    conflict = Booking.objects.filter(
        spot=OuterRef("pk"),
        time_range__overlap=buffered,
        status__in=["tentative", "confirmed", "active"],
    )

    # Subquery: does this spot have an availability window that covers the
    # full requested range?  Using Exists avoids the JOIN fan-out that would
    # require DISTINCT, which is incompatible with SELECT FOR UPDATE.
    covers = AvailabilityWindow.objects.filter(
        spot=OuterRef("pk"),
        time_range__contains=req_range,
    )

    with transaction.atomic():
        # Gate 2 — One active booking per borrower (checked inside the
        # transaction so it is atomic with Booking.objects.create, preventing
        # a race condition where two concurrent requests for the same user
        # both pass this check before either creates a tentative booking).
        already_active = (
            Booking.objects.filter(
                borrower=borrower,
                status__in=["tentative", "confirmed", "active"],
            )
            .select_for_update()
            .exists()
        )
        if already_active:
            return "already_active"

        candidates = (
            ParkingSpot.objects.select_for_update(skip_locked=True, of=("self",))
            .filter(
                organization=organization,
                status="active",
            )
            .filter(Exists(covers))
            .exclude(Exists(conflict))
            .select_related("owner")
            .order_by(F("owner__last_booking_at").asc(nulls_first=True))
        )

        spot = candidates.first()
        if not spot:
            return None

        return Booking.objects.create(
            organization=organization,
            spot=spot,
            borrower=borrower,
            time_range=req_range,
            status="tentative",
            tentative_expires_at=now() + timedelta(minutes=5),
        )


def confirm_booking(booking, borrower):
    """Confirm a tentative booking.

    Raises ValueError if the booking is not tentative or if the 5-minute
    tentative hold has expired.  On expiry the booking is also marked
    cancelled_admin so a subsequent cleanup run does not re-process it.
    """
    from django.utils.timezone import now

    if booking.status != "tentative":
        raise ValueError("Booking is not tentative")

    if booking.tentative_expires_at and booking.tentative_expires_at < now():
        booking.status = "cancelled_admin"
        booking.save()
        raise ValueError("Tentative hold expired")

    booking.status = "confirmed"
    booking.save()
    return booking


def cancel_booking(booking, cancelled_by):
    """Cancel a booking by its borrower or the spot owner.

    - If the owner cancels: status → cancelled_owner, penalty_hours set to
      the full booking duration in hours.
    - If the borrower cancels: status → cancelled_borrower.

    Raises PermissionError if cancelled_by is neither borrower nor owner.
    """
    from notifications.dispatch import notify

    is_owner = cancelled_by == booking.spot.owner
    is_borrower = cancelled_by == booking.borrower

    if not (is_owner or is_borrower):
        raise PermissionError("Not authorized to cancel this booking")

    if booking.status in (
        "completed",
        "cancelled_borrower",
        "cancelled_owner",
        "cancelled_admin",
    ):
        raise ValueError("Booking already finalised")

    if is_owner:
        duration = int(
            (booking.time_range.upper - booking.time_range.lower).total_seconds() / 3600
        )
        booking.status = "cancelled_owner"
        booking.penalty_hours = duration
    else:
        booking.status = "cancelled_borrower"

    booking.save()
    event = (
        "booking_cancelled_by_owner" if is_owner else "booking_cancelled_by_borrower"
    )
    notify(event, booking)
    return booking


def release_booking(booking, borrower, release_up_to):
    """Shorten a booking end time (early release).

    ``release_up_to`` must be:
    - exactly on the hour (minute == 0, second == 0)
    - strictly in the future
    - strictly before the current booking end

    The released hours return to inventory immediately.

    Raises PermissionError if *borrower* is not the booking's borrower.
    Raises ValueError for invalid booking state or invalid release_up_to.
    """
    from django.utils.timezone import now
    from psycopg2.extras import DateTimeTZRange

    from notifications.dispatch import notify

    if booking.borrower != borrower:
        raise PermissionError("Not your booking")

    if booking.status not in ("confirmed", "active"):
        raise ValueError("Cannot release a booking in this state")

    now_dt = now()

    if release_up_to.minute != 0 or release_up_to.second != 0:
        raise ValueError("Release time must be on the hour")
    if release_up_to <= now_dt:
        raise ValueError("Release time must be in the future")
    if release_up_to >= booking.time_range.upper:
        raise ValueError("Release time must be before booking end")

    booking.time_range = DateTimeTZRange(booking.time_range.lower, release_up_to)
    booking.save()
    notify("early_release_confirmed", booking)
    return booking
