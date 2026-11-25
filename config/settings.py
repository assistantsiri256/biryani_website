from decimal import Decimal
import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

TESTING = "test" in sys.argv
DEBUG = os.getenv("DEBUG", "True") == "True"

# No hardcoded secrets in production: refuse to start without them.
SECRET_KEY = os.getenv("SECRET_KEY") or ""
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SECRET_KEY must be set when DEBUG=False.")
    SECRET_KEY = "dev-insecure-key-change-me"

ALLOWED_HOSTS = [h for h in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "apps.core",
    "apps.accounts",
    "apps.members",
    "apps.binary_network",
    "apps.products",
    "apps.orders",
    "apps.commissions",
    "apps.wallet",
    "apps.withdrawals",
    "apps.notifications",
    "apps.reports",
    "apps.support",
    "apps.deposits",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.notifications.context_processors.unread_notifications",
                "apps.orders.context_processors.cart_count",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# PostgreSQL in production via DATABASE_URL; SQLite fallback for local development.
if os.getenv("DATABASE_URL"):
    DATABASES = {"default": dj_database_url.parse(os.environ["DATABASE_URL"], conn_max_age=600)}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# Cache backs OTP/login rate limiting. Use Redis in production so limits are shared across workers.
if os.getenv("REDIS_URL"):
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": os.environ["REDIS_URL"]}}
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
if TESTING:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast tests only

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "reports:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "localhost")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "BinaryPro <no-reply@binarypro.local>")

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"user": "120/min"},
}

# --- Field encryption (bank account, PAN, UPI) -------------------------------
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Comma-separate several keys to rotate (first key encrypts, all keys decrypt).
FIELD_ENCRYPTION_KEYS = [k for k in os.getenv("FIELD_ENCRYPTION_KEYS", "").split(",") if k]
if not FIELD_ENCRYPTION_KEYS and not DEBUG:
    raise ImproperlyConfigured("FIELD_ENCRYPTION_KEYS must be set when DEBUG=False.")

# --- OTP ----------------------------------------------------------------------
OTP_LENGTH = 6
OTP_TTL_SECONDS = 300            # code valid for 5 minutes
OTP_MAX_ATTEMPTS = 5             # wrong guesses before the code is burned
OTP_RESEND_COOLDOWN_SECONDS = 60
OTP_MAX_PER_HOUR = 5             # per destination
SMS_BACKEND = "apps.accounts.sms.LocmemSMSBackend" if TESTING else os.getenv("SMS_BACKEND", "apps.accounts.sms.ConsoleSMSBackend")

LOGIN_MAX_FAILURES = 5           # per username + IP
LOGIN_LOCKOUT_SECONDS = 900

# --- Payments -------------------------------------------------------------
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
# UPI QR: customers scan one QR (your UPI ID, amount pre-filled), pay, then submit
# the 12-digit UTR. An admin checks it against the bank/UPI app and confirms.
UPI_ID = os.getenv("UPI_ID", "").strip()
UPI_PAYEE_NAME = os.getenv("UPI_PAYEE_NAME", "BinaryPro").strip()
# Top-up wallet deposits (paid via the same UPI QR, approved by an admin).
DEPOSIT_MIN = Decimal(os.getenv("DEPOSIT_MIN", "100"))
DEPOSIT_MAX = Decimal(os.getenv("DEPOSIT_MAX", "100000"))
# USDT (BEP-20) deposits, verified automatically on BNB Smart Chain. Empty address = option hidden.
USDT_BEP20_ADDRESS = os.getenv("USDT_BEP20_ADDRESS", "").strip()
USDT_INR_RATE = Decimal(os.getenv("USDT_INR_RATE", "100"))      # ₹ credited per 1 USDT
USDT_BEP20_CONTRACT = os.getenv("USDT_BEP20_CONTRACT", "0x55d398326f99059fF775485246999027B3197955")
BSC_RPC_URL = os.getenv("BSC_RPC_URL", "https://bsc-dataseed.binance.org/")
BSC_MIN_CONFIRMATIONS = int(os.getenv("BSC_MIN_CONFIRMATIONS", "15"))
# Demo mode simulates a successful payment. Never enable in production.
PAYMENT_DEMO_MODE = os.getenv("PAYMENT_DEMO_MODE", "True" if DEBUG else "False") == "True"
if PAYMENT_DEMO_MODE and not DEBUG:
    raise ImproperlyConfigured("PAYMENT_DEMO_MODE cannot be enabled when DEBUG=False.")

# --- Celery -----------------------------------------------------------------
# Without a broker, background work (commission processing, emails) runs inline.
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "") or None
CELERY_TASK_ALWAYS_EAGER = not CELERY_BROKER_URL
CELERY_TASK_ACKS_LATE = True
CELERY_TIMEZONE = TIME_ZONE

# --- Production hardening ---------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "True") == "True"
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 60 * 60 * 8  # 8 hours

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"apps": {"handlers": ["console"], "level": os.getenv("APP_LOG_LEVEL", "INFO")}},
}
