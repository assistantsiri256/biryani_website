"""Encrypted model fields for sensitive personal/bank data (Fernet: AES-128-CBC + HMAC)."""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.db import models

PREFIX = "enc$"


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    keys = list(settings.FIELD_ENCRYPTION_KEYS)
    if not keys:
        # Development fallback only (settings refuse to start without keys when DEBUG=False).
        keys = [base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()]
    return MultiFernet([Fernet(k.encode() if isinstance(k, str) else k) for k in keys])


def encrypt(value: str) -> str:
    if not value:
        return value
    return PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value  # empty, or legacy plaintext written before encryption was enabled
    try:
        return _fernet().decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken:
        raise ValueError("Unable to decrypt field – check FIELD_ENCRYPTION_KEYS.")


class EncryptedCharField(models.TextField):
    """Stores ciphertext; exposes plaintext in Python. Not searchable or filterable by value."""

    def __init__(self, *args, max_plain_length=None, **kwargs):
        self.max_plain_length = max_plain_length
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.max_plain_length:
            kwargs["max_plain_length"] = self.max_plain_length
        return name, path, args, kwargs

    def from_db_value(self, value, expression, connection):
        return decrypt(value) if value is not None else value

    def to_python(self, value):
        return decrypt(value) if isinstance(value, str) else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt(value) if value else value

    def formfield(self, **kwargs):
        from django import forms

        defaults = {"form_class": forms.CharField, "max_length": self.max_plain_length, "widget": forms.TextInput}
        defaults.update(kwargs)
        return models.Field.formfield(self, **defaults)


def mask(value: str, visible: int = 4) -> str:
    if not value:
        return ""
    return "•" * max(len(value) - visible, 0) + value[-visible:]
