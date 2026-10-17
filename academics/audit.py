"""
academics/audit.py
------------------
Audit logging to MongoDB.

log_action() is the single entry point — call it from any view.
Django auth signals (user_logged_in, user_logged_out, user_login_failed)
are connected here so login/logout events are captured automatically
without touching the login/logout views.

Security rules enforced here:
- NEVER log passwords, tokens, or session keys.
- For LOGIN_FAILED we only store the attempted username (not the password).
- IP is read from REMOTE_ADDR (set by the WSGI server, not user-controlled).
- user_agent is informational only — it is stored as a plain string.
"""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Action constants — use these strings everywhere so typos are caught early
# ---------------------------------------------------------------------------
REGISTER          = 'REGISTER'
USER_APPROVED     = 'USER_APPROVED'
USER_REJECTED     = 'USER_REJECTED'
LOGIN_SUCCESS     = 'LOGIN_SUCCESS'
LOGIN_FAILED      = 'LOGIN_FAILED'
LOGOUT            = 'LOGOUT'
COURSE_CREATED    = 'COURSE_CREATED'
COURSE_EDITED     = 'COURSE_EDITED'
COURSE_DELETED    = 'COURSE_DELETED'
TEACHER_ASSIGNED  = 'TEACHER_ASSIGNED'
STUDENT_ENROLLED  = 'STUDENT_ENROLLED'
STUDENT_UNENROLLED = 'STUDENT_UNENROLLED'
ATTENDANCE_MARKED = 'ATTENDANCE_MARKED'
MARKS_UPLOADED    = 'MARKS_UPLOADED'
NOTICE_POSTED     = 'NOTICE_POSTED'
NOTICE_DELETED    = 'NOTICE_DELETED'
ACCESS_DENIED     = 'ACCESS_DENIED'
LOGIN_LOCKED      = 'LOGIN_LOCKED'    # Phase 5: account locked after too many failures
EXPORT_CSV        = 'EXPORT_CSV'      # Phase 5: any CSV export
REPORT_CARD_VIEWED = 'REPORT_CARD_VIEWED'  # Phase 5: student viewed their report card
ACCOUNT_UNLOCKED  = 'ACCOUNT_UNLOCKED'   # Phase 5: admin unlocked a locked account


def _get_ip(request):
    """
    Extract the client IP address from the request.
    REMOTE_ADDR is set by the WSGI server and is not spoofable by the client
    in a standard deployment (unlike X-Forwarded-For which can be faked).
    """
    return request.META.get('REMOTE_ADDR', 'unknown')


def _get_role(user):
    """
    Return the user's role string for the log document.
    Superusers are labelled 'admin'; regular users use their Profile role.
    Returns 'unknown' if the profile is missing (should never happen).
    """
    if user is None or not user.is_authenticated:
        return 'anonymous'
    if user.is_superuser:
        return 'admin'
    try:
        return user.profile.role
    except Exception:
        return 'unknown'


def log_action(request, action, target=None, details=None):
    """
    Insert one audit document into MongoDB's audit_logs collection.

    Parameters:
        request  — Django HttpRequest (used for user, IP, user-agent)
        action   — one of the ACTION constants above (a plain string)
        target   — optional string describing what was acted on (e.g. course code)
        details  — optional string with extra context

    The function is intentionally fire-and-forget:
    - It never raises an exception to the caller.
    - If MongoDB is down, it logs a warning and returns silently.
    - This means a failed audit write NEVER blocks the user's action.
    """
    from .mongo import get_audit_collection  # local import avoids circular imports

    col = get_audit_collection()
    if col is None:
        # Mongo unavailable — log locally and continue
        logger.warning("Audit skipped (Mongo unavailable): action=%s", action)
        return

    user = getattr(request, 'user', None)

    # Build the document — only safe, non-sensitive fields
    doc = {
        'timestamp':  datetime.now(timezone.utc),  # always UTC
        'user_id':    user.pk if user and user.is_authenticated else None,
        'username':   user.username if user and user.is_authenticated else 'anonymous',
        'role':       _get_role(user),
        'action':     action,          # e.g. 'LOGIN_SUCCESS'
        'target':     target,          # e.g. 'CS101' or 'alice'
        'details':    details,         # e.g. 'Score uploaded for Midterm'
        'ip_address': _get_ip(request),
        # User-agent is informational; truncated to 300 chars to avoid huge docs
        'user_agent': request.META.get('HTTP_USER_AGENT', '')[:300],
    }

    try:
        col.insert_one(doc)
    except Exception as exc:
        # Never crash the app because of a failed audit write
        logger.warning("Audit log insert failed: %s", exc)


# ---------------------------------------------------------------------------
# Django auth signals
# These fire automatically when Django's auth system logs a user in/out.
# We connect them in AcademicsConfig.ready() (apps.py).
# ---------------------------------------------------------------------------

def on_login_success(sender, request, user, **kwargs):
    """
    Signal handler for django.contrib.auth.signals.user_logged_in.
    Fires after a successful login — user is already authenticated.
    """
    log_action(request, LOGIN_SUCCESS, target=user.username)


def on_logout(sender, request, user, **kwargs):
    """
    Signal handler for django.contrib.auth.signals.user_logged_out.
    """
    log_action(request, LOGOUT, target=user.username if user else None)


def on_login_failed(sender, credentials, request, **kwargs):
    """
    Signal handler for django.contrib.auth.signals.user_login_failed.
    credentials dict contains 'username' and 'password' — we ONLY log
    the username, never the password.
    """
    attempted_username = credentials.get('username', 'unknown')
    # Build a minimal fake-request-like object if request is None
    # (some test runners pass None for request in this signal)
    log_action(
        request or _FakeRequest(),
        LOGIN_FAILED,
        target=attempted_username,
        details='Login attempt failed',
    )


class _FakeRequest:
    """Minimal stand-in when request is None in the login_failed signal."""
    user = None
    META = {}
