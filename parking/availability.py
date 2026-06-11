"""
parking.availability — Availability computation functions.

BUFFER_HOURS is fixed at 1 for the pilot (see ADR-001 and CONFIRMED-REQUIREMENTS §B4).
The config field booking_buffer_hours exists on Organization but is not consulted
here — it is reserved for a future configurable implementation.
"""

from datetime import timedelta

from django.db.models import Exists, OuterRef, Q
from psycopg2.extras import DateTimeTZRange

BUFFER_HOURS = 1  # fixed for pilot — see ADR-001


def is_spot_available(spot, requested_start, requested_end):
    """
    Returns True if the spot has no buffer conflicts for the requested window.

    Two conditions must both be met:
    1. An AvailabilityWindow covers the full requested range.
    2. No active booking (tentative/confirmed/active) overlaps the buffered range.
    """
    buffer = timedelta(hours=BUFFER_HOURS)
    buffered = DateTimeTZRange(requested_start - buffer, requested_end + buffer)

    # Check 1: an AvailabilityWindow covers the full requested range
    covers = spot.availability_windows.filter(
        time_range__contains=DateTimeTZRange(requested_start, requested_end)
    ).exists()
    if not covers:
        return False

    # Check 2: no active booking overlaps the buffered range
    conflicts = spot.bookings.filter(
        Q(status__in=['tentative', 'confirmed', 'active']),
        time_range__overlap=buffered,
    ).exists()
    return not conflicts


def get_available_slots(organization, requested_start, requested_end):
    """
    Returns a queryset of ParkingSpot instances that are available for
    the requested window, scoped to the given organization.
    """
    from parking.models import Booking, ParkingSpot

    buffer = timedelta(hours=BUFFER_HOURS)
    buffered = DateTimeTZRange(requested_start - buffer, requested_end + buffer)
    req_range = DateTimeTZRange(requested_start, requested_end)

    conflict = Booking.objects.filter(
        spot=OuterRef('pk'),
        time_range__overlap=buffered,
        status__in=['tentative', 'confirmed', 'active'],
    )

    return (
        ParkingSpot.objects
        .filter(
            organization=organization,
            status='active',
            availability_windows__time_range__contains=req_range,
        )
        .exclude(Exists(conflict))
        .distinct()
    )
