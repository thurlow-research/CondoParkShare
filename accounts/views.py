"""
accounts.views — authentication, registration, and impersonation views.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from django.contrib.auth import get_user_model

User = get_user_model()


@login_required
def impersonation_end(request):
    """
    End an active impersonation session.

    Clears the 'impersonating' and 'real_operator' session keys and redirects
    the operator back to the admin index. Logs the end of the impersonation to
    AdminAuditLog.
    """
    if 'impersonating' in request.session:
        from accounts.models import AdminAuditLog

        # Resolve the real operator via the request attribute set by
        # ImpersonationMiddleware (request._real_operator) before this view
        # runs.  The session key 'real_operator' is never written by the
        # impersonation-start path so the previous session-based lookup always
        # fell through to request.user (the impersonated user), recording the
        # wrong actor in the audit log.
        real_operator = getattr(request, '_real_operator', None)

        AdminAuditLog.objects.create(
            organization=getattr(request.user, 'organization', None),
            actor=real_operator or request.user,
            action='impersonate_end',
            target_type='user',
            target_id=request.session['impersonating'],
        )

        del request.session['impersonating']
        request.session.pop('real_operator', None)

    return redirect('admin:index')
