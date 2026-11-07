"""Base settings shared by every environment. All secrets come from the environment."""
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())
CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", default="", cast=Csv())
SITE_URL = config("SITE_URL", default="http://localhost:8000")
SITE_NAME = "ParkShare"
# Obscure the Django admin path in production (set ADMIN_URL=staff-9f3k/ for example).
ADMIN_URL = config("ADMIN_URL", default="django-admin/").strip("/") + "/"

# Content-Security-Policy. "report-only" never blocks anything; switch to "enforce" once you have clicked through
# checkout, the QR scanner and the dashboards in your own environment with the browser console open.
CSP_MODE = config("CSP_MODE", default="report-only")  # off | report-only | enforce
CSP_REPORT_URI = config("CSP_REPORT_URI", default="")
_CDN = "https://cdn.jsdelivr.net"
CSP_DIRECTIVES = {
    "default-src": ["'self'"],
    "script-src": ["'self'", _CDN, "https://checkout.razorpay.com"],
    "style-src": ["'self'", "'unsafe-inline'", _CDN, "https://fonts.googleapis.com"],
    "font-src": ["'self'", "https://fonts.gstatic.com", _CDN],
    "img-src": ["'self'", "data:", "blob:", "https://res.cloudinary.com", "https://*.razorpay.com"],
    "connect-src": ["'self'", "https://api.razorpay.com", "https://lumberjack.razorpay.com"],
    "frame-src": ["https://api.razorpay.com", "https://checkout.razorpay.com"],
    "form-action": ["'self'", "https://api.razorpay.com"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "frame-ancestors": ["'none'"],
}
PERMISSIONS_POLICY = "camera=(self), geolocation=(self), microphone=(), payment=(self), usb=()"

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    "widget_tweaks",
    "apps.core",
    "apps.accounts",
    "apps.parkings",
    "apps.bookings",
    "apps.payments",
    "apps.reviews",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "apps.core.middleware.SecurityHeadersMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
        },
    },
]

# ---------------------------------------------------------------- Database
# DB_ENGINE defaults to MySQL. For TiDB Cloud (free, MySQL-compatible) set DB_ENGINE=django_tidb.
_DB_OPTIONS = {"charset": "utf8mb4", "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"}
# Managed MySQL/TiDB services need an encrypted connection:
#   DB_SSL_MODE=REQUIRED  encrypts the connection without checking the certificate (simple, works everywhere)
#   DB_SSL_CA=/path/ca.pem  also verifies the server (for providers that give you a CA file)
_DB_SSL_MODE = config("DB_SSL_MODE", default="")
if _DB_SSL_MODE:
    _DB_OPTIONS["ssl_mode"] = _DB_SSL_MODE
DB_SSL_CA = config("DB_SSL_CA", default="")
if DB_SSL_CA:
    _DB_OPTIONS["ssl"] = {"ca": DB_SSL_CA}

DATABASES = {
    "default": {
        "ENGINE": config("DB_ENGINE", default="django.db.backends.mysql"),
        "NAME": config("DB_NAME"),
        "USER": config("DB_USER"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="127.0.0.1"),
        "PORT": config("DB_PORT", default="3306"),
        "CONN_MAX_AGE": 60,
        "OPTIONS": _DB_OPTIONS,
        "TEST": {"CHARSET": "utf8mb4", "COLLATION": "utf8mb4_unicode_ci"},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- Auth
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:landing"
LOGOUT_REDIRECT_URL = "core:landing"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

# ---------------------------------------------------------------- Payments (Razorpay)
RAZORPAY_KEY_ID = config("RAZORPAY_KEY_ID", default="")
RAZORPAY_KEY_SECRET = config("RAZORPAY_KEY_SECRET", default="")
RAZORPAY_WEBHOOK_SECRET = config("RAZORPAY_WEBHOOK_SECRET", default="")
# Secret for /jobs/run/ so a free external cron service can trigger the periodic jobs (leave empty to disable the endpoint).
JOBS_TOKEN = config("JOBS_TOKEN", default="")
AUTO_COMPLETE_HOURS = config("AUTO_COMPLETE_HOURS", default=6, cast=int)  # unverified checkouts close after this long

SUPERADMIN_EMAIL = config("SUPERADMIN_EMAIL", default="")
SUPERADMIN_PASSWORD = config("SUPERADMIN_PASSWORD", default="")

# ---------------------------------------------------------------- i18n
LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- Static / media
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

CLOUDINARY_CLOUD_NAME = config("CLOUDINARY_CLOUD_NAME", default="")
CLOUDINARY_API_KEY = config("CLOUDINARY_API_KEY", default="")
CLOUDINARY_API_SECRET = config("CLOUDINARY_API_SECRET", default="")
USE_CLOUDINARY = all([CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET])

if USE_CLOUDINARY:
    import cloudinary

    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )

STORAGES = {
    "default": {
        "BACKEND": "apps.core.storage.CloudinaryMediaStorage"
        if USE_CLOUDINARY
        else "django.core.files.storage.FileSystemStorage"
    },
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024

# ---------------------------------------------------------------- Email
EMAIL_HOST = config("EMAIL_HOST", default="")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
    EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
    EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
    EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default="ParkShare <no-reply@example.com>")
PASSWORD_RESET_TIMEOUT = 60 * 60 * 3

# ---------------------------------------------------------------- Cache (also used by rate limiting)
REDIS_URL = config("REDIS_URL", default="")
if REDIS_URL:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}}
elif DEBUG:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
else:
    # Shared across gunicorn workers. Create it once with: python manage.py createcachetable
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.db.DatabaseCache",
            "LOCATION": "django_cache",
        }
    }
RATELIMIT_USE_CACHE = "default"
RATELIMIT_ENABLE = config("RATELIMIT_ENABLE", default=True, cast=bool)
SILENCED_SYSTEM_CHECKS = ["django_ratelimit.E003", "django_ratelimit.W001"]

# ---------------------------------------------------------------- Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"},
    },
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "standard"}},
    "root": {"handlers": ["console"], "level": config("LOG_LEVEL", default="INFO")},
    "loggers": {"django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False}},
}
