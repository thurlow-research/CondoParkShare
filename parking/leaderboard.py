"""
parking.leaderboard — Top-owners leaderboard by elapsed listed hours.

The leaderboard UI is deferred (CONFIRMED-REQUIREMENTS.md §Deferred), but
the data query is implemented here so it is available when needed.

See TECHNICAL-DESIGN.md §7 for the tier_metric_window_days config field.
"""

from datetime import timedelta

from django.db.models import DurationField, ExpressionWrapper, Q, Sum
from django.db.models.functions import Greatest, Lower, Upper
from django.utils.timezone import now


def get_leaderboard(organization, limit=20):
    """Return the top *limit* active owners ranked by elapsed listed hours.

    Uses the same elapsed-listed-hours basis as
    :func:`parking.horizon.get_earned_horizon_hours`, as required by
    SPEC-1 §78 ("Leaderboard: same elapsed-listed-hours basis"). A window
    counts when:
    - Its upper bound has passed (fully elapsed: ``endswith <= now``)
    - Its upper bound is after the rolling window start
      (``endswith > window_start``), so windows that began before the
      window but elapsed within it are *clamped* to the window start
      rather than dropped — matching the authoritative horizon metric.
    - It belongs to an active ParkingSpot.

    The counted duration is ``Upper - max(Lower, window_start)``: the lower
    bound is clamped to ``window_start`` so only the in-window portion of a
    straddling window is credited. (The upper bound needs no clamping — the
    ``endswith <= now`` filter already guarantees it has passed.)

    Only active users in the organisation are considered.

    Returns a QuerySet of User instances annotated with ``elapsed_hours``
    (a timedelta or None), ordered descending.
    """
    from accounts.models import User

    now_dt = now()
    window_start = now_dt - timedelta(days=organization.tier_metric_window_days)

    return (
        User.objects.filter(organization=organization, status="active")
        .annotate(
            elapsed_hours=Sum(
                ExpressionWrapper(
                    Upper("owned_spots__availability_windows__time_range")
                    - Greatest(
                        Lower("owned_spots__availability_windows__time_range"),
                        window_start,
                    ),
                    output_field=DurationField(),
                ),
                filter=(
                    Q(
                        owned_spots__availability_windows__time_range__endswith__lte=now_dt
                    )
                    & Q(
                        owned_spots__availability_windows__time_range__endswith__gt=window_start
                    )
                    & Q(owned_spots__status="active")
                    # Explicit tenant scope (defense-in-depth): don't rely solely
                    # on the spot-owner-same-org invariant for cross-org safety.
                    & Q(owned_spots__organization=organization)
                ),
            )
        )
        .order_by("-elapsed_hours")[:limit]
    )
