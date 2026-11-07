from decimal import Decimal

from django.conf import settings
from django.db import models


class Payment(models.Model):
    class Kind(models.TextChoices):
        BOOKING = "BOOKING", "Booking"
        OVERTIME = "OVERTIME", "Overtime"

    class Status(models.TextChoices):
        CREATED = "CREATED", "Created"
        PAID = "PAID", "Paid"
        FAILED = "FAILED", "Failed"
        PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially refunded"
        REFUNDED = "REFUNDED", "Refunded"

    booking = models.ForeignKey("bookings.Booking", on_delete=models.PROTECT, related_name="payments")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    razorpay_order_id = models.CharField(max_length=40, unique=True)
    razorpay_payment_id = models.CharField(max_length=40, unique=True, null=True, blank=True)
    razorpay_signature = models.CharField(max_length=128, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True)
    platform_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    owner_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    failure_reason = models.CharField(max_length=255, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["booking", "kind", "status"], name="pay_booking_kind_idx"),
            models.Index(fields=["status", "paid_at"], name="pay_status_paid_idx"),
        ]

    def __str__(self):
        return f"{self.kind} {self.razorpay_order_id} {self.status}"


class Refund(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        APPROVED = "APPROVED", "Approved"
        PROCESSING = "PROCESSING", "Processing"
        REFUNDED = "REFUNDED", "Refunded"
        REJECTED = "REJECTED", "Rejected"
        FAILED = "FAILED", "Failed"

    ACTIVE = ("REQUESTED", "APPROVED", "PROCESSING", "FAILED")

    booking = models.ForeignKey("bookings.Booking", on_delete=models.PROTECT, related_name="refunds")
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    initiated_by_owner = models.BooleanField(default=False)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    cancellation_charge = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    razorpay_refund_id = models.CharField(max_length=40, null=True, blank=True, unique=True)
    reason = models.CharField(max_length=255, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    retry_count = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["status", "processed_at"], name="refund_status_proc_idx")]


class OwnerLedger(models.Model):
    """What the platform owes each owner. Balance = CREDIT - REFUND_DEBIT - PAYOUT.

    PLATFORM_FEE and FEE_REVERSAL rows are memo entries (they explain the platform's share) and don't change the balance.
    """

    class Type(models.TextChoices):
        CREDIT = "CREDIT", "Earning"
        REFUND_DEBIT = "REFUND_DEBIT", "Refund"
        PLATFORM_FEE = "PLATFORM_FEE", "Platform fee"
        FEE_REVERSAL = "FEE_REVERSAL", "Platform fee reversed"
        PAYOUT = "PAYOUT", "Payout"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ledger_entries")
    booking = models.ForeignKey("bookings.Booking", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    payment = models.ForeignKey(Payment, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    refund = models.ForeignKey(Refund, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    entry_type = models.CharField(max_length=14, choices=Type.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)  # always positive
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["owner", "entry_type"], name="ledger_owner_type_idx"),
            models.Index(fields=["owner", "created_at"], name="ledger_owner_date_idx"),
        ]


class WebhookEvent(models.Model):
    event_id = models.CharField(max_length=80, unique=True)
    event = models.CharField(max_length=60)
    received_at = models.DateTimeField(auto_now_add=True)
