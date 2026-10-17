"""
academics/emails.py
-------------------
All email notification helpers for CampusPro.

Design rules:
- Every function wraps send_mail() in try/except so an email failure
  NEVER crashes the HTTP request — it is only logged.
- We use Django's built-in send_mail() — no extra library needed.
- In development, EMAIL_BACKEND = console backend prints to terminal.
- In production, set SMTP settings in .env and EMAIL_BACKEND to smtp.
- We never send passwords or sensitive data in emails.
"""

import logging
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


def _send(subject, message, recipient_email):
    """
    Internal helper: send one email.
    fail_silently=False means Django raises an exception on SMTP error,
    which we catch here and log — the caller never sees the exception.
    """
    if not recipient_email:
        return  # FIX: nothing to send to (user has no email address)
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[recipient_email],
            fail_silently=False,  # raise on error so we can log it
        )
        logger.info("Email sent to %s: %s", recipient_email, subject)
    except Exception as exc:
        # Log the error but do NOT re-raise — email failure must not block the user
        logger.warning("Email failed to %s (%s): %s", recipient_email, subject, exc)


def send_registration_email(user):
    """
    Sent to the new user immediately after registration.
    Tells them their account is pending admin approval.
    """
    _send(
        subject="CampusPro — Registration Received",
        message=(
            f"Hello {user.username},\n\n"
            "Your registration has been received.\n"
            "Please wait for an administrator to approve your account "
            "before you can log in.\n\n"
            "— CampusPro Team"
        ),
        recipient_email=user.email,
    )


def send_approval_email(user):
    """
    Sent to the user when an admin approves their account.
    """
    _send(
        subject="CampusPro — Account Approved",
        message=(
            f"Hello {user.username},\n\n"
            "Your account has been approved! You can now log in at:\n"
            "http://127.0.0.1:8000/login/\n\n"
            "— CampusPro Team"
        ),
        recipient_email=user.email,
    )


def send_rejection_email(username, email):
    """
    Sent to the user when an admin rejects their account.
    We accept username and email as plain strings because the User object
    is deleted before this function is called.
    """
    _send(
        subject="CampusPro — Account Not Approved",
        message=(
            f"Hello {username},\n\n"
            "Unfortunately your account registration was not approved.\n"
            "Please contact the administrator for more information.\n\n"
            "— CampusPro Team"
        ),
        recipient_email=email,
    )


def send_marks_notification(student_user, course, exam_name, score, max_score):
    """
    Sent to a student when a teacher uploads their marks for an exam.
    """
    pct = round((score / max_score) * 100, 1) if max_score > 0 else 0
    _send(
        subject=f"CampusPro — Marks uploaded for {course.code}",
        message=(
            f"Hello {student_user.username},\n\n"
            f"Your marks for {course.name} ({course.code}) have been uploaded.\n\n"
            f"Exam   : {exam_name}\n"
            f"Score  : {score} / {max_score} ({pct}%)\n\n"
            "Log in to view your full report card.\n\n"
            "— CampusPro Team"
        ),
        recipient_email=student_user.email,
    )
