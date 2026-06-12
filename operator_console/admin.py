"""
operator_console.admin — Operator console (Django admin extensions).

Superuser-only.  All ModelAdmin classes use unscoped managers (.objects)
so that cross-tenant data is accessible in the operator console.

Registers:
  OrganizationAdmin — full config editing
  UserAdmin         — cross-tenant; impersonate action; pii_erasure action
  AdminAuditLogAdmin — read-only audit log
  BookingAdmin       — list/cancel across all orgs

The pii_erasure action and impersonation action both write AdminAuditLog
entries so that operator activity is fully auditable.
"""

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.shortcuts import redirect

from accounts.models import AdminAuditLog
from parkshare.admin_site import operator_admin_site
from parking.models import Organization, ParkingSpot, AvailabilityWindow, Booking

User = get_user_model()


# ---------------------------------------------------------------------------
# OrganizationAdmin
# ---------------------------------------------------------------------------

@admin.register(Organization, site=operator_admin_site)
class OrganizationAdmin(admin.ModelAdmin):
    """
    Operator console: full read-write access to all Organisation config fields.
    Uses Organization.objects (unscoped) — the operator console queries all
    tenants directly.
    """
    list_display = [
        'name', 'hostname', 'timezone', 'registration_mode',
        'payer_model', 'unit_count', 'launched_at', 'created_at',
    ]
    list_filter = ['registration_mode', 'payer_model']
    search_fields = ['name', 'hostname', 'support_email']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = [
        (None, {
            'fields': ['name', 'hostname', 'timezone', 'support_email'],
        }),
        ('Registration', {
            'fields': ['registration_mode', 'unit_count', 'payer_model', 'launched_at'],
        }),
        ('Booking config', {
            'fields': [
                'booking_buffer_hours',
                'max_concurrent_bookings',
                'max_booking_hours',
            ],
        }),
        ('Horizon config', {
            'fields': [
                'booking_horizon_baseline_days',
                'booking_horizon_max_days',
                'listing_to_horizon_ratio',
                'tier_metric_window_days',
                'launch_grace_days',
                'launch_grace_horizon_days',
            ],
        }),
        ('Timestamps', {
            'fields': ['created_at', 'updated_at'],
            'classes': ['collapse'],
        }),
    ]


# ---------------------------------------------------------------------------
# Bulk actions for UserAdmin
# ---------------------------------------------------------------------------

@admin.action(description='Erase PII (GDPR)')
def pii_erasure(modeladmin, request, queryset):
    """
    Admin action: erase PII for exactly one selected user.

    Enforces single-user selection to prevent accidental bulk erasure.
    Calls ``erase_user_pii(user, erased_by)`` which:
      - Cancels active bookings, anonymises all bookings
      - Scrubs RelayMessage bodies
      - Deletes TOTP devices, push subscriptions, and email OTPs
      - Overwrites User PII fields and sets status='blocked'
      - Writes an AdminAuditLog entry inside the same atomic transaction

    The action is idempotent: running it twice on the same user is safe.
    """
    if queryset.count() != 1:
        modeladmin.message_user(
            request,
            'Select exactly one user to erase.',
            level=messages.ERROR,
        )
        return

    from accounts.erasure import erase_user_pii

    user = queryset.first()
    erase_user_pii(user, erased_by=request.user)

    modeladmin.message_user(
        request,
        f'PII erased for user {user.pk}.',
        messages.SUCCESS,
    )


# ---------------------------------------------------------------------------
# UserAdmin
# ---------------------------------------------------------------------------

@admin.register(User, site=operator_admin_site)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        'email', 'display_name', 'organization', 'status',
        'is_hoa_admin', 'is_staff', 'is_active', 'created_at',
    ]
    list_filter = ['status', 'is_hoa_admin', 'is_staff', 'is_active', 'organization']
    search_fields = ['email', 'display_name']
    readonly_fields = ['created_at', 'updated_at', 'last_booking_at']
    actions = [pii_erasure, 'impersonate_user']

    fieldsets = [
        (None, {
            'fields': ['email', 'display_name', 'organization', 'status'],
        }),
        ('Permissions', {
            'fields': ['is_hoa_admin', 'is_staff', 'is_active', 'is_superuser',
                       'groups', 'user_permissions'],
        }),
        ('Preferences', {
            'fields': ['phone', 'notification_prefs', 'marketing_email_opted_in'],
        }),
        ('Timestamps', {
            'fields': ['created_at', 'updated_at', 'last_booking_at'],
            'classes': ['collapse'],
        }),
    ]

    def impersonate_user(self, request, queryset):
        """
        Begin impersonating a selected user.

        Stores the impersonated user's PK in the session under 'impersonating'
        and the real operator's PK under 'real_operator', then logs an
        ``impersonate_start`` audit entry and redirects to the site root.

        Constraints:
        - Exactly one user must be selected.
        - The target user must not be a superuser.
        """
        if queryset.count() != 1:
            self.message_user(
                request,
                'Select exactly one user to impersonate.',
                level=messages.ERROR,
            )
            return
        user = queryset.first()
        if user.is_superuser:
            self.message_user(
                request,
                'Cannot impersonate a superuser.',
                level=messages.ERROR,
            )
            return

        request.session['impersonating'] = user.pk
        request.session['real_operator'] = request.user.pk

        AdminAuditLog.objects.create(
            organization=user.organization,
            actor=request.user,
            action='impersonate_start',
            target_type='user',
            target_id=user.pk,
        )
        return redirect('/')

    impersonate_user.short_description = 'Impersonate this user'


# ---------------------------------------------------------------------------
# AdminAuditLogAdmin (read-only)
# ---------------------------------------------------------------------------

@admin.register(AdminAuditLog, site=operator_admin_site)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = [
        'created_at', 'actor', 'action', 'target_type', 'target_id', 'organization',
    ]
    list_filter = ['action', 'organization']
    search_fields = ['actor__email', 'notes']
    readonly_fields = [
        f.name for f in AdminAuditLog._meta.get_fields()
        if hasattr(f, 'column') or f.__class__.__name__ in (
            'AutoField', 'BigAutoField', 'ForeignKey', 'DateTimeField',
            'CharField', 'TextField', 'PositiveIntegerField',
        )
    ]

    def get_readonly_fields(self, request, obj=None):
        """Make every field on the model read-only."""
        return [f.name for f in self._meta.model._meta.get_fields()
                if hasattr(f, 'name')]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# BookingAdmin
# ---------------------------------------------------------------------------

@admin.register(Booking, site=operator_admin_site)
class BookingAdmin(admin.ModelAdmin):
    list_display = ['pk', 'spot', 'borrower', 'status', 'time_range', 'organization']
    list_filter = ['organization', 'status']
    search_fields = ['spot__spot_number', 'borrower__email']
    readonly_fields = ['created_at', 'updated_at']
    actions = ['admin_cancel_booking']

    def admin_cancel_booking(self, request, queryset):
        """
        Cancel selected bookings that are in an active/held state.

        Logs each cancellation to AdminAuditLog.
        """
        cancelled_count = 0
        for booking in queryset.filter(
            status__in=['tentative', 'confirmed', 'active']
        ):
            booking.status = 'cancelled_admin'
            booking.save(update_fields=['status', 'updated_at'])
            AdminAuditLog.objects.create(
                organization=booking.organization,
                actor=request.user,
                action='admin_cancel',
                target_type='booking',
                target_id=booking.pk,
            )
            cancelled_count += 1

        self.message_user(
            request,
            f'{cancelled_count} booking(s) cancelled.',
            messages.SUCCESS,
        )

    admin_cancel_booking.short_description = 'Admin cancel selected bookings'
