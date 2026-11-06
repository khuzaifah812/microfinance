from django.conf import settings
from django.core.exceptions import ValidationError


def validate_upload_size(f):
    if f.size > settings.MAX_UPLOAD_BYTES:
        raise ValidationError("File too large. Maximum size is 5 MB.")
