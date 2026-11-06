from django.apps import AppConfig


class SavingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "savings"

    def ready(self):
        from . import services  # noqa: registers approval handlers
