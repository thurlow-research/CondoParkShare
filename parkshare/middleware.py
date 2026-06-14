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

import logging
import threading

logger = logging.getLogger(__name__)

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
                            notes=f"POST {request.path}",
                        )
                    except Exception:
                        # Never block the impersonated request on an audit-log
                        # failure, but a silently-lost impersonation audit record
                        # is a security-observability gap — surface it in logs.
                        logger.exception(
                            "Failed to write impersonate_action AdminAuditLog "
                            "(operator=%s on_behalf_of=%s path=%s)",
                            getattr(real_operator, "pk", None),
                            getattr(impersonated, "pk", None),
                            request.path,
                        )

        return self.get_response(request)
