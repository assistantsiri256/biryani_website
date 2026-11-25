"""Cache-based login throttling (per username + client IP)."""

from django.conf import settings
from django.core.cache import cache


def client_ip(request) -> str:
    # Behind Nginx, configure it to set X-Real-IP; don't trust arbitrary X-Forwarded-For chains.
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "")


def _key(username: str, ip: str) -> str:
    return f"login-fail:{(username or '').lower()}:{ip}"


def is_locked(username: str, ip: str) -> bool:
    return cache.get(_key(username, ip), 0) >= settings.LOGIN_MAX_FAILURES


def register_failure(username: str, ip: str) -> None:
    key = _key(username, ip)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)


def reset(username: str, ip: str) -> None:
    cache.delete(_key(username, ip))
