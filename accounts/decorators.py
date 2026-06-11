"""
accounts.decorators — status-based access control decorators.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def status_required(status):
    """
    Decorator factory.  Wraps a view so that the authenticated user must have
    ``request.user.status == status``.  If the check fails the user is
    redirected to 'login'.

    Usage::

        @status_required('active')
        def my_view(request): ...

    The decorator also enforces login — unauthenticated users are redirected
    to 'login' before the status check runs.
    """
    def decorator(view_func):
        @wraps(view_func)
        @login_required(login_url='login')
        def wrapper(request, *args, **kwargs):
            if not hasattr(request.user, 'status') or request.user.status != status:
                return redirect('login')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# Convenience alias — used by the majority of resident-facing views.
active_required = status_required('active')
