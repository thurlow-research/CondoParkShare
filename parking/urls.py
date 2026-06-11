"""
parking.urls — URL patterns for parking spot listing and availability management.

Included in parkshare/urls.py at the root prefix.
"""

from django.urls import path

from parking import views

urlpatterns = [
    # Owner spot listing
    path('spots/', views.spot_list, name='spot_list'),
    path('spots/<int:pk>/availability/', views.spot_availability, name='spot_availability'),
    path('spots/<int:pk>/availability/add/', views.availability_add, name='availability_add'),
    path(
        'spots/<int:pk>/windows/<int:wk>/remove/',
        views.availability_remove,
        name='availability_remove',
    ),
]
