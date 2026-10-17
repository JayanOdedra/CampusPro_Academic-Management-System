from django.apps import AppConfig


class AcademicsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'academics'

    def ready(self):
        """
        Called once when Django has fully loaded all apps.
        We connect two things here:
        1. Import models so the post_migrate signal (Group creation) is registered.
        2. Connect Django's built-in auth signals to our audit handlers.
           This means login/logout/failed-login are logged automatically
           without any changes to the login/logout views.
        """
        import academics.models  # noqa: F401 — registers post_migrate signal

        # Import the signal handlers from audit.py
        from .audit import on_login_success, on_logout, on_login_failed
        from django.contrib.auth.signals import (
            user_logged_in,
            user_logged_out,
            user_login_failed,
        )

        # Connect each signal to its handler.
        # dispatch_uid prevents duplicate connections if ready() is called twice.
        user_logged_in.connect(on_login_success,  dispatch_uid='audit_login_success')
        user_logged_out.connect(on_logout,         dispatch_uid='audit_logout')
        user_login_failed.connect(on_login_failed, dispatch_uid='audit_login_failed')
