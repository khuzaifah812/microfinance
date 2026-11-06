import json
import tempfile

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from maintenance import services
from maintenance.models import BackupRecord


class Command(BaseCommand):
    help = ("Restore an encrypted backup into an EMPTY database (fresh migrate first). "
            "Refuses when ledger data already exists so a casual run can never overwrite live records.")

    def add_arguments(self, p):
        p.add_argument("filename")
        p.add_argument("--yes-i-am-sure", action="store_true")

    def handle(self, *a, **o):
        from accounting.models import Journal
        from members.models import Member
        if Journal.objects.exists() or Member.objects.exists():
            raise CommandError("The target database already contains data. Restore only into a freshly migrated, empty database.")
        if not o["yes_i_am_sure"]:
            raise CommandError("Pass --yes-i-am-sure to confirm.")
        from pathlib import Path
        from django.conf import settings
        import hashlib
        path = Path(o["filename"])
        if not path.exists():
            path = Path(settings.BACKUP_DIR) / o["filename"]
        rec = BackupRecord(filename=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), encrypted=bool(settings.BACKUP_ENCRYPTION_KEY))
        try:
            if settings.BACKUP_DIR != path.parent:
                settings.BACKUP_DIR = path.parent
            raw = services.read_backup_json(rec)
        except Exception as e:
            raise CommandError(f"Cannot read backup: {e}")
        json.loads(raw)
        with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as f:
            f.write(raw)
        call_command("loaddata", f.name)
        self.stdout.write(self.style.SUCCESS("Restore complete. Run: python manage.py check_audit"))
