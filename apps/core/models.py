from decimal import Decimal

from django.db import models


class SiteSettings(models.Model):
    """Single-row table holding platform-wide rules, editable by the superadmin."""

    verification_required = models.BooleanField(
        default=True, help_text="If on, new parkings must be approved by the superadmin before drivers see them."
    )
    reverify_on_edit = models.BooleanField(
        default=False, help_text="Send an approved parking back to review when key details are edited."
    )
    platform_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))

    points_start = models.PositiveIntegerField(default=100)
    points_satisfied = models.IntegerField(default=5)
    points_unsatisfied = models.IntegerField(default=-10)
    points_no_show = models.IntegerField(default=-15)
    points_late_cancel = models.IntegerField(default=-5)
    points_no_checkout_verify = models.IntegerField(default=-20)

    default_grace_minutes = models.PositiveIntegerField(default=15)
    default_cancel_window_minutes = models.PositiveIntegerField(default=60)
    support_email = models.EmailField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return "Site settings"

    def save(self, *args, **kwargs):
        from django.core.cache import cache

        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete("site:support_email")

    def delete(self, *args, **kwargs):  # singleton, never deleted
        pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AuditLog(models.Model):
    """Append-only record of important staff/superadmin and system actions."""

    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    action = models.CharField(max_length=60, db_index=True)
    target_type = models.CharField(max_length=60, blank=True)
    target_id = models.CharField(max_length=40, blank=True)
    message = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.action}: {self.message}"


class Notification(models.Model):
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=120)
    message = models.CharField(max_length=255, blank=True)
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["user", "is_read", "-created_at"])]
