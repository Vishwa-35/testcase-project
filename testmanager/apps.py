"""
App Configuration

RISK REMOVAL: Removed all _debug_log() calls.
Uses proper Django logging if needed (currently no logging in ready() method).
"""

from django.apps import AppConfig


class TestmanagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'  # type: ignore[assignment]
    name = 'testmanager'
    verbose_name = "Test Case Manager"
    
    def ready(self):
        """Called when Django starts."""
        try:
            from . import models  # noqa
        except Exception as e:
            # Let Django handle the exception
            raise
