import logging

logger = logging.getLogger(__name__)


def ensure_superadmin_after_migrate(sender, **kwargs):
    """Keep the .env superadmin in sync every time migrations run."""
    from .services import ensure_superadmin

    try:
        ensure_superadmin()
    except Exception:  # a bad env value must not break the deploy; it is logged loudly instead
        logger.exception("Could not create or update the superadmin from environment variables")
