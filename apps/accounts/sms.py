"""Pluggable SMS delivery. Set SMS_BACKEND to a provider class (MSG91, Twilio, etc.)."""

import logging

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger("apps.sms")


class ConsoleSMSBackend:
    """Development backend: prints the message to the server console."""

    def send(self, mobile: str, message: str) -> None:
        print(f"\n[SMS to {mobile}] {message}\n", flush=True)


class LocmemSMSBackend:
    """Test backend: keeps messages in memory."""

    outbox: list[tuple[str, str]] = []

    def send(self, mobile: str, message: str) -> None:
        self.outbox.append((mobile, message))


def send_sms(mobile: str, message: str) -> None:
    backend = import_string(settings.SMS_BACKEND)()
    try:
        backend.send(mobile, message)
    except Exception:  # never leak provider errors to users
        logger.exception("SMS delivery failed to %s", mobile[-4:])
        raise
