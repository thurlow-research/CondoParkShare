"""
parkshare.admin_site — Custom AdminSite that restricts access to superusers.

Defined in this neutral module so both ``operator_console/admin.py`` and
``parkshare/urls.py`` can import the shared singleton without a circular
dependency. (Historically this also dodged a stdlib name clash from the app's
former name ``operator``; the app has since been renamed ``operator_console``.)
"""

from django.contrib import admin


class SuperuserAdminSite(admin.AdminSite):
    """
    AdminSite subclass that gates access on ``is_superuser=True``.

    Django's default AdminSite.has_permission only requires ``is_active`` and
    ``is_staff``, which would allow any staff account to access the operator
    console and its cross-tenant data.  TECHNICAL-DESIGN.md §12 requires the
    operator console to be superuser-only.
    """

    site_header = "CondoParkShare Operator Console"
    site_title = "CondoParkShare Operator"
    index_title = "Operator Console"

    def has_permission(self, request):
        return super().has_permission(request) and request.user.is_superuser


# Singleton instance used by operator_console/admin.py and parkshare/urls.py.
operator_admin_site = SuperuserAdminSite(name="operator_admin")
