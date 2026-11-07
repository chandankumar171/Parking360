from .models import AuditLog, Notification


def audit(actor, action, target=None, message="", **metadata):
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        action=action,
        target_type=target.__class__.__name__ if target is not None else "",
        target_id=str(getattr(target, "pk", "")) if target is not None else "",
        message=message[:255],
        metadata=metadata,
    )


def notify(user, title, message="", link=""):
    return Notification.objects.create(user=user, title=title[:120], message=message[:255], link=link[:255])
