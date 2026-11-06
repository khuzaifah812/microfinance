"""SMS outbox. Messages are QUEUED; nothing is reported as sent until a provider confirms it."""
import os

from .models import ProviderConfig, SmsOutbox


class NotConfigured(Exception):
    pass


def queue_sms(phone, message, kind=""):
    return SmsOutbox.objects.create(phone=phone, message=message[:480], kind=kind)


def provider_ready():
    cfg = ProviderConfig.objects.filter(provider="sms", enabled=True).first()
    return bool(cfg and os.environ.get("SMS_PROVIDER_API_KEY"))


def dispatch_queued(limit=50):
    """Sends queued SMS through the configured provider.

    TODO: integrate your SMS provider's official API here (e.g. Africa's Talking, Beem, Twilio).
    Read credentials only from environment variables. Mark a message 'sent' ONLY after the provider
    returns an acceptance receipt. Until then messages stay 'queued' and this function reports 0.
    """
    if not provider_ready():
        raise NotConfigured("SMS provider is not enabled / SMS_PROVIDER_API_KEY is not set.")
    # TODO: for sms in SmsOutbox.objects.filter(status="queued")[:limit]: call provider, then set status/error.
    return 0
