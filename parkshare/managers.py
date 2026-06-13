"""
parkshare.managers — OrganizationScopedManager.

Used as the 'scoped' manager on every model that carries an organization FK.
Returns an empty queryset when there is no current organization (e.g. in
management commands) so that a developer is forced to use .objects explicitly
rather than accidentally leaking cross-tenant data.
"""

from django.db import models

from parkshare.middleware import get_current_organization


class OrganizationScopedManager(models.Manager):
    """
    ORM manager that automatically filters to the current request's Organization.

    Usage in models:
        objects = models.Manager()   # unscoped — for middleware, admin, management commands
        scoped  = OrganizationScopedManager()  # tenant-scoped — for all tenant/portal views

    When get_current_organization() returns None (no active request / management command),
    returns qs.none() to prevent accidental cross-tenant data access.
    """

    def get_queryset(self):
        org = get_current_organization()
        qs = super().get_queryset()
        return qs.filter(organization=org) if org else qs.none()
