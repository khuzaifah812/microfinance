from django.core.management.base import BaseCommand

from maintenance.services import create_backup


class Command(BaseCommand):
    help = "Create an encrypted, verified backup (schedule daily; copy the file off-server)."

    def handle(self, *a, **o):
        rec = create_backup(None)
        self.stdout.write(self.style.SUCCESS(f"{rec.filename} ({rec.size_bytes} bytes, verified={rec.verified_ok})"))
