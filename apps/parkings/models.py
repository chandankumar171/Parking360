from datetime import time
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.core.choices import VehicleCategory
from apps.core.validators import validate_image_size

MAX_PHOTOS = 5

AMENITIES = [  # (field, label, icon)
    ("has_cctv", "CCTV", "bi-camera-video"),
    ("is_covered", "Covered", "bi-cloud-haze2"),
    ("has_security_guard", "Security guard", "bi-shield-check"),
    ("has_ev_charging", "EV charging", "bi-ev-station"),
    ("has_washroom", "Washroom", "bi-badge-wc"),
    ("has_lighting", "Well lit", "bi-lightbulb"),
    ("is_gated", "Gated", "bi-door-closed"),
    ("has_valet", "Valet", "bi-person-workspace"),
    ("wheelchair_accessible", "Wheelchair accessible", "bi-universal-access"),
]

SLOT_CHOICES = [
    (30, "30 minutes"), (60, "1 hour"), (120, "2 hours"), (180, "3 hours"), (240, "4 hours"),
    (360, "6 hours"), (480, "8 hours"), (720, "12 hours"), (1440, "24 hours"),
]


class Parking(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PENDING = "PENDING", "Awaiting verification"
        APPROVED = "APPROVED", "Live"
        REJECTED = "REJECTED", "Rejected"
        SUSPENDED = "SUSPENDED", "Suspended"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="parkings")
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=90, unique=True, blank=True)
    description = models.TextField(max_length=2000)

    address_line = models.CharField("Street address", max_length=255)
    area = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=6, validators=[RegexValidator(r"^\d{6}$", "Enter a 6-digit pincode.")])
    google_maps_url = models.URLField("Google Maps link", max_length=500)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    contact_phone = models.CharField(max_length=10, blank=True)

    is_open = models.BooleanField("Accepting bookings", default=True)
    is_24x7 = models.BooleanField("Open 24 hours", default=True)
    opening_time = models.TimeField(null=True, blank=True)
    closing_time = models.TimeField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    rejection_reason = models.CharField(max_length=500, blank=True)
    is_verified_badge = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    submitted_at = models.DateTimeField(null=True, blank=True)

    allow_cancellation = models.BooleanField("Allow cancellation and refunds", default=False)
    cancel_window_minutes = models.PositiveIntegerField(
        "Cancel until (minutes before start)", default=60, validators=[MaxValueValidator(10080)]
    )
    cancellation_charge_percent = models.DecimalField(
        "Cancellation charge (%)", max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    grace_minutes = models.PositiveIntegerField(
        "No-show grace (minutes)", default=15, validators=[MaxValueValidator(240)]
    )

    has_cctv = models.BooleanField("CCTV", default=False)
    is_covered = models.BooleanField("Covered", default=False)
    has_security_guard = models.BooleanField("Security guard", default=False)
    has_ev_charging = models.BooleanField("EV charging", default=False)
    has_washroom = models.BooleanField("Washroom", default=False)
    has_lighting = models.BooleanField("Well lit", default=False)
    is_gated = models.BooleanField("Gated", default=False)
    has_valet = models.BooleanField("Valet", default=False)
    wheelchair_accessible = models.BooleanField("Wheelchair accessible", default=False)
    rules_text = models.TextField("Parking rules", blank=True, max_length=1500)

    avg_rating = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0"))
    review_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "city"]),
            models.Index(fields=["status", "area"]),
            models.Index(fields=["owner", "status"]),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        creating = not self.slug
        if creating:
            import uuid

            self.slug = uuid.uuid4().hex[:24]
        super().save(*args, **kwargs)
        if creating:
            self.slug = f"{slugify(self.name)[:70] or 'parking'}-{self.pk}"
            type(self).objects.filter(pk=self.pk).update(slug=self.slug)

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("parkings:detail", args=[self.slug])

    # ---- display helpers
    @property
    def status_css(self):
        return {"DRAFT": "secondary", "PENDING": "warning", "APPROVED": "success",
                "REJECTED": "danger", "SUSPENDED": "dark"}[self.status]

    @property
    def full_address(self):
        return ", ".join(p for p in [self.address_line, self.area, self.city, self.state, self.pincode] if p)

    @property
    def amenity_list(self):
        return [(icon, label) for field, label, icon in AMENITIES if getattr(self, field)]

    @property
    def cover_photo(self):
        photos = list(self.photos.all())  # uses prefetch_related cache when present
        return next((p for p in photos if p.is_cover), photos[0] if photos else None)

    @property
    def starting_price(self):
        prices = [t.price_per_slot for t in self.vehicle_types.all() if t.is_active]
        return min(prices) if prices else None

    @property
    def opening_hours_text(self):
        if self.is_24x7 or not (self.opening_time and self.closing_time):
            return "Open 24 hours"
        return f"{self.opening_time:%I:%M %p} to {self.closing_time:%I:%M %p}"

    def is_open_now(self, at=None):
        if self.status != self.Status.APPROVED or not self.is_open:
            return False
        if self.is_24x7 or not (self.opening_time and self.closing_time):
            return True
        now = (at or timezone.localtime()).time()
        o, c = self.opening_time, self.closing_time
        return (o <= now < c) if o < c else (now >= o or now < c)

    @property
    def can_edit(self):
        return self.status != self.Status.SUSPENDED

    @property
    def can_submit(self):
        return self.status in (self.Status.DRAFT, self.Status.REJECTED)

    def has_active_bookings(self):
        """True when future/active bookings exist. Becomes meaningful once the bookings app exists."""
        rel = getattr(self, "bookings", None)
        if rel is None:
            return False
        return rel.filter(status__in=["PENDING_PAYMENT", "CONFIRMED", "CHECKED_IN", "REFUND_REQUESTED"]).exists()

    def blocking_issues(self):
        issues = []
        if not self.vehicle_types.filter(is_active=True).exists():
            issues.append("Add at least one active vehicle type with a price.")
        if not self.photos.exists():
            issues.append("Upload at least one photo of the parking area.")
        return issues

    def warnings(self):
        warns = []
        if self.latitude is None or self.longitude is None:
            warns.append("No map coordinates. Drivers can still open your Google Maps link, but 'near me' sorting will skip this parking.")
        if not self.has_cctv and not self.has_security_guard:
            warns.append("Parkings with CCTV or a guard get more bookings. Update amenities if you have them.")
        return warns


class ParkingVehicleType(models.Model):
    parking = models.ForeignKey(Parking, on_delete=models.CASCADE, related_name="vehicle_types")
    category = models.CharField("Vehicle type", max_length=14, choices=VehicleCategory.choices)
    label = models.CharField("Custom name (optional)", max_length=40, blank=True, help_text="For example: SUV or Mini bus.")
    capacity = models.PositiveIntegerField(
        "Number of bays", validators=[MinValueValidator(1), MaxValueValidator(1000)],
        help_text="How many of these vehicles can park at the same time.",
    )
    slot_minutes = models.PositiveSmallIntegerField("Slot length", choices=SLOT_CHOICES, default=60)
    price_per_slot = models.DecimalField(
        "Price per slot (₹)", max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal("1"))]
    )
    min_slots = models.PositiveSmallIntegerField(
        "Minimum slots", default=1, validators=[MinValueValidator(1), MaxValueValidator(200)]
    )
    max_slots = models.PositiveSmallIntegerField(
        "Maximum slots", default=24, validators=[MinValueValidator(1), MaxValueValidator(200)]
    )
    overtime_free_minutes = models.PositiveSmallIntegerField(
        "Free minutes after end time", default=0, validators=[MaxValueValidator(240)],
        help_text="Overtime is not charged until the driver is this many minutes late.",
    )
    overtime_block_minutes = models.PositiveSmallIntegerField(
        "Overtime block (minutes)", default=30, validators=[MinValueValidator(1), MaxValueValidator(1440)]
    )
    overtime_charge = models.DecimalField(
        "Overtime charge per block (₹)", max_digits=8, decimal_places=2,
        validators=[MinValueValidator(Decimal("1"))],
        help_text="Example: block 1 minute + charge ₹6, or block 30 minutes + charge ₹100.",
    )
    is_active = models.BooleanField("Accepting this vehicle type", default=True)

    class Meta:
        ordering = ["category", "id"]
        constraints = [
            models.UniqueConstraint(fields=["parking", "category"], name="uniq_parking_vehicle_category"),
            models.CheckConstraint(condition=models.Q(min_slots__lte=models.F("max_slots")), name="min_slots_lte_max_slots"),
        ]

    def __str__(self):
        return f"{self.display_name} @ {self.parking}"

    @property
    def display_name(self):
        return self.label or self.get_category_display()

    def duration_minutes(self, slots):
        return self.slot_minutes * slots

    def total_price(self, slots):
        return self.price_per_slot * slots

    def overtime_cost(self, extra_minutes):
        """Extra time is billed in whole blocks (rounded up) after the free minutes.

        Example: block 30 min, charge ₹100, free 0 -> 45 min over = 2 blocks = ₹200.
        """
        billable = max(0, int(extra_minutes) - self.overtime_free_minutes)
        if billable == 0:
            return Decimal("0")
        blocks = -(-billable // self.overtime_block_minutes)
        return self.overtime_charge * blocks

    @property
    def overtime_text(self):
        unit = "1 minute" if self.overtime_block_minutes == 1 else f"{self.overtime_block_minutes} minutes"
        free = f" after {self.overtime_free_minutes} free minutes" if self.overtime_free_minutes else ""
        return f"₹{self.overtime_charge:g} per {unit}{free}"


class ParkingPhoto(models.Model):
    parking = models.ForeignKey(Parking, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to="parkings/", validators=[validate_image_size])
    caption = models.CharField(max_length=120, blank=True)
    is_cover = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_cover", "order", "id"]
