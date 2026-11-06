from django.core.validators import FileExtensionValidator
from django.db import models, transaction

from .validators import validate_upload_size

DEFAULT_SETTINGS = {
    "min_savings_balance": ("0", "Minimum balance that must remain in a savings account after a withdrawal (UGX)."),
    "allow_share_transfers": ("1", "Allow share transfers between members (1 = yes, 0 = no)."),
    "prov_current": ("1", "Loan-loss provision % for performing loans (0 days overdue)."),
    "prov_1_30": ("5", "Provision % for loans 1-30 days overdue."),
    "prov_31_60": ("25", "Provision % for loans 31-60 days overdue."),
    "prov_61_90": ("50", "Provision % for loans 61-90 days overdue."),
    "prov_90_plus": ("100", "Provision % for loans over 90 days overdue."),
    "backup_reminder_hours": ("24", "Remind administrators when the latest backup is older than this many hours."),
    "data_retention_years": ("7", "Years financial records are retained (informational; nothing is auto-deleted)."),
    "default_savings_interest": ("0", "Default annual savings interest rate % suggested on the interest run page."),
}


class SiteConfig(models.Model):
    """Singleton: editable system name, logo and organisation details."""
    name = models.CharField("System name", max_length=120, default="Omukazi W’omutima Umoja SACCO")
    short_name = models.CharField(max_length=40, default="Umoja SACCO")
    tagline = models.CharField(max_length=160, blank=True, default="Omukazi W’omutima")
    logo = models.ImageField(upload_to="branding/", blank=True,
                             validators=[FileExtensionValidator(["png", "jpg", "jpeg", "webp"]), validate_upload_size])
    address = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    registration_no = models.CharField("Registration number", max_length=60, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site configuration"

    def __str__(self):
        return self.name

    @property
    def has_logo(self):
        """Always true: the SACCO's own logo ships with the system and an uploaded logo replaces it."""
        return True

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class SystemSetting(models.Model):
    key = models.CharField(max_length=60, unique=True)
    value = models.CharField(max_length=255, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key}={self.value}"

    @staticmethod
    def get(key, default=None):
        row = SystemSetting.objects.filter(key=key).first()
        if row is not None:
            return row.value
        if default is not None:
            return default
        return DEFAULT_SETTINGS.get(key, ("", ""))[0]

    @staticmethod
    def set(key, value):
        SystemSetting.objects.update_or_create(key=key, defaults={"value": str(value)})


class Sequence(models.Model):
    """Gap-free-ish unique counters for member numbers, references, etc."""
    name = models.CharField(max_length=40, unique=True)
    last = models.BigIntegerField(default=0)

    def __str__(self):
        return f"{self.name}: {self.last}"

    @staticmethod
    def next(name):
        with transaction.atomic():
            Sequence.objects.get_or_create(name=name)
            seq = Sequence.objects.select_for_update().get(name=name)
            seq.last += 1
            seq.save(update_fields=["last"])
            return seq.last


def next_ref(prefix, name=None, width=6):
    return f"{prefix}{Sequence.next(name or prefix):0{width}d}"
