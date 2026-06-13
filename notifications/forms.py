"""
notifications.forms — Forms for the relay messaging system.
"""

from django import forms


class MessageForm(forms.Form):
    body = forms.CharField(
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 4}),
        label="Message",
    )
