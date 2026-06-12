"""
parkshare.admin_site — Custom AdminSite that restricts access to superusers.

Defined here (rather than in operator/admin.py) because the Django app named
'operator' shadows Python's stdlib ``operator`` module, making direct imports
of ``operator.admin`` impossible from outside that package.  Both
``operator/admin.py`` and ``parkshare/urls.py`` import from this module.
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

    site_header = 'CondoParkShare Operator Console'
    site_title = 'CondoParkShare Operator'
    index_title = 'Operator Console'

    def has_permission(self, request):
        return (
            super().has_permission(request)
            and request.user.is_superuser
        )


# Singleton instance used by operator/admin.py and parkshare/urls.py.
operator_admin_site = SuperuserAdminSite(name='operator_admin')
