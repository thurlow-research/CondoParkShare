"""
parking.models — Organization, ParkingSpot, AvailabilityWindow, Booking.

These are the core domain models for the CondoParkShare parking system.
Organization is defined here (not in accounts) because User has a FK to it
and Django resolves models in dependency order.
"""

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeOperators
from django.contrib.postgres.indexes import GistIndex
from django.db import models

from parkshare.managers import OrganizationScopedManager


class Organization(models.Model):
    name = models.CharField(max_length=255)
    hostname = models.CharField(max_length=255, unique=True)
    timezone = models.CharField(max_length=64, default="America/Los_Angeles")
    registration_mode = models.CharField(
        max_length=20,
        choices=[
            ("invite_only", "Invite Only"),
            ("approve", "Approve"),
            ("both", "Both"),
        ],
        default="invite_only",
    )
    unit_count = models.PositiveIntegerField(null=True, blank=True)
    payer_model = models.CharField(max_length=20, default="free_forever")
    support_email = models.EmailField()
    launched_at = models.DateTimeField(null=True, blank=True)

    # Booking config
    booking_buffer_hours = models.PositiveIntegerField(default=1)  # fixed at 1; reserved for future
    max_concurrent_bookings = models.PositiveIntegerField(default=1)
    max_booking_hours = models.PositiveIntegerField(default=168)

    # Horizon config
    booking_horizon_baseline_days = models.PositiveIntegerField(default=3)
    booking_horizon_max_days = models.PositiveIntegerField(default=30)
    listing_to_horizon_ratio = models.PositiveIntegerField(default=10)
    tier_metric_window_days = models.PositiveIntegerField(default=180)
    launch_grace_days = models.PositiveIntegerField(default=14)
    launch_grace_horizon_days = models.PositiveIntegerField(default=14)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Unscoped only — TenantMiddleware and operator console resolve org before
    # any scoped manager is needed.
    objects = models.Manager()

    def __str__(self):
        return self.name


class ParkingSpot(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name="spots")
    owner = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="owned_spots",
        null=True,
        blank=True,
    )
    spot_number = models.CharField(max_length=50)  # e.g. "P3076", string
    notes = models.TextField(blank=True)  # admin-only annotation

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("active", "Active"),
        ("inactive", "Inactive"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = models.Manager()
    scoped = OrganizationScopedManager()

    class Meta:
        unique_together = [("organization", "spot_number")]

    def __str__(self):
        return f"{self.spot_number} ({self.organization})"


class AvailabilityWindow(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    spot = models.ForeignKey(ParkingSpot, on_delete=models.CASCADE, related_name="availability_windows")
    time_range = DateTimeRangeField()  # tstzrange; continuous window; stored UTC

    created_at = models.DateTimeField(auto_now_add=True)

    objects = models.Manager()
    scoped = OrganizationScopedManager()

    class Meta:
        indexes = [GistIndex(fields=["time_range"])]

    def __str__(self):
        return f"AvailabilityWindow spot={self.spot_id} {self.time_range}"


class Booking(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.PROTECT)
    spot = models.ForeignKey(ParkingSpot, on_delete=models.PROTECT, related_name="bookings")
    borrower = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="bookings",
        null=True,
        blank=True,
        # null=True supports anonymisation on right-to-erasure
    )
    time_range = DateTimeRangeField()  # tstzrange; hour-aligned; stored UTC

    STATUS_CHOICES = [
        ("tentative", "Tentative"),  # held, 5-min expiry
        ("confirmed", "Confirmed"),
        ("active", "Active"),  # start time has passed
        ("completed", "Completed"),
        ("cancelled_borrower", "Cancelled by Borrower"),
        ("cancelled_owner", "Cancelled by Owner"),
        ("cancelled_admin", "Cancelled by Admin"),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="tentative")
    tentative_expires_at = models.DateTimeField(null=True, blank=True)  # now() + 5 min
    cancel_reason = models.TextField(blank=True)
    penalty_hours = models.PositiveIntegerField(default=0)  # set on owner-cancel
    is_anonymized = models.BooleanField(default=False)  # set on right-to-erasure

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = models.Manager()
    scoped = OrganizationScopedManager()

    class Meta:
        indexes = [GistIndex(fields=["time_range"])]
        constraints = [
            ExclusionConstraint(
                name="booking_no_overlap",
                expressions=[
                    ("spot", RangeOperators.EQUAL),
                    ("time_range", RangeOperators.OVERLAPS),
                ],
                condition=models.Q(status__in=["tentative", "confirmed", "active"]),
            )
        ]

    def __str__(self):
        return f"Booking {self.pk} spot={self.spot_id} {self.time_range} [{self.status}]"
