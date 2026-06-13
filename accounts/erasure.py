"""
accounts.erasure — Right-to-erasure handler for GDPR / privacy compliance.

Call ``erase_user_pii(user, erased_by)`` to scrub all personally-identifiable
data belonging to *user* from the database in a single atomic transaction.

The function is intentionally idempotent: calling it twice produces the same
result as calling it once.

What is erased
--------------
User record
  - email           → ``erased-{pk}@redacted.invalid``  (unique stub; .invalid
                       is RFC-2606 reserved and will never receive mail)
  - display_name    → ``[Erased User]``
  - phone           → None  (EncryptedCharField)
  - recovery_codes  → []
  - status          → ``blocked``  (prevents any future login)
  - password        → unusable (set_unusable_password)

Booking records (borrower)
  - Active/confirmed/tentative bookings → cancelled_admin before anonymisation
  - borrower        → None
  - is_anonymized   → True

RelayMessage records (from_user or to_user)
  - body            → ``[erased]``  (preserve audit trail of message count)

TOTPDevice records (django-otp)
  - deleted in full (secret lives in TOTPDevice, not on User)

WebPushSubscription records
  - deleted in full

EmailOTP records
  - deleted in full

Audit trail
-----------
An ``AdminAuditLog`` record with action='pii_erasure' is written inside the
transaction so the log entry and PII scrub are atomically paired.

Usage::

    from accounts.erasure import erase_user_pii
    erase_user_pii(user, erased_by=request.user)
"""

from django.db import transaction


def erase_user_pii(user, erased_by):
    """
    Scrub all PII for *user* in a single atomic transaction.

    Parameters
    ----------
    user : accounts.User
        The user whose data should be erased.
    erased_by : accounts.User
        The operator or admin performing the erasure (logged to AdminAuditLog).

    Returns
    -------
    None
    """
    with transaction.atomic():
        # Import inside transaction to avoid circular imports at module load time.
        from django_otp.plugins.otp_totp.models import TOTPDevice

        from accounts.models import AdminAuditLog
        from notifications.models import RelayMessage
        from parking.models import Booking, ParkingSpot

        user_pk = user.pk  # capture BEFORE any modification

        # --- Cancel active bookings BEFORE anonymising the borrower FK ----------
        active_pks = list(
            Booking.objects.filter(
                borrower=user,
                status__in=["tentative", "confirmed", "active"],
            ).values_list("pk", flat=True)
        )
        if active_pks:
            Booking.objects.filter(pk__in=active_pks).update(
                status="cancelled_admin",
                cancel_reason="Account erased",
            )

        # --- Anonymise all bookings — remove borrower identity, preserve records -
        Booking.objects.filter(borrower=user).update(
            borrower=None,
            is_anonymized=True,
        )

        # --- Null spot ownership — field is null=True, PROTECT-free on erasure ---
        ParkingSpot.objects.filter(owner=user).update(owner=None)

        # --- Scrub relay message bodies and null FK columns to remove identity ---
        RelayMessage.objects.filter(from_user=user).update(
            body="[erased]", from_user=None
        )
        RelayMessage.objects.filter(to_user=user).update(body="[erased]", to_user=None)

        # --- Delete TOTP devices (secret lives in TOTPDevice, not on User) -------
        TOTPDevice.objects.filter(user=user).delete()

        # --- Delete push subscriptions and email OTPs ----------------------------
        user.push_subscriptions.all().delete()
        user.email_otps.all().delete()

        # --- Scrub User PII fields ------------------------------------------------
        user.email = "erased-" + str(user_pk) + "@redacted.invalid"
        user.display_name = "[Erased User]"
        user.phone = None
        user.recovery_codes = []
        user.status = "blocked"
        # Consent withdrawal under GDPR Art. 7(3) — clear marketing opt-in.
        user.marketing_email_opted_in = False
        user.set_unusable_password()
        user.save()

        # --- Log erasure using captured pk (user.pk still valid, not deleted) ----
        AdminAuditLog.objects.create(
            organization=user.organization,
            actor=erased_by,
            action="pii_erasure",
            target_type="user",
            target_id=user_pk,
        )
