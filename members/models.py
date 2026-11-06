import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from core import crypto
from core.utils import today
from core.validators import validate_upload_size


def _name(prefix, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"{prefix}/{uuid.uuid4().hex}.{ext}"      # random names: files are never guessable


def photo_path(instance, filename):
    return _name("members/photos", filename)


def doc_path(instance, filename):
    return _name("members/docs", filename)


class Member(models.Model):
    STATUS = [("active", "Active"), ("inactive", "Inactive"), ("suspended", "Suspended"), ("exited", "Exited")]
    GENDER = [("F", "Female"), ("M", "Male")]

    member_no = models.CharField(max_length=20, unique=True)
    full_name = models.CharField(max_length=120, db_index=True)
    gender = models.CharField(max_length=1, choices=GENDER)
    date_of_birth = models.DateField(null=True, blank=True)
    nin_enc = models.TextField(blank=True, editable=False)
    nin_hash = models.CharField(max_length=64, null=True, blank=True, unique=True, editable=False)
    phone = models.CharField(max_length=20, db_index=True)
    alt_phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=200, blank=True)
    occupation = models.CharField(max_length=100, blank=True)
    next_of_kin_name = models.CharField(max_length=120)
    next_of_kin_relation = models.CharField(max_length=60, blank=True)
    next_of_kin_phone = models.CharField(max_length=20)
    photo = models.ImageField(upload_to=photo_path, blank=True,
                              validators=[FileExtensionValidator(["jpg", "jpeg", "png", "webp"]), validate_upload_size])
    status = models.CharField(max_length=12, choices=STATUS, default="active", db_index=True)
    joined_on = models.DateField(default=today, db_index=True)
    consent_given = models.BooleanField(default=False)
    consent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["member_no"]
        indexes = [models.Index(fields=["status", "joined_on"])]

    def __str__(self):
        return f"{self.member_no} - {self.full_name}"

    @property
    def nin(self):
        return crypto.decrypt(self.nin_enc)

    def set_nin(self, value):
        value = (value or "").strip().upper()
        self.nin_enc = crypto.encrypt(value)
        self.nin_hash = crypto.blind_hash(value) if value else None


class MemberDocument(models.Model):
    KINDS = [("national_id", "National ID"), ("passport", "Passport"), ("photo", "Photograph"),
             ("lc_letter", "LC1 / residence letter"), ("consent_form", "Signed consent form"), ("other", "Other")]
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="documents")
    kind = models.CharField(max_length=20, choices=KINDS)
    file = models.FileField(upload_to=doc_path,
                            validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"]), validate_upload_size])
    note = models.CharField(max_length=160, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"{self.member.member_no} {self.kind}"
