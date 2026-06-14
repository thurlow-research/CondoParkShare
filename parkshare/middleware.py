"""
parkshare.middleware — TenantMiddleware, ImpersonationMiddleware, and
RatelimitMiddleware.

TenantMiddleware: resolves the Organization from request hostname and stores
it in thread-local storage so OrganizationScopedManager can read it without
a request object.

ImpersonationMiddleware: when a superuser operator is impersonating a user
(session key 'impersonating'), overrides request.user and logs every POST
to AdminAuditLog.

RatelimitMiddleware: converts Ratelimited exceptions (PermissionDenied
subclass) raised by django-ratelimit block=True into proper HTTP 429 responses.
"""

import json
import logging
import threading
from datetime import timezone as dt_timezone

logger = logging.getLogger(__name__)
audit_recovery_logger = logging.getLogger("audit_recovery")

_thread_locals = threading.local()


def get_current_organization():
    """Return the Organization for the current request thread, or None."""
    return getattr(_thread_locals, "organization", None)


class TenantMiddleware:
    """
    Resolve the Organization from the request hostname.

    Sets:
        request.organization — the Organization instance
        _thread_locals.organization — same, for scoped manager access

    Raises Http404 if the hostname does not match any Organisation.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0].lower()
        try:
            from parking.models import Organization

            org = Organization.objects.get(hostname=host)
        except Organization.DoesNotExist:
            from django.http import Http404

            raise Http404
        _thread_locals.organization = org
        request.organization = org
        try:
            return self.get_response(request)
        finally:
            # Always clear thread-local after request to avoid leaking state
            # into subsequent requests on the same thread.
            _thread_locals.organization = None


class RatelimitMiddleware:
    """
    Convert Ratelimited exceptions into HTTP 429 responses.

    django-ratelimit raises Ratelimited (a PermissionDenied subclass) when
    block=True and the limit is exceeded.  Without this middleware Django would
    render it as a 403.  A 429 is the correct status and prevents confusion
    with genuine permission errors.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_exception(self, request, exception):
        from django_ratelimit.exceptions import Ratelimited

        if isinstance(exception, Ratelimited):
            from django.shortcuts import render as django_render

            return django_render(request, "429.html", status=429)
        return None


class ImpersonationMiddleware:
    """
    Allow superuser operators to impersonate a resident or HOA admin.

    When session['impersonating'] is set (by the operator console action):
    - request.user is replaced with the impersonated User
    - Every POST is logged to AdminAuditLog with actor=operator, on_behalf_of=user

    The 'real_operator' session key holds the superuser's pk for the audit log.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        impersonated_pk = request.session.get("impersonating")
        if (
            impersonated_pk
            and request.user.is_authenticated
            and request.user.is_superuser
        ):
            from accounts.models import User

            try:
                impersonated = User.objects.get(pk=impersonated_pk)
            except User.DoesNotExist:
                # Impersonation target no longer exists; clear the session keys
                del request.session["impersonating"]
                request.session.pop("real_operator", None)
            else:
                # Block superuser-to-superuser impersonation
                if impersonated.is_superuser:
                    del request.session["impersonating"]
                    request.session.pop("real_operator", None)
                    return self.get_response(request)

                real_operator = request.user
                request.user = impersonated
                request._real_operator = real_operator

                if request.method == "POST":
                    try:
                        from accounts.models import AdminAuditLog

                        AdminAuditLog.objects.create(
                            organization=getattr(request, "organization", None),
                            actor=real_operator,
                            on_behalf_of=impersonated,
                            action="impersonate_action",
                            target_type="user",
                            target_id=impersonated.pk,
                            notes=f"POST {request.path}",
                        )
                    except Exception:
                        # Fail-open: never block the impersonated request because
                        # the audit-log write failed. Emit a structured recovery
                        # record with every field needed to reconstruct the row.
                        # The emit itself is wrapped in a second except so that
                        # any failure inside (json.dumps error, FileHandler unable
                        # to open path, disk full) cannot propagate out of __call__
                        # and break the fail-open guarantee.
                        try:
                            from django.utils.timezone import now as django_now

                            org = getattr(request, "organization", None)
                            recovery_record = {
                                "organization_id": getattr(org, "pk", None),
                                "actor_id": getattr(real_operator, "pk", None),
                                "on_behalf_of_id": getattr(impersonated, "pk", None),
                                "action": "impersonate_action",
                                "target_type": "user",
                                "target_id": getattr(impersonated, "pk", None),
                                "notes": f"POST {request.path}",
                                "attempted_at": django_now()
                                .astimezone(dt_timezone.utc)
                                .isoformat(),
                            }
                            audit_recovery_logger.error(json.dumps(recovery_record))
                        except Exception:
                            # Last resort: recovery emit itself failed. Swallow so
                            # the request always proceeds to get_response.
                            logger.warning(
                                "audit recovery emit failed "
                                "(operator=%s on_behalf_of=%s path=%s)",
                                getattr(real_operator, "pk", None),
                                getattr(impersonated, "pk", None),
                                request.path,
                            )

        return self.get_response(request)
