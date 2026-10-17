from functools import wraps
from django.shortcuts import render
from django.contrib.auth.decorators import login_required


def role_required(*roles):
    """
    Decorator factory: @role_required('teacher') or @role_required('teacher', 'student').

    How it works:
    1. @login_required ensures the user is authenticated first.
    2. Superusers bypass all role checks (they can access everything).
    3. For regular users, we look up their Profile.role.
    4. If the role is not in the allowed list, we render a 403 page — no redirect loop.
    5. Phase 4: every 403 is also written to the MongoDB audit log as ACCESS_DENIED.
    """
    def decorator(view_func):
        @wraps(view_func)          # preserves the original function name/docstring
        @login_required            # redirects to LOGIN_URL if not authenticated
        def wrapper(request, *args, **kwargs):
            # Superuser can always access any view
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            # Check if the user has a Profile and if their role is allowed
            try:
                user_role = request.user.profile.role
            except Exception:
                _log_denied(request, view_func.__name__)
                return render(request, '403.html', status=403)

            if user_role not in roles:
                # Log the denied attempt before returning 403
                _log_denied(request, view_func.__name__)
                return render(request, '403.html', status=403)

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _log_denied(request, view_name):
    """
    Write an ACCESS_DENIED audit entry.
    Wrapped in try/except so a Mongo failure never causes a secondary error
    while we are already handling a 403.
    """
    try:
        from .audit import log_action, ACCESS_DENIED
        log_action(
            request,
            ACCESS_DENIED,
            target=view_name,
            details=f"Attempted access to {request.path}",
        )
    except Exception:
        pass  # audit failure must never affect the HTTP response
