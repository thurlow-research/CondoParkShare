"""
parkshare.context_processors — template context processors.

impersonation: injects impersonation state into every template context so that
base templates can render an "impersonation active" banner without any view
needing to pass it explicitly.
"""


def impersonation(request):
    """
    Add impersonation context variables to every template render.

    Variables injected:
        impersonation_active  — True when an operator is impersonating a user
        impersonated_user     — the User currently being impersonated (or None)
        real_operator         — the actual logged-in superuser (or None)
    """
    real_operator = getattr(request, '_real_operator', None)
    return {
        'impersonation_active': bool(real_operator),
        'impersonated_user': request.user if real_operator else None,
        'real_operator': real_operator,
    }
