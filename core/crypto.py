"""Application-level encryption for the most sensitive personal fields (NIN)."""
import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


def _fernet():
    raw = getattr(settings, "FIELD_ENCRYPTION_KEY", "") or hashlib.sha256(("fld:" + settings.SECRET_KEY).encode()).digest()
    if isinstance(raw, str):
        try:
            return Fernet(raw.encode())
        except Exception:
            raw = hashlib.sha256(raw.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode() if text else ""


def decrypt(token):
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


def blind_hash(text):
    """Deterministic keyed hash so encrypted values can still be checked for uniqueness."""
    return hmac.new(settings.SECRET_KEY.encode(), text.strip().upper().encode(), hashlib.sha256).hexdigest() if text else None
