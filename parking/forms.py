"""
parking.forms — Forms for the parking app.
"""

from datetime import timedelta

from django import forms
from django.utils.timezone import now

from parking.models import ParkingSpot

# ---------------------------------------------------------------------------
# Booking forms
# ---------------------------------------------------------------------------


class BookingRequestForm(forms.Form):
    """Form for a resident to request a parking spot booking.

    Pass ``org=request.organization`` when constructing so the max-duration
    validation can read ``org.max_booking_hours``.
    """

    start = forms.DateTimeField(
        label="Start",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
    )
    end = forms.DateTimeField(
        label="End",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
    )

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._org = org

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start")
        end = cleaned.get("end")

        if not start or not end:
            return cleaned

        now_dt = now()

        if start <= now_dt:
            raise forms.ValidationError("Start time must be in the future.")
        if start.minute != 0 or start.second != 0:
            raise forms.ValidationError("Start time must be on the hour.")
        if end.minute != 0 or end.second != 0:
            raise forms.ValidationError("End time must be on the hour.")
        if end <= start:
            raise forms.ValidationError("End must be after start.")

        duration_hours = int((end - start).total_seconds() / 3600)
        if duration_hours < 1:
            raise forms.ValidationError("Minimum booking is 1 hour.")

        if self._org and duration_hours > self._org.max_booking_hours:
            raise forms.ValidationError(f"Maximum booking is {self._org.max_booking_hours} hours.")

        return cleaned


class CancellationReasonForm(forms.Form):
    """Optional reason form used when an owner or admin cancels a booking."""

    reason = forms.CharField(
        label="Reason (optional)",
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class EarlyReleaseForm(forms.Form):
    """Form for a borrower to release hours back to inventory early.

    Pass ``booking=<Booking instance>`` when constructing so the form can
    validate that ``release_to`` is strictly before the booking's end.
    """

    release_to = forms.DateTimeField(
        label="Release spot at",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        input_formats=["%Y-%m-%dT%H:%M"],
        help_text="Release the spot back from this time onwards (must be on the hour).",
    )

    def __init__(self, *args, booking=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._booking = booking

    def clean_release_to(self):
        value = self.cleaned_data.get("release_to")
        if not value:
            return value
        if value.minute != 0 or value.second != 0:
            raise forms.ValidationError("Release time must be on the hour.")
        if value <= now():
            raise forms.ValidationError("Release time must be in the future.")
        if self._booking and value >= self._booking.time_range.upper:
            raise forms.ValidationError("Release time must be before the booking end.")
        if self._booking and value < self._booking.time_range.lower + timedelta(hours=1):
            raise forms.ValidationError("At least 1 hour must remain after the release time.")
        return value


class AvailabilityWindowForm(forms.Form):
    """
    Form for an owner to add an availability window to one of their spots.

    The spot queryset is restricted to active spots owned by the requesting
    user; callers must pass owner= when constructing the form.
    """

    spot = forms.ModelChoiceField(
        queryset=ParkingSpot.objects.none(),
        label="Parking spot",
    )
    start = forms.DateTimeField(
        label="Start",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    end = forms.DateTimeField(
        label="End",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )

    def __init__(self, *args, owner=None, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        if owner is not None:
            # Use the unscoped manager and apply the org constraint explicitly.
            # Replaces ParkingSpot.scoped which read org from TenantMiddleware
            # thread-locals, silently returning qs.none() in non-request contexts
            # (tests, management commands).
            # The org constraint is always enforced: explicitly via the passed org=
            # argument (production view passes org=request.organization), or derived
            # from owner.organization (tests / management commands that omit org=).
            # If the tenant context cannot be determined at all, the queryset is
            # emptied (fail-closed) rather than falling back to an owner-only,
            # cross-tenant-permissive filter.
            qs = ParkingSpot.objects.filter(owner=owner, status="active")
            org = org or getattr(owner, "organization", None)
            if org is not None:
                qs = qs.filter(organization=org)
            else:
                # Fail closed: if the tenant context cannot be determined (no org
                # passed and owner has no organization), expose no spots rather than
                # fall back to an owner-only, cross-tenant-permissive queryset.
                qs = qs.none()
            self.fields["spot"].queryset = qs

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get("start")
        end = cleaned.get("end")

        if not start or not end:
            return cleaned

        now_dt = now()

        if start <= now_dt:
            raise forms.ValidationError("Start time must be in the future.")

        if start.minute != 0 or start.second != 0:
            raise forms.ValidationError("Start time must be on the hour.")

        if end.minute != 0 or end.second != 0:
            raise forms.ValidationError("End time must be on the hour.")

        if end <= start:
            raise forms.ValidationError("End time must be after start time.")

        return cleaned


class AvailabilityWindowRemoveForm(forms.Form):
    """
    Confirmation form for removing an availability window.

    No fields — the POST itself is the confirmation.
    """
