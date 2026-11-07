from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.choices import VehicleCategory
from apps.core.validators import validate_image_size, validate_phone, validate_plate


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", User.Role.SUPERADMIN)
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        SUPERADMIN = "SUPERADMIN", "Super admin"
        OWNER = "OWNER", "Owner"
        DRIVER = "DRIVER", "Driver"

    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=10, unique=True, null=True, blank=True, validators=[validate_phone])
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.DRIVER, db_index=True)
    avatar = models.ImageField(upload_to="avatars/", blank=True, validators=[validate_image_size])
    points = models.IntegerField(default=100, help_text="Driver reliability points.")
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["-date_joined"]
        indexes = [models.Index(fields=["role", "date_joined"], name="user_role_joined_idx")]

    def __str__(self):
        return self.email

    def save(self, *args, **kwargs):
        self.email = (self.email or "").lower()
        if not self.phone:
            self.phone = None
        super().save(*args, **kwargs)

    # ---- convenience
    @property
    def is_superadmin(self):
        return self.role == self.Role.SUPERADMIN

    @property
    def is_owner(self):
        return self.role == self.Role.OWNER

    @property
    def is_driver(self):
        return self.role == self.Role.DRIVER

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email

    @property
    def initials(self):
        parts = [p for p in self.full_name.split() if p]
        return ("".join(p[0] for p in parts[:2]) or self.email[:1]).upper()

    @property
    def avatar_url(self):
        try:
            return self.avatar.url if self.avatar else ""
        except Exception:
            return ""

    @property
    def points_tier(self):
        if self.points >= 100:
            return {"label": "Excellent", "css": "success"}
        if self.points >= 70:
            return {"label": "Good", "css": "primary"}
        if self.points >= 40:
            return {"label": "Fair", "css": "warning"}
        return {"label": "Risky", "css": "danger"}


class OwnerProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="owner_profile")
    business_name = models.CharField(max_length=150)
    address = models.CharField(max_length=255, blank=True)
    about = models.TextField(blank=True, max_length=1000)
    is_public = models.BooleanField(default=True)

    def __str__(self):
        return self.business_name


class DriverVehicle(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="vehicles")
    category = models.CharField(max_length=14, choices=VehicleCategory.choices)
    plate_number = models.CharField(max_length=15, validators=[validate_plate])
    brand = models.CharField(max_length=60)
    model = models.CharField(max_length=60)
    color = models.CharField(max_length=30, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "plate_number"], name="uniq_vehicle_per_user")]

    def __str__(self):
        return f"{self.plate_number} ({self.brand} {self.model})"


class PointLog(models.Model):
    class Reason(models.TextChoices):
        START = "START", "Welcome points"
        SATISFIED = "SATISFIED", "Satisfied checkout"
        UNSATISFIED = "UNSATISFIED", "Unsatisfied checkout"
        NO_SHOW = "NO_SHOW", "No-show"
        LATE_CANCEL = "LATE_CANCEL", "Late cancellation"
        NO_CHECKOUT_VERIFY = "NO_CHECKOUT_VERIFY", "Checkout not verified"
        ADJUSTMENT = "ADJUSTMENT", "Manual adjustment"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="point_logs")
    delta = models.IntegerField()
    balance_after = models.IntegerField()
    reason = models.CharField(max_length=24, choices=Reason.choices)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "-created_at"])]
