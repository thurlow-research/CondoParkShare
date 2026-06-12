# CondoParkShare — Technical Design
*June 2026. Implementation contract for the pilot build. Supplements ADR-001-pilot.md and CONFIRMED-REQUIREMENTS.md. The coder implements from this document — not directly from the spec.*

---

## 1. Django app structure

```
parkshare/              ← Django project root
  settings/
    base.py
    production.py
  urls.py
  wsgi.py

accounts/               ← User, auth, TOTP, invites, registration
parking/                ← Organization, ParkingSpot, AvailabilityWindow, Booking
notifications/          ← notification dispatch, email relay, web push
portal/                 ← HOA/manager portal views
operator/               ← operator console (Django admin extensions)
```

---

## 2. Data models

### 2.1 `parking.Organization`

```python
class Organization(models.Model):
    name            = models.CharField(max_length=255)
    hostname        = models.CharField(max_length=255, unique=True)
    timezone        = models.CharField(max_length=64, default='America/Los_Angeles')
    registration_mode = models.CharField(
        max_length=20,
        choices=[('invite_only','Invite Only'),('approve','Approve'),('both','Both')],
        default='invite_only'
    )
    unit_count      = models.PositiveIntegerField(null=True, blank=True)
    payer_model     = models.CharField(max_length=20, default='free_forever')
    support_email   = models.EmailField()
    launched_at     = models.DateTimeField(null=True, blank=True)

    # Booking config
    booking_buffer_hours        = models.PositiveIntegerField(default=1)  # fixed at 1, reserved for future
    max_concurrent_bookings     = models.PositiveIntegerField(default=1)
    max_booking_hours           = models.PositiveIntegerField(default=168)

    # Horizon config
    booking_horizon_baseline_days   = models.PositiveIntegerField(default=3)
    booking_horizon_max_days        = models.PositiveIntegerField(default=30)
    listing_to_horizon_ratio        = models.PositiveIntegerField(default=10)
    tier_metric_window_days         = models.PositiveIntegerField(default=180)
    launch_grace_days               = models.PositiveIntegerField(default=14)
    launch_grace_horizon_days       = models.PositiveIntegerField(default=14)

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    objects = models.Manager()  # unscoped; used by TenantMiddleware and operator console only

    def __str__(self):
        return self.name
```

### 2.2 `accounts.User`

Extends `AbstractBaseUser` + `PermissionsMixin`. Password hashing: `Argon2PasswordHasher` (set in `PASSWORD_HASHERS`).

```python
from encrypted_model_fields.fields import EncryptedCharField

def default_notification_prefs():
    return {'push': False}

class User(AbstractBaseUser, PermissionsMixin):
    organization    = models.ForeignKey('parking.Organization', on_delete=models.PROTECT, related_name='users')

    # PII — volume encryption only (LUKS). Not field-encrypted (breaks login lookup).
    email           = models.CharField(max_length=255)
    display_name    = models.CharField(max_length=255)

    # PII — field-encrypted (django-encrypted-model-fields)
    phone           = EncryptedCharField(max_length=50, null=True, blank=True)

    # TOTP secret is NOT stored on User — it lives in django-otp's TOTPDevice.key.
    # See §9 for enrollment and verification flow.

    # Recovery codes — list of Argon2-hashed strings. Shown to user once; never stored plaintext.
    recovery_codes  = models.JSONField(default=list)

    STATUS_CHOICES = [
        ('pending_totp',     'Pending TOTP Enrollment'),
        ('pending_approval', 'Pending HOA Approval'),
        ('active',           'Active'),
        ('blocked',          'Blocked'),
    ]
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending_totp')

    is_hoa_admin    = models.BooleanField(default=False)
    is_staff        = models.BooleanField(default=False)   # Django admin access
    is_active       = models.BooleanField(default=True)

    # Schema: {'push': False}
    # 'push' is the only key. Email is intentionally absent — it cannot be disabled.
    notification_prefs      = models.JSONField(default=default_notification_prefs)
    marketing_email_opted_in = models.BooleanField(default=False)

    # Denormalized for owner-rotation assignment query
    last_booking_at = models.DateTimeField(null=True, blank=True)

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    USERNAME_FIELD  = 'email'
    REQUIRED_FIELDS = ['display_name', 'organization_id']

    objects = UserManager()  # see §3

    class Meta:
        unique_together = [('organization', 'email')]
```

**`UserManager`:**
```python
class UserManager(BaseUserManager):
    def create_user(self, email, organization, display_name, password=None, **extra_fields):
        if not email:
            raise ValueError('Email required')
        user = self.model(
            email=email,
            organization=organization,
            display_name=display_name,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, organization, display_name, password, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('status', 'active')
        return self.create_user(email, organization, display_name, password, **extra_fields)
```

### 2.3 `accounts.Invite`

```python
class Invite(models.Model):
    organization    = models.ForeignKey('parking.Organization', on_delete=models.CASCADE)
    issued_by       = models.ForeignKey(User, on_delete=models.PROTECT, related_name='issued_invites')
    code            = models.CharField(max_length=64, unique=True)  # secrets.token_urlsafe(32)
    unit_number     = models.CharField(max_length=50, blank=True)   # pre-tag: pre-fills registration form
    max_uses        = models.PositiveIntegerField(default=1)
    use_count       = models.PositiveIntegerField(default=0)
    expires_at      = models.DateTimeField(null=True, blank=True)
    consumed_by     = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name='consumed_invite')
    consumed_at     = models.DateTimeField(null=True, blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    def is_valid(self):
        from django.utils.timezone import now
        if self.use_count >= self.max_uses:
            return False
        if self.expires_at and self.expires_at < now():
            return False
        return True
```

### 2.4 `accounts.EmailOTP`

```python
class EmailOTP(models.Model):
    user        = models.ForeignKey(User, on_delete=models.CASCADE, related_name='email_otps')
    code_hash   = models.CharField(max_length=256)  # Argon2 hash of the 6-digit code
    expires_at  = models.DateTimeField()             # now() + 15 minutes
    consumed    = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)
```

### 2.5 `parking.ParkingSpot`

```python
class ParkingSpot(models.Model):
    organization    = models.ForeignKey(Organization, on_delete=models.PROTECT, related_name='spots')
    owner           = models.ForeignKey('accounts.User', on_delete=models.PROTECT,
                                        related_name='owned_spots', null=True, blank=True)
    spot_number     = models.CharField(max_length=50)   # e.g. "P3076", string
    notes           = models.TextField(blank=True)      # admin-only annotation

    STATUS_CHOICES = [('pending','Pending'),('active','Active'),('inactive','Inactive')]
    status          = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    objects         = models.Manager()
    scoped          = OrganizationScopedManager()  # see §3; use in all tenant views

    class Meta:
        unique_together = [('organization', 'spot_number')]
```

### 2.6 `parking.AvailabilityWindow`

```python
from django.contrib.postgres.fields import DateTimeRangeField
from django.contrib.postgres.indexes import GistIndex

class AvailabilityWindow(models.Model):
    organization    = models.ForeignKey(Organization, on_delete=models.PROTECT)
    spot            = models.ForeignKey(ParkingSpot, on_delete=models.CASCADE,
                                        related_name='availability_windows')
    time_range      = DateTimeRangeField()   # tstzrange; continuous window; stored UTC

    created_at      = models.DateTimeField(auto_now_add=True)

    objects     = models.Manager()
    scoped      = OrganizationScopedManager()

    class Meta:
        indexes = [GistIndex(fields=['time_range'])]
```

### 2.7 `parking.Booking`

```python
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators

class Booking(models.Model):
    organization    = models.ForeignKey(Organization, on_delete=models.PROTECT)
    spot            = models.ForeignKey(ParkingSpot, on_delete=models.PROTECT, related_name='bookings')
    borrower        = models.ForeignKey('accounts.User', on_delete=models.PROTECT,
                                        related_name='bookings', null=True, blank=True)
    # null=True on borrower to support anonymisation on right-to-erasure

    time_range      = DateTimeRangeField()   # tstzrange; hour-aligned; stored UTC

    STATUS_CHOICES = [
        ('tentative',          'Tentative'),        # held, 5-min expiry
        ('confirmed',          'Confirmed'),
        ('active',             'Active'),            # start time has passed
        ('completed',          'Completed'),
        ('cancelled_borrower', 'Cancelled by Borrower'),
        ('cancelled_owner',    'Cancelled by Owner'),
        ('cancelled_admin',    'Cancelled by Admin'),
    ]
    status                  = models.CharField(max_length=20, choices=STATUS_CHOICES, default='tentative')
    tentative_expires_at    = models.DateTimeField(null=True, blank=True)  # now() + 5 min
    cancel_reason           = models.TextField(blank=True)
    penalty_hours           = models.PositiveIntegerField(default=0)  # set on owner-cancel
    is_anonymized           = models.BooleanField(default=False)      # set on right-to-erasure

    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    objects     = models.Manager()
    scoped      = OrganizationScopedManager()

    class Meta:
        indexes = [GistIndex(fields=['time_range'])]
        constraints = [
            ExclusionConstraint(
                name='booking_no_overlap',
                expressions=[
                    ('spot', RangeOperators.EQUAL),
                    ('time_range', RangeOperators.OVERLAPS),
                ],
                condition=models.Q(status__in=['tentative', 'confirmed', 'active']),
            )
        ]
```

### 2.8 `accounts.AdminAuditLog`

```python
class AdminAuditLog(models.Model):
    organization    = models.ForeignKey('parking.Organization', on_delete=models.PROTECT,
                                        null=True, blank=True)
    actor           = models.ForeignKey(User, on_delete=models.PROTECT, related_name='audit_actions')
    on_behalf_of    = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name='audit_impersonations')  # impersonation sessions
    action          = models.CharField(max_length=100)
    # Actions: pii_access, pii_erasure, admin_cancel, block, unblock, approve_user,
    #          approve_spot, impersonate_start, impersonate_end, admin_adjustment
    target_type     = models.CharField(max_length=50, blank=True)  # 'user', 'booking', 'spot'
    target_id       = models.PositiveIntegerField(null=True, blank=True)
    notes           = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['organization', 'created_at'])]
```

### 2.9 `notifications.WebPushSubscription`

```python
class WebPushSubscription(models.Model):
    user        = models.ForeignKey('accounts.User', on_delete=models.CASCADE,
                                    related_name='push_subscriptions')
    endpoint    = models.URLField(max_length=500, unique=True)
    p256dh      = models.CharField(max_length=256)
    auth        = models.CharField(max_length=64)
    created_at  = models.DateTimeField(auto_now_add=True)
```

### 2.10 `notifications.RelayMessage`

```python
import uuid

class RelayMessage(models.Model):
    organization    = models.ForeignKey('parking.Organization', on_delete=models.PROTECT)
    from_user       = models.ForeignKey('accounts.User', on_delete=models.PROTECT,
                                        related_name='sent_relay_messages')
    to_user         = models.ForeignKey('accounts.User', on_delete=models.PROTECT,
                                        related_name='received_relay_messages')
    booking         = models.ForeignKey('parking.Booking', on_delete=models.PROTECT,
                                        related_name='relay_messages')
    body            = models.TextField()
    reply_token     = models.UUIDField(default=uuid.uuid4, unique=True)
    token_expires_at = models.DateTimeField()   # = booking.time_range.upper
    created_at      = models.DateTimeField(auto_now_add=True)
```

---

## 3. Multi-tenant ORM scoping

### Thread-local pattern

```python
# parkshare/middleware.py
import threading
_thread_locals = threading.local()

def get_current_organization():
    return getattr(_thread_locals, 'organization', None)

class TenantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(':')[0].lower()
        try:
            from parking.models import Organization
            org = Organization.objects.get(hostname=host)
        except Organization.DoesNotExist:
            from django.http import Http404
            raise Http404
        _thread_locals.organization = org
        request.organization = org
        try:
            return self.get_response(request)
        finally:
            _thread_locals.organization = None
```

### Scoped manager

```python
# parkshare/managers.py
from django.db import models
from parkshare.middleware import get_current_organization

class OrganizationScopedManager(models.Manager):
    def get_queryset(self):
        org = get_current_organization()
        qs = super().get_queryset()
        return qs.filter(organization=org) if org else qs.none()
```

Every model with an `organization` FK exposes two managers:
- `objects` — unscoped (middleware, operator console, management commands)
- `scoped` — tenant-scoped (all tenant views and portal views)

**Rule:** all resident and portal views use `.scoped`; operator console and management commands use `.objects`.

### HOA admin enforcement

All `portal/` views must explicitly verify:
```python
if obj.organization != request.user.organization:
    raise PermissionDenied
```
Do not rely on the scoped manager alone — verify on every object fetch in portal views.

---

## 4. URL structure

### `parkshare/urls.py`

```python
urlpatterns = [
    # Auth
    path('accounts/login/',                         name='login'),
    path('accounts/logout/',                        name='logout'),
    path('accounts/register/',                      name='register'),           # Mode B self-register
    path('accounts/register/<str:code>/',           name='register_invite'),    # Mode A invite
    path('accounts/totp/enroll/',                   name='totp_enroll'),
    path('accounts/totp/verify/',                   name='totp_verify'),
    path('accounts/recovery/',                      name='recovery_code'),
    path('accounts/lost-authenticator/',            name='lost_authenticator'),
    path('accounts/lost-authenticator/verify/',     name='lost_authenticator_verify'),
    path('accounts/profile/',                       name='profile'),
    path('accounts/notifications/',                 name='notification_prefs'),

    # Booking
    path('',                                        name='home'),
    path('book/',                                   name='book_request'),
    path('book/confirm/',                           name='book_confirm'),
    path('bookings/',                               name='booking_list'),
    path('bookings/<int:pk>/',                      name='booking_detail'),
    path('bookings/<int:pk>/cancel/',               name='booking_cancel'),
    path('bookings/<int:pk>/release/',              name='booking_release'),

    # Spot listing (owner)
    path('spots/',                                  name='spot_list'),
    path('spots/<int:pk>/availability/',            name='spot_availability'),
    path('spots/<int:pk>/availability/add/',        name='availability_add'),
    path('spots/<int:pk>/windows/<int:wk>/remove/', name='availability_remove'),

    # Messaging relay
    path('messages/send/<int:booking_pk>/',         name='message_send'),
    path('messages/reply/<uuid:token>/',            name='message_reply'),

    # HOA portal
    path('portal/', include('portal.urls')),

    # Operator console
    path('admin/', admin.site.urls),
    path('admin/impersonation/end/', name='impersonation_end'),

    # Web push + PWA
    path('push/subscribe/',                         name='push_subscribe'),
    path('push/unsubscribe/',                       name='push_unsubscribe'),
    path('manifest.json',                           name='pwa_manifest'),
    path('sw.js',                                   name='service_worker'),
]
```

### `portal/urls.py`

```python
urlpatterns = [
    path('',                                name='portal_home'),
    path('residents/',                      name='portal_residents'),
    path('residents/<int:pk>/approve/',     name='portal_resident_approve'),
    path('residents/<int:pk>/block/',       name='portal_resident_block'),
    path('residents/<int:pk>/unblock/',     name='portal_resident_unblock'),
    path('spots/',                          name='portal_spots'),
    path('spots/<int:pk>/approve/',         name='portal_spot_approve'),
    path('spots/<int:pk>/deactivate/',      name='portal_spot_deactivate'),
    path('invites/',                        name='portal_invites'),
    path('invites/create/',                 name='portal_invite_create'),
    path('bookings/',                       name='portal_bookings'),
    path('bookings/<int:pk>/cancel/',       name='portal_booking_cancel'),
    path('reports/',                        name='portal_reports'),
]
```

---

## 5. View contracts

All views: `login_required` + `status_required('active')` decorator unless noted. HTMX requests return partials; direct navigation returns full page (check `request.headers.get('HX-Request')`).

### Auth views

| View | Methods | Auth | Notes |
|---|---|---|---|
| `login` | GET, POST | None | Renders login form; POST validates email+password; on success redirects to `totp_verify` |
| `totp_verify` | GET, POST | Session (pre-TOTP) | Renders TOTP code form; validates against enrolled device |
| `totp_enroll` | GET, POST | Session (status=pending_totp) | GET: generate secret, render QR; POST: verify first code, generate+display 10 recovery codes |
| `recovery_code` | GET, POST | Session (pre-TOTP) | POST: validate recovery code, consume it, set `totp_reset_required` in session |
| `lost_authenticator` | GET, POST | None | POST: generate EmailOTP (expires 15 min), send email, redirect to verify |
| `lost_authenticator_verify` | GET, POST | None | POST: validate EmailOTP, set `totp_reset_required`, redirect to `totp_enroll` |
| `register_invite` | GET, POST | None | GET: load invite by code (validate), pre-fill unit_number; POST: create User (status=pending_totp), consume invite, redirect to `totp_enroll` |
| `register` | GET, POST | None | Only if `registration_mode` in ('approve','both'); POST: create User (status=pending_approval) |
| `logout` | POST | Active | Clears session |

### `BookingRequestForm`

```python
class BookingRequestForm(forms.Form):
    start = forms.DateTimeField()
    end   = forms.DateTimeField()

    def __init__(self, *args, org=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._org = org
    # Instantiate as: BookingRequestForm(request.POST, org=request.organization)

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start'), cleaned.get('end')
        if not start or not end:
            return cleaned
        now_dt = now()
        org = self._org  # set via form.__init__(org=request.organization)
        if start <= now_dt:
            raise ValidationError('Start time must be in the future.')
        if start.minute != 0 or start.second != 0:
            raise ValidationError('Start time must be on the hour.')
        if end.minute != 0 or end.second != 0:
            raise ValidationError('End time must be on the hour.')
        if end <= start:
            raise ValidationError('End must be after start.')
        duration_hours = int((end - start).total_seconds() / 3600)
        if duration_hours < 1:
            raise ValidationError('Minimum booking is 1 hour.')
        if duration_hours > org.max_booking_hours:
            raise ValidationError(f'Maximum booking is {org.max_booking_hours} hours.')
        return cleaned
```

### Booking views

| View | Methods | Notes |
|---|---|---|
| `book_request` | GET, POST | GET: render time-window form; POST: validate `BookingRequestForm`, run assignment algorithm, create tentative Booking, store `booking_pk` in session, redirect to `book_confirm` |
| `book_confirm` | GET, POST | GET: show assigned spot + time + owner name (not email/phone); POST: confirm booking (status → confirmed), notify owner; checks tentative not expired |
| `booking_cancel` | POST | Verify `request.user == booking.borrower or request.user == booking.spot.owner`; raise `PermissionDenied` otherwise. If `booking.borrower` is None (erased user), only owner may cancel. Voids booking; notifies other party; if owner-cancel: prompt for optional reason, set `penalty_hours` to booking duration in hours. |
| `booking_release` | GET, POST | Verify `request.user == booking.borrower`. GET: render release form (hours to release, minimum 1, from next hour boundary); POST: shorten booking end, return hours to inventory. |

### Owner views

| View | Methods | Notes |
|---|---|---|
| `spot_availability` | GET | Lists owner's spot(s) with current availability windows and upcoming bookings |
| `availability_add` | GET, POST | POST: create `AvailabilityWindow`; validates range is hour-aligned and future |
| `availability_remove` | POST | Verify `request.user == spot.owner`; raise `PermissionDenied` otherwise. Deletes window only if no active/confirmed bookings overlap it. |

---

## 6. Availability computation and spot assignment

### Is a spot available?

```python
from datetime import timedelta
from django.db.models import Q
from psycopg2.extras import DateTimeTZRange

BUFFER_HOURS = 1  # fixed for pilot

def is_spot_available(spot, requested_start, requested_end):
    buffer = timedelta(hours=BUFFER_HOURS)
    buffered = DateTimeTZRange(requested_start - buffer, requested_end + buffer)

    covers = spot.availability_windows.filter(
        time_range__contains=DateTimeTZRange(requested_start, requested_end)
    ).exists()
    if not covers:
        return False

    conflicts = spot.bookings.filter(
        Q(status__in=['tentative', 'confirmed', 'active']),
        time_range__overlap=buffered,
    ).exists()
    return not conflicts
```

### Spot assignment (owner rotation)

```python
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils.timezone import now

def assign_spot(organization, borrower, requested_start, requested_end):
    """
    Finds and tentatively assigns a spot. Returns a Booking (status='tentative')
    or None if no spot is available.
    """
    buffer = timedelta(hours=BUFFER_HOURS)
    buffered = DateTimeTZRange(requested_start - buffer, requested_end + buffer)
    req_range = DateTimeTZRange(requested_start, requested_end)

    # Subquery: any booking on this spot that conflicts (both conditions on the same row)
    conflict = Booking.objects.filter(
        spot=OuterRef('pk'),
        time_range__overlap=buffered,
        status__in=['tentative', 'confirmed', 'active'],
    )

    with transaction.atomic():
        candidates = (
            ParkingSpot.objects
            .select_for_update(skip_locked=True)
            .filter(
                organization=organization,
                status='active',
                availability_windows__time_range__contains=req_range,
            )
            .exclude(Exists(conflict))
            .select_related('owner')
            .order_by(
                models.F('owner__last_booking_at').asc(nulls_first=True)
            )
            .distinct()
        )

        spot = candidates.first()
        if not spot:
            return None

        booking = Booking.objects.create(
            organization=organization,
            spot=spot,
            borrower=borrower,
            time_range=req_range,
            status='tentative',
            tentative_expires_at=now() + timedelta(minutes=5),
        )
        return booking
```

### Expired tentative hold cleanup

Management command `clean_tentative_bookings` — run at `:00` each hour (alongside the notification job):

```python
Booking.objects.filter(
    status='tentative',
    tentative_expires_at__lt=now()
).update(status='cancelled_admin')
```

---

## 7. Earned-horizon metric

```python
from math import floor
from datetime import timedelta
from django.db.models import Sum
from django.db.models.functions import Upper, Lower
from django.utils.timezone import now

def get_earned_horizon_hours(user):
    org = user.organization
    now_dt = now()

    # Cold-start grace
    if org.launched_at:
        days_live = (now_dt - org.launched_at).days
        if days_live < org.launch_grace_days:
            return org.launch_grace_horizon_days * 24

    window_start = now_dt - timedelta(days=org.tier_metric_window_days)

    # Elapsed listed hours: AvailabilityWindows owned by user, fully in past, within rolling window
    from django.db.models import ExpressionWrapper, DurationField
    from django.db.models.functions import Greatest, Least

    elapsed = (
        AvailabilityWindow.objects
        .filter(
            spot__owner=user,
            spot__organization=org,
            spot__status='active',
            time_range__endswith__lte=now_dt,       # upper bound has passed
            time_range__startswith__gte=window_start,  # within rolling window
        )
        .annotate(
            hours=ExpressionWrapper(
                (Upper('time_range') - Lower('time_range')),
                output_field=DurationField()
            )
        )
        .aggregate(total=Sum('hours'))['total']
    )
    elapsed_hours = elapsed.total_seconds() / 3600 if elapsed else 0

    # Penalty: owner-cancelled bookings whose scheduled time falls within the window.
    # Filter on time_range start, not updated_at — we penalise hours the owner committed
    # to provide within the window, not cancellations that happened within the window.
    penalties = (
        Booking.objects
        .filter(
            spot__owner=user,
            spot__organization=org,
            status='cancelled_owner',
            time_range__startswith__gte=window_start,
        )
        .aggregate(total=Sum('penalty_hours'))['total'] or 0
    )

    net_hours = max(0, elapsed_hours - penalties)
    baseline  = org.booking_horizon_baseline_days * 24
    earned    = floor(net_hours / org.listing_to_horizon_ratio)
    maximum   = org.booking_horizon_max_days * 24

    return min(baseline + earned, maximum)


def check_horizon_gate(borrower, requested_start):
    """Gate 1: requested start must be within the resident's earned horizon."""
    horizon_hours = get_earned_horizon_hours(borrower)
    from django.utils.timezone import now
    max_start = now() + timedelta(hours=horizon_hours)
    return requested_start <= max_start
```

### Setting `penalty_hours` on owner-cancel

```python
def owner_cancel_booking(booking, reason=''):
    from django.utils.timezone import now
    now_dt = now()
    start  = booking.time_range.lower
    end    = booking.time_range.upper

    # Penalty = hours of booking that fall within the listing (all of them in most cases)
    duration_hours = int((end - start).total_seconds() / 3600)

    booking.status        = 'cancelled_owner'
    booking.cancel_reason = reason
    booking.penalty_hours = duration_hours
    booking.save()

    # Update owner's last_booking_at is NOT updated on cancel — only on completion
    # Notify borrower
    from notifications.dispatch import notify
    notify('booking_cancelled_by_owner', booking)
```

---

## 8. Three booking gates

Applied in order inside `book_request` POST:

```python
def book_request(request):
    # Parse and validate form (hour-aligned, whole hours, max_booking_hours)
    # ...

    # Gate 1 — Horizon
    if not check_horizon_gate(request.user, requested_start):
        return htmx_error('You can't book that far ahead yet. List your spot to earn more.')

    # Gate 2 — One active booking
    active = Booking.scoped.filter(
        borrower=request.user,
        status__in=['tentative', 'confirmed', 'active'],
    ).exists()
    if active:
        return htmx_error('You already have an active booking.')

    # Gate 3 — Assign (includes DB overlap check via ExclusionConstraint)
    booking = assign_spot(request.organization, request.user, requested_start, requested_end)
    if not booking:
        return htmx_error('No spots are available for that window. Try a different time.')

    request.session['pending_booking_pk'] = booking.pk
    return redirect('book_confirm')
```

---

## 9. TOTP and authentication

**Library:** `django-otp` with `django_otp.plugins.otp_totp.models.TOTPDevice`.

### Enrollment

1. `GET /accounts/totp/enroll/` — generate `TOTPDevice` (unconfirmed), render QR code using `device.config_url`.
2. `POST /accounts/totp/enroll/` — verify submitted code via `device.verify_token(token)`. On success:
   - `device.confirmed = True`; `device.save()`
   - Generate 10 recovery codes: `[secrets.token_urlsafe(10) for _ in range(10)]`
   - Hash each: `[make_password(code) for code in plaintext_codes]`
   - Store hashed list on `user.recovery_codes`
   - Display plaintext codes **once** in the response
   - Set `user.status = 'active'`; `user.save()`

### Login flow

1. Email + password → `authenticate()` → session set with `_auth_user_id` but **not** fully authenticated
2. Redirect to `totp_verify`
3. `POST /accounts/totp/verify/` — `django_otp.verify_token(user, token)` — on success: `django_otp.login(request, device)`
4. Session now fully authenticated

### Recovery code flow

1. User enters a recovery code at `totp_verify` (separate link)
2. Iterate `user.recovery_codes`; for each hash call `check_password(submitted, hash)`
3. On match: remove that entry from the list; `user.save()`
4. Set `request.session['totp_reset_required'] = True`
5. Redirect to `totp_enroll` (generates new device, new codes)

### Lost authenticator flow (NEW-2)

1. `POST /accounts/lost-authenticator/` — validate email exists; invalidate all prior non-consumed OTPs; create a fresh `EmailOTP`:
   ```python
   # Invalidate any prior unexpired OTPs before issuing a new one
   EmailOTP.objects.filter(user=user, consumed=False, expires_at__gt=now()).update(consumed=True)

   otp_code  = f'{secrets.randbelow(1000000):06d}'
   code_hash = make_password(otp_code)
   EmailOTP.objects.create(user=user, code_hash=code_hash,
                           expires_at=now() + timedelta(minutes=15))
   ```
   Send email with `otp_code`. Never reveal whether an email address exists — return the same response regardless (enumerate-safe).
2. `POST /accounts/lost-authenticator/verify/` — find non-consumed, non-expired `EmailOTP` for user; `check_password(submitted, otp.code_hash)`. On match: `otp.consumed = True; otp.save()`; set session with `totp_reset_required=True`; redirect to `totp_enroll`.

---

## 10. Notification dispatch

### Management command: `notify_bookings`

Accepts `--event` flag. Run via cron:

```
0  * * * *  python manage.py notify_bookings --event starts,completions,tentative_cleanup
30 * * * *  python manage.py notify_bookings --event warning_30
45 * * * *  python manage.py notify_bookings --event warning_15
```

**`starts`** — bookings where `time_range` lower bound falls in `(now - 1h, now]` and status is `confirmed`:
```python
Booking.objects.filter(
    time_range__startswith__gt=now() - timedelta(hours=1),
    time_range__startswith__lte=now(),
    status='confirmed',
).update(status='active')
# notify borrower: 'booking_starts'
```

**`completions`** — bookings where `time_range` upper bound falls in `(now - 1h, now]` and status is `active`:
```python
# Evaluate to list BEFORE update — queryset is consumed by .update()
completed_bookings = list(Booking.objects.filter(
    time_range__endswith__gt=now() - timedelta(hours=1),
    time_range__endswith__lte=now(),
    status='active',
).select_related('spot__owner'))

if completed_bookings:
    pks = [b.pk for b in completed_bookings]
    Booking.objects.filter(pk__in=pks).update(status='completed')

    for booking in completed_bookings:
        owner = booking.spot.owner
        if owner:
            owner.last_booking_at = booking.time_range.upper
            owner.save(update_fields=['last_booking_at'])
        notify('booking_completed', booking)
```

**`warning_30` / `warning_15`** — bookings ending in approximately 30/15 minutes:
```python
target_end = now() + timedelta(minutes=30)   # or 15
Booking.objects.filter(
    time_range__endswith__gt=target_end - timedelta(minutes=5),
    time_range__endswith__lte=target_end + timedelta(minutes=5),
    status='active',
)
# notify borrower + owner: 'warning_30' / 'warning_15'
```

### `notify()` dispatcher

```python
# notifications/dispatch.py
def notify(event, booking, **kwargs):
    owner    = booking.spot.owner
    borrower = booking.borrower

    OWNER_EVENTS    = {'booking_confirmed', 'booking_completed', 'warning_30',
                       'warning_15', 'booking_cancelled_by_borrower',
                       'booking_cancelled_by_owner', 'early_release_confirmed'}
    BORROWER_EVENTS = {'booking_confirmed', 'booking_starts', 'booking_completed',
                       'warning_30', 'warning_15', 'booking_cancelled_by_borrower',
                       'booking_cancelled_by_owner', 'early_release_confirmed'}

    if event in OWNER_EVENTS and owner:
        _send(owner, event, booking, **kwargs)
    if event in BORROWER_EVENTS and borrower:
        _send(borrower, event, booking, **kwargs)

def _send(user, event, booking, **kwargs):
    _send_email(user, event, booking, **kwargs)
    if user.notification_prefs.get('push') and user.push_subscriptions.exists():
        _send_push(user, event, booking)
```

### Email relay rate limiting

```python
# notifications/ratelimit.py
from parking.models import Booking
from notifications.models import RelayMessage

MAX_MESSAGES_PER_USER_PER_BOOKING = 10

def can_send_relay(from_user, booking):
    count = RelayMessage.objects.filter(
        from_user=from_user, booking=booking
    ).count()
    return count < MAX_MESSAGES_PER_USER_PER_BOOKING
```

---

## 11. HOA portal

All portal views live in `portal/views.py`. All require `@login_required` + `@hoa_admin_required` decorator:

```python
from functools import wraps

def hoa_admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if (not request.user.is_hoa_admin
                or request.user.organization != request.organization):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper
```

**PII-displaying views** (resident list, resident detail) must log a `pii_access` entry:
```python
AdminAuditLog.objects.create(
    organization=request.organization,
    actor=request.user,
    action='pii_access',
    target_type='user',
    target_id=resident.pk,
)
```

**Admin-cancel** must log `admin_cancel` and prompt for reason.

---

## 12. Operator console

Extends Django's default admin site. Superuser-only (`is_superuser=True`). Key customisations:

- `OrganizationAdmin` — all config fields editable; `launched_at` datepicker
- `UserAdmin` — cross-tenant; custom action "Impersonate user" sets session:
  ```python
  request.session['impersonating'] = user.pk
  request.session['real_operator'] = request.user.pk
  AdminAuditLog.objects.create(actor=request.user, action='impersonate_start',
                                target_type='user', target_id=user.pk)
  ```
- `AdminAuditLogAdmin` — read-only; no add/change/delete permissions

**Impersonation middleware** — checks session for `impersonating` key; overrides `request.user` with impersonated user; logs every POST to `AdminAuditLog`; banner template tag injected into base template.

---

## 13. Right-to-erasure

```python
# accounts/erasure.py
from django.db import transaction
from django.contrib.auth.hashers import make_password

def erase_user_pii(user, erased_by):
    with transaction.atomic():
        from parking.models import Booking
        from notifications.models import RelayMessage
        from django_otp.plugins.otp_totp.models import TOTPDevice

        user_pk = user.pk  # capture before any modification

        # Cancel any active/confirmed/tentative bookings before anonymising borrower FK
        active_booking_pks = list(
            Booking.objects.filter(
                borrower=user,
                status__in=['tentative', 'confirmed', 'active'],
            ).values_list('pk', flat=True)
        )
        if active_booking_pks:
            Booking.objects.filter(pk__in=active_booking_pks).update(
                status='cancelled_admin',
                cancel_reason='Account erased',
            )

        # Anonymise all bookings — remove borrower identity, preserve records
        Booking.objects.filter(borrower=user).update(
            borrower=None,
            is_anonymized=True,
        )

        # Scrub relay message bodies — preserve audit trail of message count
        RelayMessage.objects.filter(from_user=user).update(body='[erased]')
        RelayMessage.objects.filter(to_user=user).update(body='[erased]')

        # Revoke TOTP devices
        TOTPDevice.objects.filter(user=user).delete()

        # Revoke push subscriptions
        user.push_subscriptions.all().delete()

        # Revoke email OTPs
        user.email_otps.all().delete()

        # Scrub User PII fields
        user.email          = f'erased-{user_pk}@redacted.invalid'
        user.display_name   = '[Erased User]'
        user.phone          = None
        user.recovery_codes = []
        user.status         = 'blocked'
        user.set_unusable_password()
        user.save()

        # Log erasure
        AdminAuditLog.objects.create(
            organization=user.organization,
            actor=erased_by,
            action='pii_erasure',
            target_type='user',
            target_id=user_pk,
        )
```

---

## 14. Key settings

```python
# settings/base.py (partial)

AUTH_USER_MODEL = 'accounts.User'

PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.BCryptSHA256PasswordHasher',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'parkshare.middleware.TenantMiddleware',
    # ...
    'parkshare.middleware.ImpersonationMiddleware',
]

# django-encrypted-model-fields
FIELD_ENCRYPTION_KEY = env('PII_ENCRYPTION_KEY')

# django-anymail
ANYMAIL = {
    'BREVO_API_KEY': env('BREVO_API_KEY'),  # or other provider
}
EMAIL_BACKEND = env('EMAIL_BACKEND', default='anymail.backends.brevo.EmailBackend')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL')

# Web push (pywebpush)
VAPID_PRIVATE_KEY = env('VAPID_PRIVATE_KEY')
VAPID_PUBLIC_KEY  = env('VAPID_PUBLIC_KEY')
VAPID_ADMIN_EMAIL = env('VAPID_ADMIN_EMAIL')

# Security
SESSION_COOKIE_SECURE   = True
CSRF_COOKIE_SECURE      = True
SECURE_HSTS_SECONDS     = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
X_FRAME_OPTIONS         = 'DENY'
```

---

## 15. Migration notes

- Migration for `Booking` must include the `ExclusionConstraint` — verify it is present in the generated migration before applying.
- `AvailabilityWindow.time_range` and `Booking.time_range` are `DateTimeRangeField` — requires `django.contrib.postgres` in `INSTALLED_APPS`.
- GiST indexes on both `time_range` fields — included in model `Meta`; will be created by migration automatically.
- `User.email` uniqueness is `unique_together = [('organization', 'email')]` — not a `unique=True` field constraint — to support multi-tenant use of the same email address across organisations.
