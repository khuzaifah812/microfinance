from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.models import SystemSetting


class Command(BaseCommand):
    help = "Wipe a DEMONSTRATION database and re-seed it. Refuses to run against production data."

    def add_arguments(self, p):
        p.add_argument("--yes-i-am-sure", action="store_true")

    def handle(self, *a, **o):
        if settings.PRODUCTION_MODE:
            raise CommandError("PRODUCTION_MODE is on. Demo reset is disabled to protect real financial records.")
        if SystemSetting.get("demo_data_loaded", "0") != "1":
            raise CommandError("This database was not created by seed_data (no demo marker). Refusing to erase it.")
        if not o["yes_i_am_sure"]:
            raise CommandError("Pass --yes-i-am-sure to confirm.")
        call_command("flush", "--no-input", verbosity=0)
        call_command("seed_data", "--force", verbosity=0)
        self.stdout.write(self.style.SUCCESS("Demo data reset."))
