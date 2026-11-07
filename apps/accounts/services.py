import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from .models import User

logger = logging.getLogger(__name__)


def ensure_superadmin():
    """Create or update the superadmin defined by SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD.

    Idempotent. Returns (user, "created" | "updated" | "unchanged") or None when not configured.
    """
    email = (settings.SUPERADMIN_EMAIL or "").strip().lower()
    password = settings.SUPERADMIN_PASSWORD or ""
    if not email or not password:
        logger.warning("SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD not set; superadmin was not created.")
        return None
    try:
        validate_email(email)
    except ValidationError:
        logger.error("SUPERADMIN_EMAIL is not a valid email address.")
        return None

    with transaction.atomic():
        user = User.objects.select_for_update().filter(email__iexact=email).first()
        if user is None:
            user = User.objects.create_user(
                email=email,
                password=password,
                full_name="Super Admin",
                role=User.Role.SUPERADMIN,
                is_staff=True,
                is_superuser=True,
            )
            logger.info("Superadmin %s created.", email)
            return user, "created"

        changed = False
        if not user.check_password(password):
            user.set_password(password)
            changed = True
        for field, value in (
            ("role", User.Role.SUPERADMIN),
            ("is_staff", True),
            ("is_superuser", True),
            ("is_active", True),
        ):
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed = True
        if changed:
            user.save()
            logger.info("Superadmin %s updated.", email)
        return user, "updated" if changed else "unchanged"
