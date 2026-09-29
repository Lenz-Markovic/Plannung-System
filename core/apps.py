from django.apps import AppConfig
from django.db.models.signals import post_migrate


def update_roles(sender, **kwargs):
    """After every `migrate`: NEW default permissions reach the roles at once (core/role_sync.py)."""
    from .role_sync import grant_new_defaults

    grant_new_defaults()


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Allgemein"

    def ready(self):
        post_migrate.connect(update_roles, dispatch_uid="core.update_roles")
