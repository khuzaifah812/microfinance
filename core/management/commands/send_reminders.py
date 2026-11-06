from datetime import timedelta

from django.core.management.base import BaseCommand

from core.utils import today
from integrations.sms import queue_sms
from loans.models import LoanInstallment
from notifications.models import Notification
from notifications.services import notify


class Command(BaseCommand):
    help = "Create due-date reminders and overdue alerts (run daily from a scheduler). SMS are queued, never faked as sent."

    def handle(self, *a, **o):
        t = today()
        n_due = n_over = 0
        for i in LoanInstallment.objects.filter(loan__status="active", status__in=("pending", "partial"), due_date__gte=t, due_date__lte=t + timedelta(days=3)).select_related("loan__member"):
            title = f"Repayment due {i.due_date:%d %b}: {i.loan.loan_no}"
            if not Notification.objects.filter(title=title, created_at__date=t).exists():
                notify("repayment_due", title, f"{i.loan.member.full_name} UGX {i.balance:,.0f}", roles=["loan_officer", "cashier"], link=f"/loans/{i.loan_id}/")
                queue_sms(i.loan.member.phone, f"Dear {i.loan.member.full_name.split()[0]}, your loan instalment of UGX {i.balance:,.0f} is due on {i.due_date:%d %b %Y}.", "reminder")
                n_due += 1
        for i in LoanInstallment.objects.filter(loan__status="active", status__in=("pending", "partial"), due_date__lt=t).select_related("loan__member"):
            title = f"Overdue loan {i.loan.loan_no}"
            if i.balance > 0 and not Notification.objects.filter(title=title, created_at__date=t).exists():
                notify("overdue", title, f"{i.loan.member.full_name} - instalment {i.no} overdue since {i.due_date:%d %b}", roles=["loan_officer", "manager"], link=f"/loans/{i.loan_id}/", level="bad")
                n_over += 1
        from maintenance.services import last_backup_age_hours
        age = last_backup_age_hours()
        if (age is None or age > 24) and not Notification.objects.filter(kind="backup", created_at__date=t).exists():
            notify("backup", "Backup reminder", "No verified backup in the last 24 hours. Create and download one today.", roles=["admin"], link="/maintenance/backups/", level="warn")
        self.stdout.write(f"Reminders: {n_due} due, {n_over} overdue.")
