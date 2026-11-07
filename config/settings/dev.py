from .base import *  # noqa

import sys

DEBUG = True
# runserver serves static files itself in development; WhiteNoise would only warn about the missing staticfiles/ folder.
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}  # noqa: F405
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

if "test" in sys.argv:  # the suite deliberately triggers 403/404/400 responses; don't print their tracebacks
    LOGGING["root"]["level"] = "CRITICAL"  # noqa: F405
    LOGGING["loggers"]["django.request"]["level"] = "CRITICAL"  # noqa: F405
