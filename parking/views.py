"""
parking.views — Owner spot listing and availability management views.

All views require @active_required (login + status='active').
"""

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.timezone import now

from psycopg2.extras import DateTimeTZRange

from accounts.decorators import active_required
from parking.forms import AvailabilityWindowForm, AvailabilityWindowRemoveForm
from parking.models import AvailabilityWindow, Booking, ParkingSpot


@active_required
def spot_list(request):
    """
    List all parking spots owned by the authenticated user in their organization.

    Shows spot_number, status, count of upcoming availability windows,
    and count of active bookings per spot.
    """
    spots = (
        ParkingSpot.objects
        .filter(owner=request.user, organization=request.organization)
        .order_by('spot_number')
    )

    now_dt = now()
    spot_data = []
    for spot in spots:
        upcoming_windows = spot.availability_windows.filter(
            time_range__endswith__gt=now_dt
        ).count()
        active_bookings = spot.bookings.filter(
            status__in=['tentative', 'confirmed', 'active']
        ).count()
        spot_data.append({
            'spot': spot,
            'upcoming_windows': upcoming_windows,
            'active_bookings': active_bookings,
        })

    return render(request, 'parking/spot_list.html', {'spot_data': spot_data})


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
    ).order_by('time_range')

    upcoming_bookings = spot.bookings.filter(
        status__in=['confirmed', 'active'],
        time_range__endswith__gt=now_dt,
    ).order_by('time_range')

    context = {
        'spot': spot,
        'future_windows': future_windows,
        'upcoming_bookings': upcoming_bookings,
    }
    return render(request, 'parking/spot_availability.html', context)


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

    if request.method == 'POST':
        form = AvailabilityWindowForm(request.POST, owner=request.user)
        if form.is_valid():
            start = form.cleaned_data['start']
            end = form.cleaned_data['end']
            AvailabilityWindow.objects.create(
                organization=request.organization,
                spot=spot,
                time_range=DateTimeTZRange(start, end),
            )
            if request.headers.get('HX-Request'):
                future_windows = spot.availability_windows.filter(
                    time_range__endswith__gt=now()
                ).order_by('time_range')
                return render(
                    request,
                    'parking/partials/availability_windows.html',
                    {'spot': spot, 'future_windows': future_windows},
                )
            return redirect('spot_availability', pk=spot.pk)
        else:
            if request.headers.get('HX-Request'):
                return render(
                    request,
                    'parking/partials/availability_form_errors.html',
                    {'form': form},
                    status=422,
                )
    else:
        # Pre-select this spot in the form
        form = AvailabilityWindowForm(
            initial={'spot': spot},
            owner=request.user,
        )

    context = {'spot': spot, 'form': form}
    return render(request, 'parking/availability_add.html', context)


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

    if request.method == 'POST':
        form = AvailabilityWindowRemoveForm(request.POST)
        if form.is_valid():
            # Guard: refuse to delete if active/confirmed bookings overlap this window
            overlapping = Booking.objects.filter(
                spot=spot,
                status__in=['tentative', 'confirmed', 'active'],
                time_range__overlap=window.time_range,
            ).exists()

            if overlapping:
                error_msg = (
                    'This availability window cannot be removed because '
                    'it has active or confirmed bookings.'
                )
                if request.headers.get('HX-Request'):
                    return render(
                        request,
                        'parking/partials/availability_remove_error.html',
                        {'error': error_msg},
                        status=422,
                    )
                context = {
                    'spot': spot,
                    'window': window,
                    'form': form,
                    'error': error_msg,
                }
                return render(request, 'parking/availability_remove.html', context)

            window.delete()
            if request.headers.get('HX-Request'):
                response = HttpResponse(status=204)
                response['HX-Redirect'] = request.build_absolute_uri(
                    redirect('spot_availability', pk=spot.pk).url
                )
                return response
            return redirect('spot_availability', pk=spot.pk)
    else:
        form = AvailabilityWindowRemoveForm()

    context = {'spot': spot, 'window': window, 'form': form}
    return render(request, 'parking/availability_remove.html', context)
