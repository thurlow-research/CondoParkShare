"""
parking.horizon — Earned-horizon metric.

Each resident's booking horizon is a function of how many hours their spot
has been listed (minus owner-cancel penalties).  See TECHNICAL-DESIGN.md §7.
"""

from datetime import timedelta
from math import floor

from django.db.models import DurationField, ExpressionWrapper, Sum
from django.db.models.functions import Greatest, Least, Lower, Upper
from django.utils.timezone import now


def get_earned_horizon_hours(user):
    """Return the number of hours into the future a resident may book.

    Calculation:
      1. If the organisation is within its launch-grace window, return the
         grace-period horizon (flat value).
      2. Otherwise, count elapsed listed hours within the rolling metric window,
         subtract owner-cancel penalty_hours, compute the earned add-on, and
         cap at the configured maximum.
    """
    from parking.models import AvailabilityWindow, Booking

    org = user.organization
    now_dt = now()

    # Cold-start grace: give early adopters a flat horizon until the building
    # has been live long enough to have meaningful listing history.
    if org.launched_at:
        days_live = (now_dt - org.launched_at).days
        if days_live < org.launch_grace_days:
            return org.launch_grace_horizon_days * 24

    window_start = now_dt - timedelta(days=org.tier_metric_window_days)

    # Sum elapsed hours from availability windows that overlap the rolling window,
    # clamping spanning windows to the window boundary rather than dropping them.
    # Windows entirely in the future (upper <= now_dt is False) or entirely before
    # window_start (upper <= window_start) are excluded via the two endswith filters.
    elapsed = (
        AvailabilityWindow.objects.filter(
            spot__owner=user,
            spot__organization=org,
            spot__status="active",
            time_range__endswith__lte=now_dt,  # upper bound has passed (fully elapsed)
            time_range__endswith__gt=window_start,  # window hasn't ended before rolling window started
        )
        .annotate(
            clamped_start=Greatest(Lower("time_range"), window_start),
            clamped_end=Least(Upper("time_range"), now_dt),
            hours=ExpressionWrapper(
                Least(Upper("time_range"), now_dt)
                - Greatest(Lower("time_range"), window_start),
                output_field=DurationField(),
            ),
        )
        .aggregate(total=Sum("hours"))["total"]
    )
    elapsed_hours = elapsed.total_seconds() / 3600 if elapsed else 0

    # Penalties: owner-cancelled bookings whose scheduled start falls within the window.
    penalties = (
        Booking.objects.filter(
            spot__owner=user,
            spot__organization=org,
            status="cancelled_owner",
            time_range__startswith__gte=window_start,
        ).aggregate(total=Sum("penalty_hours"))["total"]
        or 0
    )

    net_hours = max(0, elapsed_hours - penalties)
    baseline = org.booking_horizon_baseline_days * 24
    earned = floor(net_hours / org.listing_to_horizon_ratio)
    maximum = org.booking_horizon_max_days * 24

    return min(baseline + earned, maximum)


def check_horizon_gate(borrower, requested_start):
    """Gate 1: the requested start must be within the resident's earned horizon.

    Returns True if the start is within the allowed horizon, False otherwise.
    """
    horizon_hours = get_earned_horizon_hours(borrower)
    max_start = now() + timedelta(hours=horizon_hours)
    return requested_start <= max_start
