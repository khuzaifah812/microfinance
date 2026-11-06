from django.apps import AppConfig
from django.db.models.signals import post_migrate


class MaintenanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "maintenance"

    def ready(self):
        from .services import seed_defaults
        post_migrate.connect(seed_defaults, sender=self, dispatch_uid="sfms_seed_defaults")
