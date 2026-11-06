from django.core.management.base import BaseCommand

from accounts_core.audit import verify_chain


class Command(BaseCommand):
    help = "Verify the audit-log hash chain. Exit code 1 on failure (use in monitoring)."

    def handle(self, *a, **o):
        ok, n, problems = verify_chain()
        if ok:
            self.stdout.write(self.style.SUCCESS(f"Audit chain intact ({n} records)."))
        else:
            for p in problems:
                self.stderr.write(p)
            raise SystemExit(1)
