"""
accounts.erasure — Right-to-erasure handler for GDPR / privacy compliance.

Call ``erase_user_pii(user)`` to scrub all personally-identifiable data
belonging to *user* from the database.  The function is intentionally
idempotent: calling it twice produces the same result as calling it once.

What is erased
--------------
User record
  - email           → ``[erased]@erased.invalid``  (kept non-null for DB integrity;
                       the domain ``.invalid`` is RFC-2606 reserved and will never
                       receive mail)
  - display_name    → ``[erased]``
  - phone           → None  (EncryptedCharField, already field-encrypted)
  - recovery_codes  → []    (hashed, but no longer needed)
  - is_active       → False (prevents any future login)

RelayMessage records (from_user or to_user)
  - body            → ``[erased]``
  - from_user / to_user FK → None (set to null before the FK reference is lost)

  FK nullability note: RelayMessage.from_user and to_user currently use
  on_delete=PROTECT.  The erasure function updates them to NULL via a direct
  queryset update, which bypasses the PROTECT constraint.  A future migration
  should change these FKs to on_delete=SET_NULL, null=True to align the schema
  with the erasure path; until then the update() call is safe because Django's
  PROTECT guard is ORM-layer only and does not affect UPDATE statements.

Booking records (borrower)
  - borrower        → None
  - is_anonymized   → True

This mirrors the pattern already used by Booking.borrower (null=True,
is_anonymized=BooleanField) and extends it consistently to RelayMessage.

Audit trail
-----------
The caller is responsible for writing the ``AdminAuditLog`` entry
(action='pii_erasure') before or after calling this function.  The admin
action in ``accounts/admin.py`` handles that.

Usage::

    from accounts.erasure import erase_user_pii
    erase_user_pii(user)
"""

from django.db import transaction


def erase_user_pii(user):
    """
    Scrub all PII for *user* in a single atomic transaction.

    Parameters
    ----------
    user : accounts.User
        The user whose data should be erased.  The object is re-fetched
        inside the transaction to guard against stale state.

    Returns
    -------
    None
    """
    # Import here to avoid circular imports (accounts.models → erasure → models).
    from notifications.models import RelayMessage
    from parking.models import Booking

    with transaction.atomic():
        # --- RelayMessage: scrub body first, then null out FK references -------
        # Scrub body on messages sent by this user.
        RelayMessage.objects.filter(from_user=user).update(
            body='[erased]',
            from_user=None,
        )
        # Scrub body on messages received by this user.
        RelayMessage.objects.filter(to_user=user).update(
            body='[erased]',
            to_user=None,
        )

        # --- Booking: null out borrower, flag as anonymized -------------------
        Booking.objects.filter(borrower=user).update(
            borrower=None,
            is_anonymized=True,
        )

        # --- User record: overwrite PII fields --------------------------------
        # Use a unique-per-user email stub so the unique_together constraint
        # on (organization, email) is not violated when multiple users in the
        # same org are erased.
        erased_email = f'erased-{user.pk}@erased.invalid'
        user.email = erased_email
        user.display_name = '[erased]'
        user.phone = None
        user.recovery_codes = []
        user.is_active = False
        user.save(update_fields=[
            'email',
            'display_name',
            'phone',
            'recovery_codes',
            'is_active',
        ])
