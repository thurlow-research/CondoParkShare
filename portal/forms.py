"""
portal.forms — HOA portal forms.
"""

from django import forms


class InviteCreateForm(forms.Form):
    """
    Form for HOA admins to create a new invite link.

    Fields
    ------
    unit_number
        Optional — pre-fills the unit number field on the registration form
        so residents cannot accidentally register under the wrong unit.
    expires_at
        Optional — if set, the invite will reject redemptions after this
        datetime.
    max_uses
        How many times the invite code may be used.  Defaults to 1 (single
        use).  Minimum value is 1.
    """

    unit_number = forms.CharField(
        label="Unit number",
        max_length=50,
        required=False,
        help_text="Optional — pre-fills the unit number at registration.",
    )
    expires_at = forms.DateTimeField(
        label="Expires at",
        required=False,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
        help_text="Optional — leave blank for a non-expiring invite.",
    )
    max_uses = forms.IntegerField(
        label="Maximum uses",
        min_value=1,
        initial=1,
        help_text="Number of times this invite code can be used.",
    )
