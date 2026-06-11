"""
parking.forms — Forms for the parking app.
"""

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
        label='Start',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        input_formats=['%Y-%m-%dT%H:%M'],
    )
    end = forms.DateTimeField(
        label='End',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        input_formats=['%Y-%m-%dT%H:%M'],
    )

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._org = org

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start')
        end = cleaned.get('end')

        if not start or not end:
            return cleaned

        now_dt = now()

        if start <= now_dt:
            raise forms.ValidationError('Start time must be in the future.')
        if start.minute != 0 or start.second != 0:
            raise forms.ValidationError('Start time must be on the hour.')
        if end.minute != 0 or end.second != 0:
            raise forms.ValidationError('End time must be on the hour.')
        if end <= start:
            raise forms.ValidationError('End must be after start.')

        duration_hours = int((end - start).total_seconds() / 3600)
        if duration_hours < 1:
            raise forms.ValidationError('Minimum booking is 1 hour.')

        if self._org and duration_hours > self._org.max_booking_hours:
            raise forms.ValidationError(
                f'Maximum booking is {self._org.max_booking_hours} hours.'
            )

        return cleaned


class CancellationReasonForm(forms.Form):
    """Optional reason form used when an owner or admin cancels a booking."""

    reason = forms.CharField(
        label='Reason (optional)',
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={'rows': 3}),
    )


class EarlyReleaseForm(forms.Form):
    """Form for a borrower to release hours back to inventory early.

    ``release_to`` must be an hour-aligned datetime in the future and before
    the booking's current end.  The view is responsible for additional
    context-specific validation (must be < booking end, > now).
    """

    release_to = forms.DateTimeField(
        label='Release spot at',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        input_formats=['%Y-%m-%dT%H:%M'],
    )

    def clean_release_to(self):
        value = self.cleaned_data['release_to']
        if value.minute != 0 or value.second != 0:
            raise forms.ValidationError('Release time must be on the hour.')
        if value <= now():
            raise forms.ValidationError('Release time must be in the future.')
        return value


class AvailabilityWindowForm(forms.Form):
    """
    Form for an owner to add an availability window to one of their spots.

    The spot queryset is restricted to active spots owned by the requesting
    user; callers must pass owner= when constructing the form.
    """

    spot = forms.ModelChoiceField(
        queryset=ParkingSpot.objects.none(),
        label='Parking spot',
    )
    start = forms.DateTimeField(
        label='Start',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )
    end = forms.DateTimeField(
        label='End',
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        if owner is not None:
            self.fields['spot'].queryset = ParkingSpot.objects.filter(
                owner=owner, status='active'
            )

    def clean(self):
        cleaned = super().clean()
        start = cleaned.get('start')
        end = cleaned.get('end')

        if not start or not end:
            return cleaned

        now_dt = now()

        if start <= now_dt:
            raise forms.ValidationError('Start time must be in the future.')

        if start.minute != 0 or start.second != 0:
            raise forms.ValidationError('Start time must be on the hour.')

        if end.minute != 0 or end.second != 0:
            raise forms.ValidationError('End time must be on the hour.')

        if end <= start:
            raise forms.ValidationError('End time must be after start time.')

        return cleaned


class AvailabilityWindowRemoveForm(forms.Form):
    """
    Confirmation form for removing an availability window.

    No fields — the POST itself is the confirmation.
    """
