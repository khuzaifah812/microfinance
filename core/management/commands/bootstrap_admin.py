import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts_core.services import create_staff


class Command(BaseCommand):
    help = "Create the first System Administrator from BOOTSTRAP_ADMIN_* environment variables (run once after first migrate)."

    def handle(self, *a, **kw):
        User = get_user_model()
        u = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin").lower()
        pw = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD", "")
        if len(pw) < 10:
            raise CommandError("Set BOOTSTRAP_ADMIN_PASSWORD (min 10 characters) in the environment.")
        if User.objects.filter(username=u).exists():
            self.stdout.write(self.style.WARNING(f"User '{u}' already exists - nothing to do."))
            return
        create_staff(u, "System", "Administrator", "admin", os.environ.get("BOOTSTRAP_ADMIN_EMAIL", ""), password=pw)
        self.stdout.write(self.style.SUCCESS(f"Administrator '{u}' created. You must change the password and enrol 2FA at first sign-in."))
