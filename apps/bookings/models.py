from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.choices import VehicleCategory
from apps.core.validators import validate_plate


class Booking(models.Model):
    class Status(models.TextChoices):
        PENDING_PAYMENT = "PENDING_PAYMENT", "Awaiting payment"
        CONFIRMED = "CONFIRMED", "Confirmed"
        CHECKED_IN = "CHECKED_IN", "Parked"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        NO_SHOW = "NO_SHOW", "No-show"
        REFUND_REQUESTED = "REFUND_REQUESTED", "Refund requested"
        EXPIRED = "EXPIRED", "Expired"

    # Code is issued only after payment succeeds (Phase 4); NULLs do not collide on the unique index.
    booking_code = models.CharField(max_length=6, unique=True, null=True, blank=True)
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings")
    parking = models.ForeignKey("parkings.Parking", on_delete=models.PROTECT, related_name="bookings")
    vehicle_type = models.ForeignKey("parkings.ParkingVehicleType", on_delete=models.PROTECT, related_name="bookings")

    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    slots = models.PositiveSmallIntegerField()

    # Vehicle details typed by the driver (snapshot)
    vehicle_category = models.CharField(max_length=14, choices=VehicleCategory.choices)
    vehicle_number_plate = models.CharField(max_length=15, validators=[validate_plate])
    vehicle_brand_model = models.CharField(max_length=100)

    # Price + rule snapshot, so later owner edits never change an existing booking
    price_per_slot = models.DecimalField(max_digits=8, decimal_places=2)
    slot_minutes = models.PositiveSmallIntegerField()
    overtime_block_minutes = models.PositiveSmallIntegerField()
    overtime_charge = models.DecimalField(max_digits=8, decimal_places=2)
    overtime_free_minutes = models.PositiveSmallIntegerField(default=0)
    grace_minutes = models.PositiveIntegerField(default=15)
    base_amount = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    platform_fee_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    owner_amount = models.DecimalField(max_digits=10, decimal_places=2)
    total_paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT)
    payment_hold_expires_at = models.DateTimeField(null=True, blank=True)

    allow_cancellation = models.BooleanField(default=False)
    cancel_window_minutes = models.PositiveIntegerField(default=60)
    cancel_charge_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))

    checked_in_at = models.DateTimeField(null=True, blank=True)
    checkout_requested_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    overtime_minutes = models.PositiveIntegerField(default=0)
    overtime_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0"))
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    cancel_reason = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_at", "-id"]
        indexes = [
            models.Index(fields=["vehicle_type", "status", "start_at", "end_at"], name="bk_availability_idx"),
            models.Index(fields=["driver", "status"], name="bk_driver_status_idx"),
            models.Index(fields=["parking", "status", "start_at"], name="bk_parking_status_idx"),
            models.Index(fields=["vehicle_number_plate"], name="bk_plate_idx"),
            models.Index(fields=["status", "start_at"], name="bk_status_start_idx"),
        ]
        constraints = [models.CheckConstraint(condition=models.Q(end_at__gt=models.F("start_at")), name="bk_end_after_start")]

    def __str__(self):
        return f"Booking #{self.pk} {self.vehicle_number_plate} @ {self.parking_id}"

    @property
    def status_css(self):
        return {
            "PENDING_PAYMENT": "warning", "CONFIRMED": "primary", "CHECKED_IN": "info", "COMPLETED": "success",
            "CANCELLED": "secondary", "NO_SHOW": "danger", "REFUND_REQUESTED": "warning", "EXPIRED": "secondary",
        }[self.status]

    @property
    def hold_active(self):
        return (self.status == self.Status.PENDING_PAYMENT and self.payment_hold_expires_at is not None
                and self.payment_hold_expires_at > timezone.now())

    @property
    def hold_seconds_left(self):
        if not self.hold_active:
            return 0
        return max(0, int((self.payment_hold_expires_at - timezone.now()).total_seconds()))

    @property
    def cancel_deadline(self):
        return self.start_at - timedelta(minutes=self.cancel_window_minutes)

    @property
    def can_request_refund(self):
        return (self.status == self.Status.CONFIRMED and self.allow_cancellation
                and timezone.now() <= self.cancel_deadline)

    @property
    def duration_minutes(self):
        return int((self.end_at - self.start_at) / timedelta(minutes=1))


class DriverVerdict(models.Model):
    """The owner's verdict on a driver, recorded when checkout is completed."""

    booking = models.OneToOneField(Booking, on_delete=models.CASCADE, related_name="verdict")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="verdicts_given")
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="verdicts_received")
    satisfied = models.BooleanField()
    comment = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
