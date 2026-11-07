"""Driver reliability points. Every change is atomic and written to PointLog."""
from django.db import transaction

from apps.core.models import SiteSettings

from .models import PointLog, User

REASON_SETTING = {
    PointLog.Reason.SATISFIED: "points_satisfied",
    PointLog.Reason.UNSATISFIED: "points_unsatisfied",
    PointLog.Reason.NO_SHOW: "points_no_show",
    PointLog.Reason.LATE_CANCEL: "points_late_cancel",
    PointLog.Reason.NO_CHECKOUT_VERIFY: "points_no_checkout_verify",
}


@transaction.atomic
def apply_points(user, reason, note=""):
    """Add the configured delta for `reason`. Points never go below zero. Returns the PointLog row."""
    delta = getattr(SiteSettings.load(), REASON_SETTING[reason])
    locked = User.objects.select_for_update().get(pk=user.pk)
    new_total = max(0, locked.points + delta)
    actual = new_total - locked.points
    locked.points = new_total
    locked.save(update_fields=["points"])
    return PointLog.objects.create(user=locked, delta=actual, balance_after=new_total, reason=reason, note=note[:255])
