"""
parkshare.admin_site — Custom AdminSite that restricts access to superusers.

Defined in this neutral module so both ``operator_console/admin.py`` and
``parkshare/urls.py`` can import the shared singleton without a circular
dependency. (Historically this also dodged a stdlib name clash from the app's
former name ``operator``; the app has since been renamed ``operator_console``.)
"""

from django.contrib import admin
from django.shortcuts import redirect


class SuperuserAdminSite(admin.AdminSite):
    """
    AdminSite subclass that gates access on ``is_superuser=True`` AND TOTP
    verification.

    Django's default AdminSite.has_permission only requires ``is_active`` and
    ``is_staff``, which would allow any staff account to access the operator
    console and its cross-tenant data.  TECHNICAL-DESIGN.md §12 requires the
    operator console to be superuser-only AND OTP-verified — a password alone
    must not grant access to cross-tenant data or PII erasure.
    """

    site_header = "CondoParkShare Operator Console"
    site_title = "CondoParkShare Operator"
    index_title = "Operator Console"

    def has_permission(self, request):
        # OTPMiddleware installs is_verified() on request.user; require both
        # superuser status and a verified OTP device so that a stolen password
        # alone cannot access the operator console.
        return (
            super().has_permission(request)
            and request.user.is_superuser
            and request.user.is_verified()
        )

    def login(self, request, extra_context=None):
        # If the user is already authenticated as a superuser but has not
        # completed OTP, redirect to TOTP enrollment rather than showing the
        # admin login form (which would be confusing and unhelpful).
        if (
            request.user.is_authenticated
            and request.user.is_superuser
            and not request.user.is_verified()
        ):
            return redirect("totp_enroll")
        return super().login(request, extra_context=extra_context)


# Singleton instance used by operator_console/admin.py and parkshare/urls.py.
operator_admin_site = SuperuserAdminSite(name="operator_admin")
