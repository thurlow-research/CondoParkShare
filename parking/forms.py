"""
parking.forms — Forms for the parking app.
"""

from django import forms
from django.utils.timezone import now

from parking.models import ParkingSpot


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
