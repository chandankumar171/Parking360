from django.core.cache import cache
from django.db.models.signals import post_delete, post_save


def clear_landing_cache(sender, **kwargs):
    from apps.core.views import LANDING_CACHE_KEY

    cache.delete(LANDING_CACHE_KEY)


def connect():
    from .models import Parking

    post_save.connect(clear_landing_cache, sender=Parking, dispatch_uid="parking_save_clear_landing")
    post_delete.connect(clear_landing_cache, sender=Parking, dispatch_uid="parking_delete_clear_landing")
