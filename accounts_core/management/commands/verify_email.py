from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts_core.audit import audit


class Command(BaseCommand):
    help = "Rescue command: mark a staff member's email as verified (use if mail is down or an admin typed their address wrongly)."

    def add_arguments(self, parser):
        parser.add_argument("username")
        parser.add_argument("--email", help="set this address first")
        parser.add_argument("--unverified", action="store_true", help="only change the address and leave it unverified, so the next sign-in sends a code to it")

    def handle(self, *args, **o):
        User = get_user_model()
        try:
            u = User.objects.select_related("profile").get(username=o["username"].lower())
        except User.DoesNotExist:
            raise CommandError("No such user")
        if o.get("email"):
            u.email = o["email"].strip().lower()
            u.save(update_fields=["email"])
        u.profile.email_verified = not o["unverified"]
        u.profile.save(update_fields=["email_verified"])
        audit("user.email_verified", "security", "User", u.pk, u.username, detail="Marked verified from the command line")
        self.stdout.write(self.style.SUCCESS(f"{u.username}: email {'will be verified at next sign-in' if o['unverified'] else 'marked verified'}"))
