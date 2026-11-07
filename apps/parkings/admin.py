from django.contrib import admin

from .models import Parking, ParkingPhoto, ParkingVehicleType


class VehicleTypeInline(admin.TabularInline):
    model = ParkingVehicleType
    extra = 0


class PhotoInline(admin.TabularInline):
    model = ParkingPhoto
    extra = 0


@admin.register(Parking)
class ParkingAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "city", "status", "is_verified_badge", "is_open", "created_at")
    list_filter = ("status", "is_verified_badge", "city")
    search_fields = ("name", "owner__email", "city", "area")
    inlines = [VehicleTypeInline, PhotoInline]
