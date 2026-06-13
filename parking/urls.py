"""
parking.urls — URL patterns for bookings and owner spot management.

Included in parkshare/urls.py at the root prefix.
"""

from django.urls import path

from parking import views

urlpatterns = [
    path("", views.home, name="home"),
    # Booking (resident)
    path("book/", views.book_request, name="book_request"),
    path("book/confirm/", views.book_confirm, name="book_confirm"),
    path("bookings/", views.booking_list, name="booking_list"),
    path("bookings/<int:pk>/", views.booking_detail, name="booking_detail"),
    path("bookings/<int:pk>/cancel/", views.booking_cancel, name="booking_cancel"),
    path("bookings/<int:pk>/release/", views.booking_release, name="booking_release"),
    # Owner spot listing
    path("spots/", views.spot_list, name="spot_list"),
    path(
        "spots/<int:pk>/availability/",
        views.spot_availability,
        name="spot_availability",
    ),
    path(
        "spots/<int:pk>/availability/add/",
        views.availability_add,
        name="availability_add",
    ),
    path(
        "spots/<int:pk>/windows/<int:wk>/remove/",
        views.availability_remove,
        name="availability_remove",
    ),
]
