"""
parking.views — Booking and owner spot-listing views.

All views require @active_required (login + status='active') unless
noted otherwise.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.timezone import now
from psycopg2.extras import DateTimeTZRange

from accounts.decorators import active_required
from notifications.dispatch import notify
from parking.booking import (
    assign_spot,
    cancel_booking,
    confirm_booking,
    release_booking,
)
from parking.forms import (
    AvailabilityWindowForm,
    AvailabilityWindowRemoveForm,
    BookingRequestForm,
    CancellationReasonForm,
    EarlyReleaseForm,
)
from parking.horizon import check_horizon_gate
from parking.models import AvailabilityWindow, Booking, ParkingSpot


def home(request):
    """
    Root landing (``/``). Authenticated residents go to their bookings;
    everyone else is sent to login. Deliberately undecorated — anonymous and
    not-yet-active users must be able to hit the root URL without a 404.
    """
    if request.user.is_authenticated:
        return redirect("booking_list")
    return redirect("login")


@active_required
def spot_list(request):
    """
    List all parking spots owned by the authenticated user in their organization.

    Shows spot_number, status, count of upcoming availability windows,
    and count of active bookings per spot.
    """
    from django.db.models import Count, Q

    now_dt = now()
    spots = (
        ParkingSpot.scoped.filter(owner=request.user)
        .annotate(
            upcoming_windows=Count(
                "availability_windows",
                filter=Q(availability_windows__time_range__endswith__gt=now_dt),
            ),
            active_bookings=Count(
                "bookings",
                filter=Q(bookings__status__in=["tentative", "confirmed", "active"]),
            ),
        )
        .order_by("spot_number")
    )

    return render(request, "parking/spot_list.html", {"spots": spots})


@active_required
def spot_availability(request, pk):
    """
    Show availability windows and upcoming bookings for a specific spot.

    Only the spot owner may view this page.
    """
    spot = get_object_or_404(ParkingSpot, pk=pk, organization=request.organization)
    if spot.owner != request.user:
        raise PermissionDenied

    now_dt = now()

    future_windows = spot.availability_windows.filter(
        time_range__endswith__gt=now_dt
    ).order_by("time_range")

    upcoming_bookings = spot.bookings.filter(
        status__in=["confirmed", "active"],
        time_range__endswith__gt=now_dt,
    ).order_by("time_range")

    context = {
        "spot": spot,
        "future_windows": future_windows,
        "upcoming_bookings": upcoming_bookings,
    }
    return render(request, "parking/spot_availability.html", context)


@active_required
def availability_add(request, pk):
    """
    Add an availability window to a spot.

    GET: render AvailabilityWindowForm (spot pre-selected to pk).
    POST valid: create AvailabilityWindow.
    HTMX requests receive a partial response on success.
    """
    spot = get_object_or_404(ParkingSpot, pk=pk, organization=request.organization)
    if spot.owner != request.user:
        raise PermissionDenied

    if request.method == "POST":
        form = AvailabilityWindowForm(
            request.POST, owner=request.user, org=request.organization
        )
        if form.is_valid():
            start = form.cleaned_data["start"]
            end = form.cleaned_data["end"]
            AvailabilityWindow.objects.create(
                organization=request.organization,
                spot=spot,
                time_range=DateTimeTZRange(start, end),
            )
            if request.headers.get("HX-Request"):
                future_windows = spot.availability_windows.filter(
                    time_range__endswith__gt=now()
                ).order_by("time_range")
                return render(
                    request,
                    "parking/partials/availability_windows.html",
                    {"spot": spot, "future_windows": future_windows},
                )
            return redirect("spot_availability", pk=spot.pk)
        else:
            if request.headers.get("HX-Request"):
                return render(
                    request,
                    "parking/partials/availability_form_errors.html",
                    {"form": form},
                    status=422,
                )
    else:
        # Pre-select this spot in the form
        form = AvailabilityWindowForm(
            initial={"spot": spot},
            owner=request.user,
            org=request.organization,
        )

    context = {"spot": spot, "form": form}
    return render(request, "parking/availability_add.html", context)


@active_required
def availability_remove(request, pk, wk):
    """
    Remove an availability window from a spot.

    Verifies the authenticated user owns the spot, then checks that no
    active or confirmed bookings overlap the window before deleting.
    Only responds to POST (AvailabilityWindowRemoveForm confirmation).
    """
    spot = get_object_or_404(ParkingSpot, pk=pk, organization=request.organization)
    if spot.owner != request.user:
        raise PermissionDenied

    window = get_object_or_404(AvailabilityWindow, pk=wk, spot=spot)

    if request.method == "POST":
        form = AvailabilityWindowRemoveForm(request.POST)
        if form.is_valid():
            # Guard: refuse to delete if active/confirmed bookings overlap this window
            overlapping = Booking.objects.filter(
                spot=spot,
                status__in=["tentative", "confirmed", "active"],
                time_range__overlap=window.time_range,
            ).exists()

            if overlapping:
                error_msg = (
                    "This availability window cannot be removed because "
                    "it has active or confirmed bookings."
                )
                if request.headers.get("HX-Request"):
                    return render(
                        request,
                        "parking/partials/availability_remove_error.html",
                        {"error": error_msg},
                        status=422,
                    )
                context = {
                    "spot": spot,
                    "window": window,
                    "form": form,
                    "error": error_msg,
                }
                return render(request, "parking/availability_remove.html", context)

            window.delete()
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response["HX-Redirect"] = request.build_absolute_uri(
                    redirect("spot_availability", pk=spot.pk).url
                )
                return response
            return redirect("spot_availability", pk=spot.pk)
    else:
        form = AvailabilityWindowRemoveForm()

    context = {"spot": spot, "window": window, "form": form}
    return render(request, "parking/availability_remove.html", context)


# ---------------------------------------------------------------------------
# Booking views
# ---------------------------------------------------------------------------


def _htmx_form_error(request, template, context, status=422):
    """Return a partial on HTMX requests; full page otherwise."""
    if request.headers.get("HX-Request"):
        return render(request, template, context, status=status)
    return render(request, template, context)


@active_required
def book_request(request):
    """GET/POST — Request a parking spot.

    GET: render the time-window form.
    POST:
      1. Validate BookingRequestForm.
      2. Gate 1 — Horizon: requested start must be within earned horizon.
      3. Gate 2 — One active booking: borrower must not already have one.
      4. Gate 3 — Assign: find and tentatively hold a spot.
      5. Store booking.pk in session, redirect to book_confirm.
    """
    if request.method == "POST":
        form = BookingRequestForm(request.POST, org=request.organization)
        if form.is_valid():
            start = form.cleaned_data["start"]
            end = form.cleaned_data["end"]

            # Gate 1 — Horizon
            if not check_horizon_gate(request.user, start):
                form.add_error(
                    None,
                    "You can't book that far ahead yet. List your spot to earn more.",
                )
            else:
                # Gate 2 (one active booking) and Gate 3 (assign) are both
                # enforced atomically inside assign_spot to prevent race
                # conditions where two concurrent requests bypass Gate 2.
                booking = assign_spot(request.organization, request.user, start, end)
                if booking == "already_active":
                    form.add_error(None, "You already have an active booking.")
                elif booking is None:
                    form.add_error(
                        None,
                        "No spots are available for that window. Try a different time.",
                    )
                else:
                    request.session["pending_booking_pk"] = booking.pk
                    if request.headers.get("HX-Request"):
                        return render(
                            request,
                            "parking/partials/book_confirm_redirect.html",
                            {"booking": booking},
                        )
                    return redirect("book_confirm")
    else:
        form = BookingRequestForm(org=request.organization)

    context = {"form": form}
    if request.headers.get("HX-Request") and request.method == "POST":
        return render(
            request, "parking/partials/book_request_form.html", context, status=422
        )
    return render(request, "parking/book_request.html", context)


@active_required
def book_confirm(request):
    """GET/POST — Review and confirm a tentative booking.

    The tentative booking pk is stored in the session (set by book_request).
    The hold expires after 5 minutes; an expired hold is rejected.

    GET: show assigned spot number, time range, and expiry notice.
    POST: promote booking from tentative → confirmed, notify owner.
    """
    pk = request.session.get("pending_booking_pk")
    if not pk:
        return redirect("book_request")

    booking = get_object_or_404(Booking.scoped, pk=pk, borrower=request.user)

    # Validate the tentative hold is still live
    if booking.status != "tentative":
        messages.error(request, "That booking is no longer available.")
        return redirect("book_request")

    now_dt = now()
    if booking.tentative_expires_at and booking.tentative_expires_at < now_dt:
        booking.status = "cancelled_admin"
        booking.save()
        del request.session["pending_booking_pk"]
        messages.error(request, "Your 5-minute hold expired. Please try again.")
        return redirect("book_request")

    if request.method == "POST":
        try:
            confirm_booking(booking, request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
            return redirect("book_request")

        # Notify the spot owner
        notify("booking_confirmed", booking)

        del request.session["pending_booking_pk"]
        return redirect("booking_detail", pk=booking.pk)

    context = {"booking": booking}
    return render(request, "parking/book_confirm.html", context)


@active_required
def booking_list(request):
    """List the authenticated user's bookings (active + recent)."""
    bookings = (
        Booking.scoped.filter(borrower=request.user)
        .select_related("spot")
        .order_by("-time_range")
    )
    return render(request, "parking/booking_list.html", {"bookings": bookings})


@active_required
def booking_detail(request, pk):
    """Show detail for a single booking.

    Accessible by both the borrower and the spot owner.
    """
    booking = get_object_or_404(Booking.scoped, pk=pk)
    is_borrower = booking.borrower == request.user
    is_owner = booking.spot.owner == request.user
    if not (is_borrower or is_owner):
        raise PermissionDenied

    context = {
        "booking": booking,
        "is_borrower": is_borrower,
        "is_owner": is_owner,
    }
    return render(request, "parking/booking_detail.html", context)


@active_required
def booking_cancel(request, pk):
    """POST — Cancel a booking.

    Only the borrower or the spot owner may cancel.  If the booking's
    borrower has been erased (borrower=None) only the owner may cancel.

    If the owner cancels they are shown an optional CancellationReasonForm;
    the reason is stored on the booking.
    """
    booking = get_object_or_404(Booking.scoped, pk=pk)

    is_owner = booking.spot.owner == request.user
    is_borrower = (booking.borrower is not None) and (booking.borrower == request.user)

    if not (is_owner or is_borrower):
        raise PermissionDenied

    # Refuse if booking is already in a terminal state
    terminal = {"cancelled_borrower", "cancelled_owner", "cancelled_admin", "completed"}
    if booking.status in terminal:
        messages.error(request, "This booking has already been cancelled or completed.")
        return redirect("booking_detail", pk=booking.pk)

    if request.method == "POST":
        if is_owner:
            form = CancellationReasonForm(request.POST)
            if not form.is_valid():
                context = {"booking": booking, "form": form, "is_owner": is_owner}
                return render(request, "parking/booking_cancel.html", context)
            reason = form.cleaned_data["reason"]
        else:
            form = CancellationReasonForm()
            reason = ""

        booking.cancel_reason = reason
        cancel_booking(booking, request.user)
        return redirect("booking_list")

    # GET — show confirmation page with optional reason form (owner only)
    form = CancellationReasonForm() if is_owner else None
    context = {"booking": booking, "form": form, "is_owner": is_owner}
    return render(request, "parking/booking_cancel.html", context)


@active_required
def booking_release(request, pk):
    """GET/POST — Early release: shorten booking end to an hour boundary.

    Only the borrower may release.  The release time must be:
    - on the hour
    - strictly in the future
    - strictly before the current booking end
    """
    booking = get_object_or_404(Booking.scoped, pk=pk)
    if booking.borrower != request.user:
        raise PermissionDenied

    if booking.status not in ("confirmed", "active"):
        messages.error(request, "This booking cannot be released in its current state.")
        return redirect("booking_detail", pk=booking.pk)

    if request.method == "POST":
        form = EarlyReleaseForm(request.POST, booking=booking)
        if form.is_valid():
            release_to = form.cleaned_data["release_to"]
            try:
                release_booking(booking, request.user, release_to)
            except (PermissionError, ValueError) as exc:
                form.add_error(None, str(exc))
            else:
                return redirect("booking_detail", pk=booking.pk)
    else:
        form = EarlyReleaseForm(booking=booking)

    context = {"booking": booking, "form": form}
    return render(request, "parking/booking_release.html", context)
