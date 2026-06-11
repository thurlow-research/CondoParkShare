"""
portal.urls — HOA portal URL patterns.

All URLs are prefixed with /portal/ via parkshare/urls.py.
All views require @login_required + @hoa_admin_required.
"""

from django.urls import path

from portal import views

urlpatterns = [
    # Dashboard
    path('', views.portal_home, name='portal_home'),

    # Resident management
    path('residents/', views.resident_list, name='portal_resident_list'),
    path('residents/<int:pk>/', views.resident_detail, name='portal_resident_detail'),
    path('residents/<int:pk>/approve/', views.resident_approve, name='portal_resident_approve'),
    path('residents/<int:pk>/block/', views.resident_block, name='portal_resident_block'),
    path('residents/<int:pk>/unblock/', views.resident_unblock, name='portal_resident_unblock'),

    # Spot management
    path('spots/', views.spot_list, name='portal_spot_list'),
    path('spots/<int:pk>/approve/', views.spot_approve, name='portal_spot_approve'),
    path('spots/<int:pk>/deactivate/', views.spot_deactivate, name='portal_spot_deactivate'),

    # Invite management
    path('invites/', views.invite_list, name='portal_invite_list'),
    path('invites/create/', views.invite_create, name='portal_invite_create'),

    # Booking management
    path('bookings/', views.portal_bookings, name='portal_bookings'),
    path('bookings/<int:pk>/cancel/', views.portal_booking_cancel, name='portal_booking_cancel'),

    # Reports
    path('reports/', views.portal_reports, name='portal_reports'),
]
