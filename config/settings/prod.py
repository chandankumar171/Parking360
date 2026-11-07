import logging

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa
from .base import USE_CLOUDINARY, config

DEBUG = False

if not USE_CLOUDINARY:
    raise ImproperlyConfigured(
        "Cloudinary credentials are required in production (uploaded files must not live on the app server)."
    )
if len(SECRET_KEY) < 32 or SECRET_KEY.startswith("change-me"):  # noqa: F405
    raise ImproperlyConfigured("Set a long random SECRET_KEY in production.")

if "*" in ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("ALLOWED_HOSTS must list your real domain(s); '*' is not allowed in production.")
if set(ALLOWED_HOSTS) <= {"localhost", "127.0.0.1"}:  # noqa: F405
    logging.getLogger(__name__).warning("ALLOWED_HOSTS only lists localhost. Set it to your domain before going live.")

SILENCED_SYSTEM_CHECKS = list(SILENCED_SYSTEM_CHECKS) + ["security.W021"]  # noqa: F405  (HSTS preload is a deliberate opt-in)

SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SECURE_REDIRECT_EXEMPT = [r"^healthz/$"]  # allow plain-HTTP load balancer health checks
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# Optional error tracking: set SENTRY_DSN and `pip install sentry-sdk`.
SENTRY_DSN = config("SENTRY_DSN", default="")
if SENTRY_DSN:
    try:
        import sentry_sdk

        sentry_sdk.init(
            dsn=SENTRY_DSN, environment=config("ENVIRONMENT", default="production"),
            traces_sample_rate=config("SENTRY_TRACES_RATE", default=0.0, cast=float), send_default_pii=False,
        )
    except ImportError:
        logging.getLogger(__name__).warning("SENTRY_DSN is set but sentry-sdk is not installed.")
