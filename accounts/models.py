"""
accounts.models — User, UserManager, Invite, EmailOTP, AdminAuditLog.

User extends AbstractBaseUser + PermissionsMixin.
Phone is field-encrypted via django-encrypted-model-fields.
AUTH_USER_MODEL = 'accounts.User' is set in settings.
"""

from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    PermissionsMixin,
)
from django.db import models
from encrypted_model_fields.fields import EncryptedCharField


def default_notification_prefs():
    return {"push": False}


class UserManager(BaseUserManager):
    def create_user(
        self, email, organization, display_name, password=None, **extra_fields
    ):
        if not email:
            raise ValueError("Email required")
        user = self.model(
            email=email,
            organization=organization,
            display_name=display_name,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(
        self, email, organization, display_name, password, **extra_fields
    ):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("status", "active")
        return self.create_user(
            email, organization, display_name, password, **extra_fields
        )


class User(AbstractBaseUser, PermissionsMixin):
    organization = models.ForeignKey(
        "parking.Organization", on_delete=models.PROTECT, related_name="users"
    )

    # PII — volume encryption only (LUKS). Not field-encrypted (breaks login lookup).
    email = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)

    # PII — field-encrypted (django-encrypted-model-fields)
    phone = EncryptedCharField(max_length=50, null=True, blank=True)

    # TOTP secret is NOT stored on User — it lives in django-otp's TOTPDevice.key.
    # See TECHNICAL-DESIGN.md §9 for enrollment and verification flow.

    # Recovery codes — list of Argon2-hashed strings. Shown to user once; never stored plaintext.
    recovery_codes = models.JSONField(default=list)

    STATUS_CHOICES = [
        ("pending_totp", "Pending TOTP Enrollment"),
        ("pending_approval", "Pending HOA Approval"),
        ("active", "Active"),
        ("blocked", "Blocked"),
    ]
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="pending_totp"
    )

    is_hoa_admin = models.BooleanField(default=False)
    is_staff = models.BooleanField(default=False)  # Django admin access
    is_active = models.BooleanField(default=True)

    # Schema: {'push': False}
    # 'push' is the only key. Email is intentionally absent — it cannot be disabled.
    notification_prefs = models.JSONField(default=default_notification_prefs)
    marketing_email_opted_in = models.BooleanField(default=False)

    # Denormalized for owner-rotation assignment query
    last_booking_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["display_name", "organization_id"]

    objects = UserManager()

    class Meta:
        unique_together = [("organization", "email")]

    def __str__(self):
        return f"{self.display_name} <{self.email}>"


class Invite(models.Model):
    organization = models.ForeignKey("parking.Organization", on_delete=models.CASCADE)
    issued_by = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="issued_invites"
    )
    code = models.CharField(max_length=64, unique=True)  # secrets.token_urlsafe(32)
    unit_number = models.CharField(
        max_length=50, blank=True
    )  # pre-tag: pre-fills registration form
    max_uses = models.PositiveIntegerField(default=1)
    use_count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    consumed_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consumed_invite",
    )
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        from django.utils.timezone import now

        if self.use_count >= self.max_uses:
            return False
        if self.expires_at and self.expires_at < now():
            return False
        return True

    def __str__(self):
        return f"Invite {self.code[:8]}… ({self.organization})"


class EmailOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_otps")
    code_hash = models.CharField(max_length=256)  # Argon2 hash of the 6-digit code
    expires_at = models.DateTimeField()  # now() + 15 minutes
    consumed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"EmailOTP user={self.user_id} consumed={self.consumed}"


class AdminAuditLog(models.Model):
    organization = models.ForeignKey(
        "parking.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    actor = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="audit_actions"
    )
    on_behalf_of = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_impersonations",
    )  # impersonation sessions
    action = models.CharField(max_length=100)
    # Actions: pii_access, pii_erasure, admin_cancel, block, unblock, approve_user,
    #          approve_spot, impersonate_start, impersonate_end, admin_adjustment,
    #          impersonate_action
    target_type = models.CharField(
        max_length=50, blank=True
    )  # 'user', 'booking', 'spot'
    target_id = models.PositiveIntegerField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "created_at"])]

    def __str__(self):
        return f"AuditLog {self.action} by {self.actor_id} at {self.created_at}"

    @classmethod
    def log(
        cls,
        actor,
        action,
        organization=None,
        target_type="",
        target_id=None,
        notes="",
        on_behalf_of=None,
    ):
        """
        Convenience classmethod for creating an AdminAuditLog entry.

        Falls back to ``actor.organization`` when *organization* is not supplied
        so callers in portal views do not have to pass it explicitly.
        """
        return cls.objects.create(
            actor=actor,
            action=action,
            organization=organization or getattr(actor, "organization", None),
            target_type=target_type,
            target_id=target_id,
            notes=notes,
            on_behalf_of=on_behalf_of,
        )
