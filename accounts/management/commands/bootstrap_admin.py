"""
accounts management command: bootstrap_admin

Idempotent, env-driven first-run bootstrap. Brings up a fresh deployment's
first operator account without manual `shell` / `createsuperuser` / TOTP
enrollment steps.

What it does (on FIRST run only — see idempotency contract below):
  1. get-or-create the Organization by hostname.
  2. create the superuser admin (is_superuser, is_staff, is_hoa_admin,
     status='active').
  3. create a *confirmed* EncryptedTOTPDevice so the operator can log into the
     operator console immediately after scanning the QR / otpauth URI.

Idempotency contract (re-runs MUST be safe and non-mutating):
  - Organization: get_or_create(hostname=...). If an org already exists with a
    DIFFERENT name, the existing org is used unchanged and a warning is printed
    (we never overwrite an existing org's name).
  - User: looked up by (organization, email). If the admin already exists this
    command is a complete NO-OP for that user — it does NOT reset the password,
    change the status, rotate recovery codes, or touch the TOTP device. The
    user is treated as fully bootstrapped.
  - TOTP device: created only for a freshly-created user that has no device.

Security:
  - The generated password and the TOTP otpauth:// URI are printed to STDOUT
    ONCE and ONLY ONCE. They are NEVER passed to `logging` and MUST NOT be
    echoed to logs. (Issue #172 security requirement.)
  - The otpauth:// URI is itself a secret: it embeds the Base32-encoded TOTP
    shared secret (`...?secret=<BASE32>`). It MUST be treated with the same
    care as the password — stdout-only, never logged. We never print the raw
    bytes of the secret separately, but printing the otpauth URI is equivalent
    to disclosing the secret, by design (the operator needs it to enroll 2FA).

Operator-console trust note:
  The operator console requires is_superuser AND a *confirmed* OTP device. We
  create the bootstrap device with confirmed=True so the first operator can log
  in immediately. The bootstrap operator is trusted by definition (they hold
  the deployment env and ran this command), so skipping the scan-and-verify
  round-trip for this single first-run device is an accepted trade-off.

Usage:

    python manage.py bootstrap_admin \\
      --email admin@example.com \\
      --org-name "Maple Court HOA" --org-hostname maplecourt.example.com \\
      [--org-support-email support@example.com] \\
      [--display-name "Administrator"] \\
      [--password-from-env ADMIN_PASSWORD] \\
      [--print-totp-uri]

See README "First-run admin bootstrap" and TECHNICAL-DESIGN §9 (TOTP).
"""

import os
import secrets

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from accounts.models import EncryptedTOTPDevice, User
from parking.models import Organization

# Length (in bytes of entropy) for a generated admin password. token_urlsafe(n)
# yields ~1.3*n characters; 24 bytes -> ~32 chars, comfortably strong.
_GENERATED_PASSWORD_BYTES = 24


class Command(BaseCommand):
    help = (
        "Idempotently bootstrap the first operator admin (org + superuser + "
        "confirmed TOTP device) for a fresh deployment. Re-runs are a safe "
        "no-op once the admin exists: the password, status, and TOTP device "
        "are never mutated on a second run.\n\n"
        "The TOTP otpauth:// URI is printed on first device creation so the "
        "operator can enroll 2FA; --print-totp-uri additionally labels it for "
        "easy copy-paste. A generated password (when --password-from-env is "
        "omitted) is printed once and never shown again. Neither secret is "
        "ever written to logs."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            required=True,
            help="Admin login email (USERNAME_FIELD).",
        )
        parser.add_argument(
            "--org-name",
            required=True,
            help="Organization display name (used only when creating a new org).",
        )
        parser.add_argument(
            "--org-hostname",
            required=True,
            help="Organization hostname (unique key; org is get-or-created by this).",
        )
        parser.add_argument(
            "--org-support-email",
            default=None,
            help="Organization.support_email. Defaults to --email when omitted.",
        )
        parser.add_argument(
            "--display-name",
            default=None,
            help=(
                "User.display_name. Defaults to the local-part of --email "
                "(e.g. 'admin' from admin@x.com), or 'Administrator'."
            ),
        )
        parser.add_argument(
            "--password-from-env",
            default=None,
            metavar="VARNAME",
            help=(
                "Read the admin password from os.environ[VARNAME]. If the var "
                "is unset or empty the command errors out (it will not silently "
                "generate one). When this flag is omitted entirely a strong "
                "password is generated and printed once."
            ),
        )
        parser.add_argument(
            "--print-totp-uri",
            action="store_true",
            help=(
                "Print the raw otpauth:// URI with an explicit label in "
                "addition to the standard confirmation. (The otpauth URI is "
                "printed on first device creation regardless, since the "
                "operator needs it to enroll 2FA; this flag only adds a "
                "clearly-labeled copy.)"
            ),
        )

    def handle(self, *args, **options):
        email = options["email"].strip()
        org_name = options["org_name"]
        org_hostname = options["org_hostname"].strip()
        org_support_email = options["org_support_email"] or email
        display_name = options["display_name"] or _default_display_name(email)
        password_env_var = options["password_from_env"]
        print_totp_uri = options["print_totp_uri"]
        verbosity = options["verbosity"]

        # --- Validation (fail fast, before any DB write) --------------------
        _validate_email_or_raise(email, field="--email")
        _validate_email_or_raise(org_support_email, field="--org-support-email")

        password = None
        password_was_generated = False
        if password_env_var is not None:
            password = os.environ.get(password_env_var)
            if not password:  # unset OR empty
                raise CommandError(
                    f"--password-from-env was given as {password_env_var!r} but "
                    f"that environment variable is unset or empty. Set it, or "
                    f"omit --password-from-env to have a password generated."
                )
        else:
            password = secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)
            password_was_generated = True

        # --- All-or-nothing bootstrap --------------------------------------
        # We collect the outcome inside the transaction and report AFTER it
        # commits, so a poisoned/rolled-back transaction never emits a success
        # message.  `created` distinguishes the first-run create path from both
        # idempotent re-runs and the concurrent-loser race below.
        created = False
        user = None
        otpauth_uri = None

        with transaction.atomic():
            org, org_created, org_name_conflict = _get_or_create_org(org_hostname, org_name, org_support_email)

            existing = User.objects.filter(organization=org, email=email).first()
            if existing is None:
                # Looks like a first run. Attempt the create inside a SAVEPOINT
                # so that a unique_together (organization, email) collision from
                # a concurrent invocation rolls back only this nested block —
                # NOT the outer transaction (an IntegrityError raised directly
                # inside the outer atomic() would poison it and force a full
                # rollback). On collision we re-query and fall through to the
                # idempotent no-op path: the concurrent loser behaves exactly
                # like an ordinary re-run.
                try:
                    with transaction.atomic():  # savepoint
                        # create_superuser via the custom manager sets
                        # is_staff/is_superuser/status='active'; flag HOA admin.
                        user = User.objects.create_superuser(
                            email=email,
                            organization=org,
                            display_name=display_name,
                            password=password,
                            is_hoa_admin=True,
                        )
                        # Create a confirmed TOTP device. The user is brand new
                        # so it cannot already have one. confirmed=True so the
                        # operator can sign in to the console right after
                        # scanning (first-run operator is trusted by definition).
                        device = EncryptedTOTPDevice.objects.create(
                            user=user,
                            name=f"{user.email} TOTP",
                            confirmed=True,
                        )
                        otpauth_uri = device.config_url
                        created = True
                except IntegrityError:
                    # A concurrent invocation inserted the (org, email) row
                    # between our SELECT and INSERT. The savepoint has rolled
                    # back; the outer transaction is intact. Re-query and treat
                    # as an existing admin.
                    existing = User.objects.filter(organization=org, email=email).first()

            if not created:
                # Re-run OR concurrent-loser: the admin already exists. Treat as
                # fully bootstrapped and do NOT mutate anything (no password
                # reset, no status change, no device touch). Idempotent no-op.
                self._report_noop(org, org_created, org_name_conflict, existing, verbosity)
                return

        self._report_created(
            org=org,
            org_created=org_created,
            org_name_conflict=org_name_conflict,
            user=user,
            password=password,
            password_was_generated=password_was_generated,
            otpauth_uri=otpauth_uri,
            print_totp_uri=print_totp_uri,
            verbosity=verbosity,
        )

    # -- output helpers -----------------------------------------------------

    def _report_noop(self, org, org_created, org_name_conflict, user, verbosity):
        if verbosity < 1:
            return
        if org_name_conflict:
            self.stdout.write(
                self.style.WARNING(
                    f"Organization with hostname '{org.hostname}' already "
                    f"exists with a different name ('{org.name}'); using the "
                    f"existing org unchanged."
                )
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"Admin '{user.email}' already exists in org '{org.name}' — "
                f"nothing to do. Password, status, and TOTP device were left "
                f"untouched (idempotent re-run)."
            )
        )

    def _report_created(
        self,
        *,
        org,
        org_created,
        org_name_conflict,
        user,
        password,
        password_was_generated,
        otpauth_uri,
        print_totp_uri,
        verbosity,
    ):
        # Even at verbosity 0 we must surface the one-time secrets, otherwise
        # the operator loses the only copy of the generated password.
        write = self.stdout.write

        if org_name_conflict:
            write(
                self.style.WARNING(
                    f"Organization with hostname '{org.hostname}' already "
                    f"exists with a different name ('{org.name}'); using the "
                    f"existing org unchanged (the supplied --org-name was NOT "
                    f"applied)."
                )
            )

        if verbosity >= 1:
            write(self.style.SUCCESS("Bootstrap complete. Summary:"))
            write(f"  Organization: '{org.name}' ({org.hostname}) " f"[{'created' if org_created else 'existing'}]")
            write(f"  Admin user:   {user.email} (display name: {user.display_name})")
            write("                is_superuser=True, is_staff=True, is_hoa_admin=True, status='active'")
            write("  TOTP device:  created (confirmed) — ready for immediate console login.")
            write("")

        # --- ONE-TIME SECRETS — stdout only, never logged -------------------
        # SECURITY (#172): these lines print the generated password and the
        # otpauth:// URI. They go to self.stdout exclusively and must never be
        # routed through `logging` or echoed into any log sink.
        if password_was_generated:
            write(self.style.WARNING("  Generated admin password (store this now — it will not be shown again):"))
            write(f"    {password}")
            write("")

        write("  Enroll 2FA with this otpauth URI (scan as a QR in your authenticator app):")
        write(f"    {otpauth_uri}")
        if print_totp_uri:
            # --print-totp-uri: emit a bare, clearly-labeled line for scripted
            # copy-paste in addition to the human-readable block above.
            write("")
            write(f"  TOTP_OTPAUTH_URI={otpauth_uri}")
        write("")

        if verbosity >= 1:
            write(self.style.SUCCESS("Next steps:"))
            write("  1. Add the otpauth URI above to your authenticator app (it is already confirmed).")
            if password_was_generated:
                write("  2. Log in to the operator console with the admin email and the generated password above.")
            else:
                write("  2. Log in to the operator console with the admin email and your supplied password.")


def _default_display_name(email):
    """Local-part of the email, or 'Administrator' if it cannot be derived."""
    local_part = email.split("@", 1)[0].strip()
    return local_part or "Administrator"


def _validate_email_or_raise(value, *, field):
    try:
        validate_email(value)
    except ValidationError:
        raise CommandError(f"{field} is not a valid email address: {value!r}")


def _get_or_create_org(hostname, name, support_email):
    """get-or-create org by hostname; never overwrite an existing org's name.

    Returns (org, created, name_conflict) where name_conflict is True when the
    org already existed with a name different from the supplied --org-name.
    """
    org, created = Organization.objects.get_or_create(
        hostname=hostname,
        defaults={"name": name, "support_email": support_email},
    )
    name_conflict = (not created) and org.name != name
    return org, created, name_conflict
