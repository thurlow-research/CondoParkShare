"""
portal.views — HOA portal views.

All views require @login_required + @hoa_admin_required.
@hoa_admin_required checks both is_hoa_admin and organization membership.

PII-displaying views log a 'pii_access' AdminAuditLog entry on every access.
"""

import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import hoa_admin_required
from accounts.models import AdminAuditLog, Invite
from notifications.dispatch import notify
from parking.models import Booking, ParkingSpot
from portal.forms import InviteCreateForm

User = get_user_model()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _log(request, action, target_type="", target_id=None, notes=""):
    """Convenience wrapper around AdminAuditLog.log for portal views."""
    AdminAuditLog.log(
        actor=request.user,
        action=action,
        organization=request.organization,
        target_type=target_type,
        target_id=target_id,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@login_required
@hoa_admin_required
def portal_home(request):
    """
    HOA portal dashboard.

    Shows:
    - Count of users pending approval (status='pending_approval')
    - Count of active bookings
    - Count of pending spots
    - 20 most recent AdminAuditLog entries for the organisation
    """
    org = request.organization

    pending_approvals = User.objects.filter(
        organization=org,
        status="pending_approval",
    ).count()

    active_bookings = Booking.objects.filter(
        organization=org,
        status__in=["tentative", "confirmed", "active"],
    ).count()

    pending_spots = ParkingSpot.objects.filter(
        organization=org,
        status="pending",
    ).count()

    recent_audit = (
        AdminAuditLog.objects.filter(
            organization=org,
        )
        .select_related("actor", "on_behalf_of")
        .order_by("-created_at")[:20]
    )

    return render(
        request,
        "portal/home.html",
        {
            "pending_approvals": pending_approvals,
            "active_bookings": active_bookings,
            "pending_spots": pending_spots,
            "recent_audit": recent_audit,
        },
    )


# ---------------------------------------------------------------------------
# Resident management
# ---------------------------------------------------------------------------


@login_required
@hoa_admin_required
def resident_list(request):
    """
    List all users in the current organisation.

    Logs a 'pii_access' audit entry on every access (bulk).
    """
    org = request.organization

    # PII audit log — bulk list view; target_id records the requesting admin's pk
    _log(
        request,
        "pii_access",
        target_type="user",
        target_id=request.user.pk,
        notes="bulk_list",
    )

    residents = User.objects.filter(
        organization=org,
    ).order_by("status", "display_name")

    return render(
        request,
        "portal/resident_list.html",
        {
            "residents": residents,
        },
    )


@login_required
@hoa_admin_required
def resident_detail(request, pk):
    """
    Display the full profile of a single resident within the current organisation.

    Logs a 'pii_access' audit entry with target_id=resident.pk on every access,
    as required by TECHNICAL-DESIGN.md §11.
    """
    org = request.organization
    user = get_object_or_404(User, pk=pk, organization=org)

    _log(request, "pii_access", target_type="user", target_id=user.pk)

    return render(request, "portal/resident_detail.html", {"resident": user})


@login_required
@hoa_admin_required
def resident_approve(request, pk):
    """
    Approve a user whose status is 'pending_approval'.

    GET: confirmation page.
    POST: set status='active'; log 'approve_user'; redirect to resident list.
    """
    org = request.organization
    user = get_object_or_404(User, pk=pk, organization=org)

    if user.status != "pending_approval":
        return redirect("portal_resident_list")

    if request.method == "POST":
        user.status = "active"
        user.save(update_fields=["status", "updated_at"])
        _log(request, "approve_user", target_type="user", target_id=user.pk)
        return redirect("portal_resident_list")

    return render(request, "portal/resident_approve.html", {"resident": user})


@login_required
@hoa_admin_required
def resident_block(request, pk):
    """
    Block a user.

    GET: confirmation page.
    POST: set status='blocked'; log 'block'.
    """
    org = request.organization
    user = get_object_or_404(User, pk=pk, organization=org)

    if request.method == "POST":
        user.status = "blocked"
        user.save(update_fields=["status", "updated_at"])
        _log(request, "block", target_type="user", target_id=user.pk)
        return redirect("portal_resident_list")

    return render(request, "portal/resident_block.html", {"resident": user})


@login_required
@hoa_admin_required
def resident_unblock(request, pk):
    """
    Unblock a blocked user.

    GET: confirmation page.
    POST: set status='active'; log 'unblock'.
    """
    org = request.organization
    user = get_object_or_404(User, pk=pk, organization=org)

    if user.status != "blocked":
        return redirect("portal_resident_list")

    if request.method == "POST":
        user.status = "active"
        user.save(update_fields=["status", "updated_at"])
        _log(request, "unblock", target_type="user", target_id=user.pk)
        return redirect("portal_resident_list")

    return render(request, "portal/resident_unblock.html", {"resident": user})


# ---------------------------------------------------------------------------
# Spot management
# ---------------------------------------------------------------------------


@login_required
@hoa_admin_required
def spot_list(request):
    """List all parking spots in the current organisation with their status."""
    org = request.organization
    spots = (
        ParkingSpot.objects.filter(
            organization=org,
        )
        .select_related("owner")
        .order_by("status", "spot_number")
    )

    return render(request, "portal/spot_list.html", {"spots": spots})


@login_required
@hoa_admin_required
@require_POST
def spot_approve(request, pk):
    """Approve a pending spot; log 'approve_spot'."""
    org = request.organization
    spot = get_object_or_404(ParkingSpot, pk=pk, organization=org)
    spot.status = "active"
    spot.save(update_fields=["status", "updated_at"])
    _log(request, "approve_spot", target_type="spot", target_id=spot.pk)
    return redirect("portal_spot_list")


@login_required
@hoa_admin_required
@require_POST
def spot_deactivate(request, pk):
    """Deactivate an active spot; log 'deactivate_spot'."""
    org = request.organization
    spot = get_object_or_404(ParkingSpot, pk=pk, organization=org)
    spot.status = "inactive"
    spot.save(update_fields=["status", "updated_at"])
    _log(request, "deactivate_spot", target_type="spot", target_id=spot.pk)
    return redirect("portal_spot_list")


# ---------------------------------------------------------------------------
# Invite management
# ---------------------------------------------------------------------------


@login_required
@hoa_admin_required
def invite_list(request):
    """List all invites for the current organisation."""
    org = request.organization
    invites = (
        Invite.objects.filter(organization=org)
        .select_related("issued_by", "consumed_by")
        .order_by("-created_at")
    )
    return render(request, "portal/invite_list.html", {"invites": invites})


@login_required
@hoa_admin_required
def invite_create(request):
    """
    Create a new invite for the current organisation.

    GET:  Render InviteCreateForm.
    POST: Validate and create Invite with a securely random code; redirect to
          invite list.
    """
    if request.method == "POST":
        form = InviteCreateForm(request.POST)
        if form.is_valid():
            Invite.objects.create(
                organization=request.organization,
                issued_by=request.user,
                code=secrets.token_urlsafe(32),
                unit_number=form.cleaned_data.get("unit_number", ""),
                expires_at=form.cleaned_data.get("expires_at"),
                max_uses=form.cleaned_data.get("max_uses", 1),
            )
            return redirect("portal_invite_list")
    else:
        form = InviteCreateForm(initial={"max_uses": 1})

    return render(request, "portal/invite_create.html", {"form": form})


# ---------------------------------------------------------------------------
# Booking management
# ---------------------------------------------------------------------------


@login_required
@hoa_admin_required
def portal_bookings(request):
    """List all bookings for the current organisation."""
    org = request.organization
    bookings = (
        Booking.objects.filter(
            organization=org,
        )
        .select_related("spot", "borrower", "spot__owner")
        .order_by("-created_at")
    )

    return render(request, "portal/bookings.html", {"bookings": bookings})


@login_required
@hoa_admin_required
@require_POST
def portal_booking_cancel(request, pk):
    """
    Admin-cancel a booking.

    Sets status to 'cancelled_admin', logs 'admin_cancel', and notifies both
    the owner and borrower.
    """
    org = request.organization
    booking = get_object_or_404(Booking, pk=pk, organization=org)

    if booking.status in ("tentative", "confirmed", "active"):
        booking.status = "cancelled_admin"
        booking.cancel_reason = request.POST.get(
            "cancel_reason", "Cancelled by HOA admin"
        )
        booking.save(update_fields=["status", "cancel_reason", "updated_at"])

        _log(
            request,
            "admin_cancel",
            target_type="booking",
            target_id=booking.pk,
            notes=booking.cancel_reason,
        )

        # Notify both parties (fail-safe: catch all errors so the cancel
        # still commits even if email delivery fails)
        try:
            notify("booking_cancelled_by_owner", booking)
        except Exception:
            pass

    return redirect("portal_bookings")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@login_required
@hoa_admin_required
def portal_reports(request):
    """
    Aggregate statistics for the current organisation.

    Provides:
    - Total bookings (all statuses)
    - Bookings per spot (demand signal): booking count and total hours booked
    """
    org = request.organization

    total_bookings = Booking.objects.filter(organization=org).count()

    # Per-spot aggregate: booking count
    # (Booking.time_range is a DateTimeRangeField; duration queries require
    # database support.  We compute a simple count here; hours can be
    # computed client-side or via a raw annotation if needed.)
    spot_stats = (
        ParkingSpot.objects.filter(organization=org)
        .annotate(booking_count=Count("bookings"))
        .order_by("-booking_count")
        .select_related("owner")
    )

    return render(
        request,
        "portal/reports.html",
        {
            "total_bookings": total_bookings,
            "spot_stats": spot_stats,
        },
    )
