from django.contrib import admin

from parking.models import Organization, ParkingSpot, AvailabilityWindow, Booking


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    """
    Operator console: full read-write access to all Organisation config fields.
    Uses Organization.objects (unscoped) — the operator console queries all tenants directly.
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
